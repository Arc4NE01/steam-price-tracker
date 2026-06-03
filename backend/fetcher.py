import asyncio
import re
import html
import httpx
from datetime import datetime, timezone
import db

_STORE_PAGE     = "https://store.steampowered.com/app/{}/"
_RESOLVE_BUNDLE = "https://store.steampowered.com/actions/ajaxresolvebundles"
# Cookie to bypass the age gate on mature-content store pages while scraping bundles
_AGE_COOKIE = {"Cookie": "birthtime=0; mature_content=1; wants_mature_content=1; lastagecheckage=1-0-1990"}

REGIONS = ["us", "cn", "ua", "tr", "ar", "in", "gb", "br"]

REGION_CURRENCY = {
    "us": "USD", "cn": "CNY", "ua": "UAH", "tr": "TRY",
    "ar": "ARS", "in": "INR", "gb": "GBP", "br": "BRL",
}

FX_CURRENCIES = ["CNY", "UAH", "TRY", "ARS", "INR", "GBP", "BRL"]

FALLBACK_FX = {
    "USD": 1.0,    "CNY": 0.138,  "UAH": 0.024,
    "TRY": 0.030,  "ARS": 0.0011, "INR": 0.012,
    "GBP": 1.27,   "BRL": 0.20,
}

sync_status = {"is_syncing": False, "done": 0, "total": 0, "phase": "", "last_error": ""}

_HEADERS    = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_APPDETAILS = "https://store.steampowered.com/api/appdetails"

# Delay between API calls.
# 0.5 s = 2 req/sec = 120 req/min — safely under Steam's ~200 req/min limit.
_DELAY = 0.5


def parse_steam_input(raw: str) -> tuple:
    s = raw.strip().rstrip("/")
    s = re.sub(r"^https?://(www\.)?steamcommunity\.com/", "", s)
    if s.startswith("profiles/"): return ("profiles", s[9:])
    if s.startswith("id/"):       return ("id", s[3:])
    if re.match(r"^\d{17}$", s):  return ("profiles", s)
    return ("id", s)


async def resolve_vanity_to_steam64(vanity: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS, follow_redirects=True) as c:
            r = await c.get(f"https://steamcommunity.com/id/{vanity}/?xml=1")
        m = re.search(r"<steamID64>(\d+)</steamID64>", r.text, re.IGNORECASE)
        if m: print(f"[Resolve] {vanity} → {m.group(1)}"); return m.group(1)
        m = re.search(r'"steamid"\s*:\s*"(\d+)"', r.text)
        if m: print(f"[Resolve] {vanity} → {m.group(1)} (HTML)"); return m.group(1)
    except Exception as e:
        print(f"[Resolve] {e}")
    return None


async def fetch_fx_rates() -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://open.er-api.com/v6/latest/USD")
            data = r.json()
        if data.get("result") == "success":
            now = datetime.now(timezone.utc).isoformat()
            await db.upsert_fx_rate("USD", 1.0, now)
            for cur in FX_CURRENCIES:
                rate = data["rates"].get(cur)
                if rate and rate > 0:
                    await db.upsert_fx_rate(cur, 1.0 / rate, now)
            return True
    except Exception as e:
        print(f"[FX] {e}")
    return False


async def fetch_wishlist_ids(steam64: str) -> list:
    try:
        async with httpx.AsyncClient(timeout=30, headers=_HEADERS) as c:
            r = await c.get(
                "https://api.steampowered.com/IWishlistService/GetWishlist/v1/",
                params={"steamid": steam64},
            )
        print(f"[Wishlist] HTTP {r.status_code} — {len(r.text)} bytes")
        items = r.json().get("response", {}).get("items", [])
        if not items:
            sync_status["last_error"] = (
                "Wishlist is empty or set to Private. "
                "In Steam: View Profile → Edit Profile → Privacy Settings → "
                "Game details → Public. Then click Retry."
            )
            return []
        ids = [int(i["appid"]) for i in items if "appid" in i]
        print(f"[Wishlist] {len(ids)} games")
        return ids
    except Exception as e:
        sync_status["last_error"] = f"Wishlist fetch failed: {e}"
        return []


# Sentinel for "confirmed not purchasable in this region" — stored so the
# pair counts as resolved and is NOT retried. Filtered out of API responses.
NA_PRICE = -1

# _appdetails outcomes
OK          = "ok"           # got app_data dict
UNAVAILABLE = "unavailable"  # Steam confirms no store data here (data == [])
FAIL        = "fail"         # transient: null body, success:false, rate-limit, error


async def _appdetails(client: httpx.AsyncClient, app_id: int,
                      cc: str, filters: str | None):
    """
    Single appdetails call. Returns (status, app_data):
      (OK, dict)          — success, data present
      (UNAVAILABLE, None) — success but data is empty (game not sold in region)
      (FAIL, None)        — null body / success:false / rate-limited / error → retry

    filters=None fetches the full response (needed for package_groups/editions).
    """
    try:
        params = {"appids": app_id, "cc": cc, "l": "english"}
        if filters:
            params["filters"] = filters
        r = await client.get(_APPDETAILS, params=params)
        # HTTP 429 = explicit rate limit
        if r.status_code == 429:
            return (FAIL, None)
        data = r.json()
        # Steam returns literal `null` (whole body) when rate-limited
        if not isinstance(data, dict):
            return (FAIL, None)
        entry = data.get(str(app_id))
        if not isinstance(entry, dict):
            return (FAIL, None)
        if entry.get("success") is not True:
            # success:false is ambiguous (bad id OR rate-limit) — retry to be safe
            return (FAIL, None)
        app_data = entry.get("data")
        if isinstance(app_data, dict):
            return (OK, app_data)
        # success:true but data == [] (or other) → genuinely no store entry here
        return (UNAVAILABLE, None)
    except Exception as e:
        print(f"[API] {app_id}/{cc}/{filters}: {e}")
        return (FAIL, None)


async def _fetch_name(app_id: int, client: httpx.AsyncClient, now: str) -> bool:
    """Fetch and store the game name + release info from the US store."""
    status, app_data = await _appdetails(client, app_id, "us", "basic,release_date")
    if status == OK and app_data.get("name"):
        rel = app_data.get("release_date") or {}
        is_upcoming  = 1 if rel.get("coming_soon") else 0
        release_date = (rel.get("date") or "").strip()
        await db.upsert_game(
            app_id,
            app_data["name"],
            app_data.get(
                "header_image",
                f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg",
            ),
            now,
            is_upcoming=is_upcoming,
            release_date=release_date,
        )
        return True
    return False


async def _fetch_price(app_id: int, region: str,
                       client: httpx.AsyncClient, fx: dict, now: str) -> str:
    """
    Fetch and store one price record.
    Returns OK if resolved (price/free/sentinel stored), FAIL if transient (retry).
    """
    status, app_data = await _appdetails(client, app_id, region, "price_overview")

    if status == FAIL:
        return FAIL

    if status == UNAVAILABLE:
        # Confirmed not sold here — store sentinel so we stop retrying
        cur = REGION_CURRENCY.get(region, "USD")
        await db.upsert_price(app_id, region, cur, NA_PRICE, NA_PRICE, 0, now)
        return OK

    # status == OK
    po = app_data.get("price_overview")
    if po:
        raw  = po.get("final", 0)
        cur  = po.get("currency", REGION_CURRENCY.get(region, "USD"))
        disc = po.get("discount_percent", 0)
        rate = fx.get(cur, FALLBACK_FX.get(cur, 1.0))
        usd  = (raw / 100.0) * rate if raw > 0 else 0.0
        await db.upsert_price(app_id, region, cur, raw, usd, disc, now)
    elif app_data.get("is_free"):
        cur = REGION_CURRENCY.get(region, "USD")
        await db.upsert_price(app_id, region, cur, 0, 0.0, 0, now)
    else:
        # Released/announced but no price yet (e.g. coming soon) — sentinel, no retry
        cur = REGION_CURRENCY.get(region, "USD")
        await db.upsert_price(app_id, region, cur, NA_PRICE, NA_PRICE, 0, now)
    return OK


async def fetch_all_prices(app_ids: list, new_ids: set | None = None):
    """
    Fetch names + prices for app_ids.

    new_ids: app_ids added to the wishlist since the last sync.
             If None, all stub games get a name refresh.
             On incremental syncs only new_ids get name-fetched; all get price-fetched.
    """
    fx = await db.get_fx_rates()
    if not fx:
        await fetch_fx_rates()
        fx = await db.get_fx_rates()
    if not fx:
        fx = FALLBACK_FX
        print("[FX] Using fallback rates")

    now = datetime.now(timezone.utc).isoformat()

    # Which games need a name/metadata fetch?
    if new_ids is not None:
        # Incremental: only newly-added games
        needs_name = list(new_ids)
    else:
        # Full sync: refresh metadata for ALL games so names, release dates and
        # upcoming→released transitions stay current (+~45s, runs in background).
        needs_name = list(app_ids)

    n_names  = len(needs_name)
    n_prices = len(app_ids) * len(REGIONS)
    sync_status["total"] = n_names + n_prices
    sync_status["done"]  = 0

    async with httpx.AsyncClient(timeout=12.0, headers=_HEADERS) as client:

        # ── Phase 1: game names (only for games that need them) ──────────
        if needs_name:
            sync_status["phase"] = (
                f"Fetching names for {n_names} game{'s' if n_names != 1 else ''}..."
            )
            for app_id in needs_name:
                await _fetch_name(app_id, client, now)
                sync_status["done"] += 1
                await asyncio.sleep(_DELAY)

        # ── Phase 2: prices for ALL wishlist games ───────────────────────
        # Always re-fetch all prices so sales and changes are captured.
        sync_status["phase"] = f"Fetching prices for {len(app_ids)} games..."
        all_pairs = [(a, r) for a in app_ids for r in REGIONS]
        await _fetch_pairs_with_backoff(all_pairs, client, fx, now, count_progress=True)

        # ── Phase 3: retry passes for pairs that failed transiently ──────
        # A pair has no row only if its fetch failed (rate-limit/network).
        # Confirmed-N/A pairs got a sentinel row, so they're excluded here.
        for attempt in range(1, 4):
            resolved = await db.get_resolved_pairs()
            missing  = [p for p in all_pairs if p not in resolved]
            if not missing:
                break
            print(f"[Retry] pass {attempt}: {len(missing)} unresolved prices")
            sync_status["phase"] = (
                f"Retrying {len(missing)} prices (pass {attempt})..."
            )
            # Let Steam's rate limiter cool down before hammering again
            await asyncio.sleep(15)
            await _fetch_pairs_with_backoff(
                missing, client, fx, now, count_progress=False, delay=1.2
            )

        # ── Phase 4: editions + bundles (full sync only) ─────────────────
        if new_ids is None:
            try:
                await populate_variants(app_ids, fx, now, client)
                await db.delete_stale_variants(now)   # drop vanished editions/bundles
            except Exception as e:
                print(f"[Variants] error: {e}")


async def _fetch_pairs_with_backoff(pairs, client, fx, now,
                                    count_progress: bool, delay: float = None):
    """
    Fetch a list of (app_id, region) pairs sequentially with adaptive backoff.
    If Steam starts rate-limiting (many consecutive FAILs), pause to recover.
    """
    if delay is None:
        delay = _DELAY
    consecutive_fail = 0
    for app_id, region in pairs:
        result = await _fetch_price(app_id, region, client, fx, now)
        if count_progress:
            sync_status["done"] += 1

        if result == FAIL:
            consecutive_fail += 1
            # Back off hard when Steam is clearly throttling us
            if consecutive_fail >= 6:
                cooldown = min(30, 5 * (consecutive_fail - 5))
                print(f"[Backoff] {consecutive_fail} fails in a row — pausing {cooldown}s")
                await asyncio.sleep(cooldown)
        else:
            consecutive_fail = 0

        await asyncio.sleep(delay)


async def run_wishlist_check(steam_input: str) -> int:
    """
    Startup fast-path: detect ONLY games newly added to the wishlist since the
    last sync. Never re-fetches existing games — that is the job of the 6-hour
    full sync. Returns the number of new games found.
    """
    if sync_status["is_syncing"]:
        return 0
    sync_status["is_syncing"] = True
    sync_status["last_error"] = ""
    try:
        sync_status["phase"] = "Checking for new wishlist games..."

        path_type, value = parse_steam_input(steam_input)
        steam64 = value if path_type == "profiles" else await resolve_vanity_to_steam64(value)
        if not steam64:
            return 0

        wishlist_ids = await fetch_wishlist_ids(steam64)
        if not wishlist_ids:
            return 0

        known_ids = await db.get_known_app_ids()
        added_ids = set(wishlist_ids) - known_ids   # only genuinely new

        # Games that already exist but lack release-date info (e.g. after the
        # release_date feature was added) — backfill metadata only, no prices.
        backfill_ids = [a for a in await db.get_games_missing_release()
                        if a not in added_ids]

        if not added_ids and not backfill_ids:
            print("[WishlistCheck] Nothing new or to backfill — skipping")
            return 0

        print(f"[WishlistCheck] {len(added_ids)} new + "
              f"{len(backfill_ids)} metadata backfill")

        now = datetime.now(timezone.utc).isoformat()
        for app_id in added_ids:
            fallback = f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg"
            await db.upsert_game(app_id, f"App {app_id}", fallback, now)

        fx = await db.get_fx_rates() or FALLBACK_FX
        sync_status["total"] = (len(added_ids) * (1 + len(REGIONS))) + len(backfill_ids)
        sync_status["done"]  = 0
        sync_status["phase"] = "Updating release info..." if not added_ids \
            else f"Fetching {len(added_ids)} new game(s)..."

        async with httpx.AsyncClient(timeout=12.0, headers=_HEADERS) as client:
            # New games: full name + prices
            for app_id in added_ids:
                await _fetch_name(app_id, client, now)
                sync_status["done"] += 1
                await asyncio.sleep(_DELAY)
                for region in REGIONS:
                    await _fetch_price(app_id, region, client, fx, now)
                    sync_status["done"] += 1
                    await asyncio.sleep(_DELAY)

            # Backfill: metadata (name + release date) only — fast
            for app_id in backfill_ids:
                await _fetch_name(app_id, client, now)
                sync_status["done"] += 1
                await asyncio.sleep(_DELAY)

        print(f"[WishlistCheck] Done — {len(added_ids)} new, "
              f"{len(backfill_ids)} backfilled")
        return len(added_ids) + len(backfill_ids)
    finally:
        sync_status["is_syncing"] = False
        sync_status["phase"]      = ""


# ── Variants: editions (package_groups) + bundles ───────────────────────
def _clean_pkg_name(option_text: str) -> str:
    """Extract the edition name from Steam's option_text HTML (drops the price tail)."""
    s = re.sub(r"<[^>]+>", "", option_text or "")
    s = html.unescape(s).strip()
    parts = s.rsplit(" - ", 1)
    if len(parts) == 2 and re.search(r"\d", parts[1]):
        s = parts[0].strip()
    return s


def _parse_money_cents(formatted: str):
    """
    Parse a localized Steam price string into integer cents.
    Handles '¥ 1,780.65', '₴ 1 599,00' (space thousands, comma decimal),
    '₹ 2,999' (no decimals), 'R$ 199,90', etc.
    """
    if not formatted:
        return None
    s = re.sub(r"[^\d.,\s]", "", formatted)        # keep digits + separators
    s = re.sub(r"\s", "", s)                        # drop space thousands sep
    if not s:
        return None
    # A trailing separator followed by exactly 2 digits = decimal part
    m = re.search(r"[.,](\d{2})$", s)
    if m:
        cents = re.sub(r"[.,]", "", s[:-3]) + s[-2:]
    else:
        cents = re.sub(r"[.,]", "", s) + "00"       # whole number, no minor unit
    return int(cents) if cents.isdigit() else None


def _extract_editions(app_data: dict) -> list:
    """Return a list of edition dicts from package_groups, or [] if only a base package."""
    subs = []
    for g in app_data.get("package_groups") or []:
        for s in g.get("subs") or []:
            subs.append(s)
    if len(subs) < 2:
        return []   # single package == base game, no dropdown needed
    editions = []
    for i, s in enumerate(subs):
        raw = s.get("price_in_cents_with_discount")
        if raw is None:
            continue
        dm = re.search(r"(\d+)", s.get("percent_savings_text") or "")
        editions.append({
            # Key by index, not packageid — Steam uses different package IDs
            # per region for the same edition, which would split the grouping.
            "variant_id": f"ed:{i}",
            "name": _clean_pkg_name(s.get("option_text")),
            "raw": int(raw),
            "disc": int(dm.group(1)) if dm else 0,
            "sort": i,
        })
    return editions


def _detect_currency(formatted: str, region: str) -> str:
    """Detect the real currency from a formatted price (regions can fall back to USD)."""
    s = formatted or ""
    if "USD" in s: return "USD"
    if "ARS" in s: return "ARS"
    if "R$"  in s: return "BRL"
    if "₴"   in s: return "UAH"
    if "¥"   in s: return "CNY"
    if "₺"   in s: return "TRY"
    if "₹"   in s: return "INR"
    if "£"   in s: return "GBP"
    return REGION_CURRENCY.get(region, "USD")


async def _scrape_bundle_ids(app_id: int, client: httpx.AsyncClient) -> list:
    try:
        r = await client.get(_STORE_PAGE.format(app_id), headers={**_HEADERS, **_AGE_COOKIE})
        ids = set(re.findall(r'data-ds-bundleid="(\d+)"', r.text))
        return [int(x) for x in ids]
    except Exception as e:
        print(f"[Bundle] scrape {app_id}: {e}")
        return []


async def _resolve_bundles(bundle_ids: list, region: str, client: httpx.AsyncClient) -> dict:
    """Batch-resolve bundle prices for one region → {bundleid: data}."""
    if not bundle_ids:
        return {}
    try:
        r = await client.get(_RESOLVE_BUNDLE, params={
            "bundleids": ",".join(str(b) for b in bundle_ids),
            "cc": region, "l": "english",
        })
        data = r.json()
        return {b["bundleid"]: b for b in data if isinstance(b, dict) and "bundleid" in b}
    except Exception as e:
        print(f"[Bundle] resolve {region}: {e}")
        return {}


async def populate_variants(app_ids: list, fx: dict, now: str, client: httpx.AsyncClient):
    """
    Fetch editions + bundles for all games and store them. Runs after base prices
    so each game's lowest region is known. Best-effort: failures are non-fatal.
    """
    lowest = await db.get_lowest_regions()   # {app_id: region}

    # ── Editions ────────────────────────────────────────────────────────
    sync_status["phase"] = "Checking game editions..."
    for app_id in app_ids:
        # Display regions: China, Ukraine, and the game's cheapest region
        regions = []
        for r in ("cn", "ua", lowest.get(app_id, "us")):
            if r and r not in regions:
                regions.append(r)
        # Probe the cheapest region first; if no editions, skip the rest (saves calls)
        probe = lowest.get(app_id) or regions[0]
        ordered = [probe] + [r for r in regions if r != probe]

        editions_found = False
        for idx, region in enumerate(ordered):
            status, app_data = await _appdetails(client, app_id, region, None)
            await asyncio.sleep(_DELAY)
            if status != OK:
                continue
            editions = _extract_editions(app_data)
            if not editions:
                break  # single package — no dropdown for this game
            editions_found = True
            # Use the region's ACTUAL currency from price_overview (some regions
            # fall back to USD), not the assumed regional currency.
            po = app_data.get("price_overview") or {}
            cur  = po.get("currency") or REGION_CURRENCY.get(region, "USD")
            rate = fx.get(cur, FALLBACK_FX.get(cur, 1.0))
            for e in editions:
                usd = (e["raw"] / 100.0) * rate if e["raw"] > 0 else 0.0
                await db.upsert_variant(
                    app_id, e["variant_id"], "edition", e["name"], region,
                    e["raw"], usd, cur, e["disc"], "", e["sort"], now,
                )
        # (probe had no editions → loop already broke after first iteration)

    # ── Bundles ─────────────────────────────────────────────────────────
    sync_status["phase"] = "Checking bundles..."
    app_bundles = {}        # app_id -> [bundle_id]
    all_bundle_ids = set()
    for app_id in app_ids:
        bids = await _scrape_bundle_ids(app_id, client)
        await asyncio.sleep(_DELAY)
        if bids:
            app_bundles[app_id] = bids
            all_bundle_ids.update(bids)

    if all_bundle_ids:
        # Resolve every bundle in every region (batched → 1 call per region)
        resolved = {}       # region -> {bundleid: data}
        for region in REGIONS:
            resolved[region] = await _resolve_bundles(list(all_bundle_ids), region, client)
            await asyncio.sleep(_DELAY)

        for app_id, bids in app_bundles.items():
            for bid in bids:
                for region in REGIONS:
                    b = resolved.get(region, {}).get(bid)
                    if not b:
                        continue
                    fmt = b.get("formatted_final_price")
                    raw = _parse_money_cents(fmt)
                    if raw is None:
                        continue
                    cur  = _detect_currency(fmt, region)   # may be USD fallback
                    rate = fx.get(cur, FALLBACK_FX.get(cur, 1.0))
                    usd  = (raw / 100.0) * rate if raw > 0 else 0.0
                    await db.upsert_variant(
                        app_id, f"bundle:{bid}", "bundle", b.get("name", "Bundle"),
                        region, raw, usd, cur, int(b.get("discount_percent", 0) or 0),
                        b.get("header_image_url", ""), 0, now,
                    )


async def run_full_sync(steam_input: str) -> bool:
    sync_status["is_syncing"] = True
    sync_status["last_error"] = ""
    try:
        sync_status["phase"] = "Fetching exchange rates..."
        await fetch_fx_rates()

        sync_status["phase"] = "Resolving Steam profile..."
        path_type, value = parse_steam_input(steam_input)
        if path_type == "id":
            steam64 = await resolve_vanity_to_steam64(value)
            if not steam64:
                sync_status["last_error"] = (
                    f"Could not find Steam profile '{value}'. "
                    "Make sure the profile is Public and the URL is correct."
                )
                return False
        else:
            steam64 = value

        sync_status["phase"] = "Fetching wishlist..."
        wishlist_ids = await fetch_wishlist_ids(steam64)
        if not wishlist_ids:
            return False

        known_ids = await db.get_known_app_ids()
        added_ids = set(wishlist_ids) - known_ids   # genuinely new games

        # Pre-insert stubs for new games so they appear immediately
        now = datetime.now(timezone.utc).isoformat()
        for app_id in added_ids:
            fallback = f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg"
            await db.upsert_game(app_id, f"App {app_id}", fallback, now)

        # A full sync always refreshes ALL metadata + prices (new_ids=None) so
        # names, release dates, sales and upcoming→released changes stay current.
        n_req = len(wishlist_ids) + len(wishlist_ids) * len(REGIONS)
        eta   = round(n_req * _DELAY / 60, 1)
        print(f"[Sync] full: {len(wishlist_ids)} names + "
              f"{len(wishlist_ids) * len(REGIONS)} prices = {n_req} requests (~{eta} min)")

        await fetch_all_prices(wishlist_ids, new_ids=None)

        print("[Sync] Complete.")
        return True
    finally:
        sync_status["is_syncing"] = False
        sync_status["phase"]      = ""
