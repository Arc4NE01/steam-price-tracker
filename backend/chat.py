"""
Simple rule-based chat assistant: extracts a game name + regions from a natural
language question, looks the game up on Steam, fetches its prices across regions,
and replies in plain language. No API key required.
"""
import re
import calendar
import asyncio
from datetime import date, datetime

import httpx
import db
import fetcher

# Region code → display name
REGION_NAME = {
    "cn": "China", "ua": "Ukraine", "us": "the US", "tr": "Turkey",
    "ar": "Argentina", "in": "India", "gb": "the UK", "br": "Brazil",
}

# Aliases users might type. Ambiguous English words ("in", "us") are intentionally
# omitted — we require the full country name for those.
REGION_ALIASES = {
    "cn": ["china", "chinese", "cn"],
    "ua": ["ukraine", "ukrainian", "ua"],
    "us": ["usa", "america", "american", "united states"],
    "tr": ["turkey", "turkish", "tr"],
    "ar": ["argentina", "argentinian", "ar"],
    "in": ["india", "indian"],
    "gb": ["uk", "britain", "british", "england", "united kingdom"],
    "br": ["brazil", "brazilian"],
}

CURRENCY_SYMBOL = {
    "CNY": "¥", "UAH": "₴", "USD": "$", "TRY": "₺",
    "ARS": "AR$", "INR": "₹", "GBP": "£", "BRL": "R$",
}

STOPWORDS = {
    "i", "want", "to", "know", "about", "the", "price", "prices", "pricing",
    "cost", "costs", "game", "games", "compare", "comparison", "compared",
    "with", "and", "also", "its", "it", "lowest", "region", "regions", "priced",
    "cheapest", "cheaper", "of", "for", "how", "much", "is", "tell", "me", "show",
    "what", "whats", "please", "steam", "in", "at", "vs", "versus", "between",
    "find", "get", "check", "currency", "dollar", "dollars", "usd", "released",
    "release", "releases", "when", "will", "this", "that", "a", "an", "s", "are",
    "their", "there", "do", "does", "give", "gimme", "look", "up", "on", "now",
}


# ── Release date parsing (shared with main.build_response) ───────────────
def parse_release(release_date: str, fallback_upcoming: bool):
    """Return (is_upcoming, days_until). days_until is None when day unknown."""
    s = (release_date or "").strip()
    if not s:
        return (fallback_upcoming, None)
    today = date.today()

    for fmt in ("%b %d, %Y", "%d %b, %Y", "%b %d %Y", "%d %b %Y",
                "%B %d, %Y", "%d %B, %Y", "%B %d %Y", "%d %B %Y"):
        try:
            d = datetime.strptime(s, fmt).date()
            days = (d - today).days
            return (days > 0, days if days > 0 else None)
        except ValueError:
            pass

    qm = re.search(r"Q([1-4])\s*'?\s*(20\d{2})", s, re.IGNORECASE)
    if qm:
        q, yr = int(qm.group(1)), int(qm.group(2))
        end_month = q * 3
        d = date(yr, end_month, calendar.monthrange(yr, end_month)[1])
        return ((d - today).days > 0, None)

    for fmt in ("%b %Y", "%B %Y"):
        try:
            dt = datetime.strptime(s, fmt)
            d = date(dt.year, dt.month, calendar.monthrange(dt.year, dt.month)[1])
            return ((d - today).days > 0, None)
        except ValueError:
            pass

    ym = re.search(r"(20\d{2})", s)
    if ym:
        yr = int(ym.group(1))
        if yr > today.year:
            return (True, None)
        if yr < today.year:
            return (False, None)
        return (fallback_upcoming, None)

    return (fallback_upcoming, None)


# ── Query parsing ────────────────────────────────────────────────────────
def parse_query(message: str):
    """Return (game_name, [region_codes]) extracted from a free-text question."""
    q = message.lower()

    regions = []
    for code, aliases in REGION_ALIASES.items():
        for a in aliases:
            if re.search(r"\b" + re.escape(a) + r"\b", q):
                if code not in regions:
                    regions.append(code)
                break

    # Strip region words, then punctuation, then stopwords
    for aliases in REGION_ALIASES.values():
        for a in aliases:
            q = re.sub(r"\b" + re.escape(a) + r"\b", " ", q)
    q = re.sub(r"[^a-z0-9 :]", " ", q)
    tokens = [t for t in q.split() if t and t not in STOPWORDS]
    name = " ".join(tokens).strip()
    return name, regions


async def steam_search(name: str, client: httpx.AsyncClient):
    """Return {app_id, name, header_image} for the best match, or None."""
    try:
        r = await client.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": name, "cc": "us", "l": "en"},
        )
        items = r.json().get("items", [])
        if not items:
            return None
        top = items[0]
        app_id = top["id"]
        return {
            "app_id": app_id,
            "name": top.get("name", name),
            "header_image": f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg",
        }
    except Exception as e:
        print(f"[Chat] search error: {e}")
        return None


def _fmt_native(currency: str, raw: int) -> str:
    sym = CURRENCY_SYMBOL.get(currency, currency + " ")
    val = raw / 100.0
    if val == int(val):
        return f"{sym}{int(val):,}"
    return f"{sym}{val:,.2f}"


async def fetch_game_data(app_id: int, client: httpx.AsyncClient, fx: dict):
    """Fetch prices for all regions + release info for one game."""
    prices = {}
    for region in fetcher.REGIONS:
        status, data = await fetcher._appdetails(client, app_id, region, "price_overview")
        if status == fetcher.OK:
            po = data.get("price_overview")
            if po:
                raw = po.get("final", 0)
                cur = po.get("currency", fetcher.REGION_CURRENCY.get(region, "USD"))
                disc = po.get("discount_percent", 0)
                rate = fx.get(cur, fetcher.FALLBACK_FX.get(cur, 1.0))
                prices[region] = {
                    "raw": raw, "currency": cur, "disc": disc,
                    "usd": (raw / 100.0) * rate,
                    "native": _fmt_native(cur, raw),
                }
            elif data.get("is_free"):
                prices[region] = {"raw": 0, "currency": "USD", "disc": 0,
                                  "usd": 0.0, "native": "Free"}
        await asyncio.sleep(0.2)

    release = {"is_upcoming": False, "date": "", "days": None}
    status, data = await fetcher._appdetails(client, app_id, "us", "basic,release_date")
    if status == fetcher.OK:
        rel = data.get("release_date") or {}
        d = (rel.get("date") or "").strip()
        up, days = parse_release(d, bool(rel.get("coming_soon")))
        release = {"is_upcoming": up, "date": d, "days": days}
    return prices, release


def compose_reply(game_name: str, prices: dict, requested: list, release: dict) -> str:
    """Build a natural-language answer from the fetched data."""
    reported = requested[:] if requested else ["cn", "ua"]

    # Release / upcoming preamble
    rel_note = ""
    if release["is_upcoming"]:
        if release["days"] is not None:
            rel_note = (f"🚀 {game_name} is unreleased — it launches on "
                        f"{release['date']} (in {release['days']} days). ")
        elif release["date"]:
            rel_note = f"🚀 {game_name} is unreleased — it launches {release['date']}. "
        else:
            rel_note = f"🚀 {game_name} is unreleased. "

    if not prices:
        return (rel_note +
                f"{game_name} doesn't have a purchasable price on Steam right now.").strip()

    def fmt(p):
        s = f"{p['native']} (~${p['usd']:.2f})"
        if p["disc"] > 0:
            s += f" -{p['disc']}%"
        return s

    priced  = [(r, prices[r]) for r in reported if r in prices and prices[r]["raw"] > 0]
    free    = [r for r in reported if r in prices and prices[r]["raw"] == 0]
    missing = [r for r in reported if r not in prices]

    parts = []
    if not release["is_upcoming"]:
        parts.append(f"**{game_name}**")

    # Reported region prices
    seg = [f"{p['native']} (~${p['usd']:.2f})" + (f" -{p['disc']}%" if p['disc'] > 0 else "")
           + f" in {REGION_NAME[r]}" for r, p in priced]
    seg += [f"free in {REGION_NAME[r]}" for r in free]
    if seg:
        body = ("is " if not release["is_upcoming"] else "Pre-order is ") + seg[0]
        if len(seg) > 1:
            body += ", and " + ", ".join(seg[1:])
        parts.append(body)

    sentence = (" ".join(parts) + ".").strip() if parts else ""

    extra = []

    # Compare the first two reported priced regions
    if len(priced) >= 2:
        (r1, p1), (r2, p2) = priced[0], priced[1]
        diff = abs(p1["usd"] - p2["usd"])
        if diff < 0.01:
            extra.append(f"{REGION_NAME[r1]} and {REGION_NAME[r2]} cost about the same")
        else:
            cheaper, dearer = (r1, r2) if p1["usd"] < p2["usd"] else (r2, r1)
            extra.append(f"{REGION_NAME[cheaper].capitalize()} is ${diff:.2f} cheaper "
                         f"than {REGION_NAME[dearer]}")

    # Lowest across all regions
    all_priced = {r: p for r, p in prices.items() if p["raw"] > 0}
    if all_priced:
        low_r = min(all_priced, key=lambda r: all_priced[r]["usd"])
        low = all_priced[low_r]
        low_txt = f"the cheapest region is {REGION_NAME[low_r]} at {low['native']} (~${low['usd']:.2f})"
        if priced:
            base_r, base = max(priced, key=lambda rp: rp[1]["usd"])
            d = base["usd"] - low["usd"]
            if d > 0.01 and low_r != base_r:
                low_txt += f" — ${d:.2f} below {REGION_NAME[base_r]}"
        extra.append(low_txt)

    reply = (rel_note + sentence).strip()
    if extra:
        reply += " " + ". ".join(s[0].upper() + s[1:] for s in extra) + "."
    if missing:
        reply += " " + " ".join(f"(Not sold in {REGION_NAME[r]}.)" for r in missing)
    return reply.strip()


async def answer_query(message: str) -> dict:
    """Top-level: parse → search → fetch → compose. Returns {reply, game?}."""
    name, regions = parse_query(message)
    if not name or len(name) < 2:
        return {"reply": "Tell me a game name and I'll look up its price — "
                         "e.g. \"Elden Ring price in China and Ukraine\"."}

    fx = await db.get_fx_rates()
    if not fx:
        await fetcher.fetch_fx_rates()
        fx = await db.get_fx_rates() or fetcher.FALLBACK_FX

    async with httpx.AsyncClient(timeout=12.0, headers=fetcher._HEADERS) as client:
        game = await steam_search(name, client)
        if not game:
            return {"reply": f"I couldn't find a game called “{name}” on Steam. "
                             "Try the exact title."}
        prices, release = await fetch_game_data(game["app_id"], client, fx)

    reply = compose_reply(game["name"], prices, regions, release)
    return {"reply": reply, "game": game}
