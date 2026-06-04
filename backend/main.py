import asyncio
import os
import base64
import secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db
import fetcher
import chat
from chat import parse_release
from models import SettingsIn, GameWithPrices, PriceInfo, SyncStatus, ChatIn

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

scheduler = AsyncIOScheduler(timezone="UTC")


from datetime import timezone

# Prices synced more recently than this are considered fresh — a manual refresh
# will skip the slow full re-fetch and just report "up to date".
STALE_HOURS = 18


async def do_sync():
    if fetcher.sync_status["is_syncing"]:
        return
    steam_id = await db.get_setting("steam_id")
    if not steam_id:
        return
    await fetcher.run_full_sync(steam_id)


async def hours_since_last_sync() -> float | None:
    """Hours since the most recent price fetch, or None if never synced."""
    last = await db.get_last_sync()
    if not last:
        return None
    try:
        dt = datetime.fromisoformat(last)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    except Exception:
        return None


async def do_startup_check():
    """On every startup: detect new wishlist games and fix any stubs. Fast (~5s per new game)."""
    steam_id = await db.get_setting("steam_id")
    if not steam_id or fetcher.sync_status["is_syncing"]:
        return
    await fetcher.run_wishlist_check(steam_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()

    scheduler.add_job(do_sync, "interval", hours=24, id="periodic_sync")
    scheduler.add_job(fetcher.fetch_fx_rates, "interval", hours=24, id="fx_sync")
    scheduler.start()

    game_count = await db.get_game_count()
    steam_id   = await db.get_setting("steam_id")
    if steam_id:
        if game_count == 0:
            asyncio.create_task(do_sync())          # first ever run
        else:
            asyncio.create_task(do_startup_check()) # check for new wishlist games

    yield

    scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)

# Optional password protection. Set the APP_PASSWORD environment variable to
# require a login (HTTP Basic Auth) for the whole app. Leave it unset for no auth
# (fine on a trusted home network). Any username works; only the password matters.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()


@app.middleware("http")
async def password_gate(request: Request, call_next):
    if APP_PASSWORD:
        header = request.headers.get("Authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8", "ignore")
                _, _, pw = decoded.partition(":")
                ok = secrets.compare_digest(pw, APP_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Steam Price Tracker"'},
            )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def build_response(raw: list, apps_with_variants: set = frozenset()) -> list[GameWithPrices]:
    result = []
    for item in raw:
        prices: dict[str, PriceInfo] = {}
        for region, p in item["prices"].items():
            # Sentinel rows (price_raw < 0) mark confirmed-unavailable — show as N/A
            if p["price_raw"] < 0:
                continue
            prices[region] = PriceInfo(
                price_raw=p["price_raw"],
                price_usd=p["price_usd"],
                currency=p["currency"],
                discount_pct=p["discount_pct"],
            )

        lowest_region = None
        lowest_usd = None
        for region, p in prices.items():
            if p.price_usd > 0:
                if lowest_usd is None or p.price_usd < lowest_usd:
                    lowest_usd = p.price_usd
                    lowest_region = region

        release_date = item.get("release_date", "") or ""
        is_upcoming, days_until = parse_release(
            release_date, bool(item.get("is_upcoming", 0))
        )

        result.append(
            GameWithPrices(
                app_id=item["app_id"],
                name=item["name"],
                header_image=item["header_image"],
                prices=prices,
                lowest_region=lowest_region,
                lowest_price_usd=lowest_usd,
                is_upcoming=is_upcoming,
                release_date=release_date,
                days_until_release=days_until,
                has_variants=item["app_id"] in apps_with_variants,
            )
        )
    return result


def build_variants(rows: list) -> list:
    """Group raw variant rows (per region) into display objects with cn/ua/lowest."""
    by_id = {}
    for r in rows:
        vid = r["variant_id"]
        v = by_id.setdefault(vid, {
            "variant_id": vid, "kind": r["kind"], "name": r["name"],
            "header_image": r["header_image"], "sort_order": r["sort_order"],
            "prices": {}, "lowest_region": None, "lowest_price_usd": None,
        })
        if r["price_raw"] >= 0:
            v["prices"][r["region"]] = {
                "price_raw": r["price_raw"], "price_usd": r["price_usd"],
                "currency": r["currency"], "discount_pct": r["discount_pct"],
            }
    for v in by_id.values():
        for region, p in v["prices"].items():
            if p["price_usd"] > 0 and (
                v["lowest_price_usd"] is None or p["price_usd"] < v["lowest_price_usd"]
            ):
                v["lowest_price_usd"] = p["price_usd"]
                v["lowest_region"] = region
    # editions first, then bundles; preserve store order within each
    return sorted(by_id.values(), key=lambda v: (v["kind"] != "edition", v["sort_order"], v["name"]))


@app.get("/api/settings")
async def get_settings():
    return {"steam_id": await db.get_setting("steam_id") or ""}


@app.post("/api/settings")
async def save_settings(body: SettingsIn, background_tasks: BackgroundTasks):
    await db.set_setting("steam_id", body.steam_id.strip())
    background_tasks.add_task(do_sync)
    return {"ok": True}


@app.get("/api/wishlist")
async def get_wishlist():
    apps_with_variants = await db.get_apps_with_variants()
    return build_response(await db.get_wishlist_data(), apps_with_variants)


@app.get("/api/variants/{app_id}")
async def get_variants(app_id: int):
    return build_variants(await db.get_variants(app_id))


@app.post("/api/chat")
async def chat_endpoint(body: ChatIn):
    return await chat.answer_query(body.message)


@app.post("/api/refresh")
async def refresh(background_tasks: BackgroundTasks, force: bool = False):
    if fetcher.sync_status["is_syncing"]:
        return {"ok": False, "message": "Already syncing"}

    # Skip the slow full re-fetch if prices are still fresh — unless forced.
    if not force:
        hrs = await hours_since_last_sync()
        if hrs is not None and hrs < STALE_HOURS:
            return {"ok": False, "fresh": True, "hours": round(hrs, 1),
                    "message": "Prices are already up to date."}

    background_tasks.add_task(do_sync)
    return {"ok": True}


@app.get("/api/status")
async def get_status():
    last_sync = await db.get_last_sync()
    game_count = await db.get_game_count()
    return SyncStatus(
        is_syncing=fetcher.sync_status["is_syncing"],
        phase=fetcher.sync_status["phase"],
        done=fetcher.sync_status["done"],
        total=fetcher.sync_status["total"],
        last_sync=last_sync,
        game_count=game_count,
        last_error=fetcher.sync_status["last_error"],
    )


# Serve frontend — must be last (catch-all)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
