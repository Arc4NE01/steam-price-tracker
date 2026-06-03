import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.db")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS games (
    app_id INTEGER PRIMARY KEY,
    name TEXT,
    header_image TEXT,
    updated_at TEXT,
    is_upcoming INTEGER DEFAULT 0,
    release_date TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS prices (
    app_id INTEGER,
    region TEXT,
    currency TEXT,
    price_raw INTEGER,
    price_usd REAL,
    discount_pct INTEGER,
    fetched_at TEXT,
    PRIMARY KEY (app_id, region)
);
CREATE TABLE IF NOT EXISTS fx_rates (
    currency TEXT PRIMARY KEY,
    rate_to_usd REAL,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS variants (
    app_id INTEGER,          -- wishlist game this variant is shown under
    variant_id TEXT,         -- 'pkg:85568' or 'bundle:757'
    kind TEXT,               -- 'edition' or 'bundle'
    name TEXT,
    region TEXT,
    price_raw INTEGER,
    price_usd REAL,
    currency TEXT,
    discount_pct INTEGER,
    header_image TEXT,
    sort_order INTEGER DEFAULT 0,
    fetched_at TEXT,
    PRIMARY KEY (app_id, variant_id, region)
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(CREATE_SQL)
        # Migrate older databases that lack the newer columns
        async with db.execute("PRAGMA table_info(games)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if "is_upcoming" not in cols:
            await db.execute("ALTER TABLE games ADD COLUMN is_upcoming INTEGER DEFAULT 0")
        if "release_date" not in cols:
            await db.execute("ALTER TABLE games ADD COLUMN release_date TEXT DEFAULT ''")
        await db.commit()


async def get_setting(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))
        await db.commit()


async def upsert_game(app_id: int, name: str, header_image: str, updated_at: str,
                      is_upcoming: int | None = None, release_date: str | None = None):
    """Insert/update a game. When is_upcoming/release_date are None, existing
    values are preserved (so stub inserts don't wipe real release info)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO games (app_id, name, header_image, updated_at, is_upcoming, release_date)
            VALUES (?, ?, ?, ?, COALESCE(?, 0), COALESCE(?, ''))
            ON CONFLICT(app_id) DO UPDATE SET
                name         = excluded.name,
                header_image = excluded.header_image,
                updated_at   = excluded.updated_at,
                is_upcoming  = COALESCE(?, games.is_upcoming),
                release_date = COALESCE(?, games.release_date)
            """,
            (app_id, name, header_image, updated_at, is_upcoming, release_date,
             is_upcoming, release_date),
        )
        await db.commit()


async def upsert_price(
    app_id: int,
    region: str,
    currency: str,
    price_raw: int,
    price_usd: float,
    discount_pct: int,
    fetched_at: str,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)",
            (app_id, region, currency, price_raw, price_usd, discount_pct, fetched_at),
        )
        await db.commit()


async def upsert_fx_rate(currency: str, rate: float, fetched_at: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO fx_rates VALUES (?,?,?)", (currency, rate, fetched_at)
        )
        await db.commit()


async def get_fx_rates() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT currency, rate_to_usd FROM fx_rates") as cur:
            rows = await cur.fetchall()
            return {r[0]: r[1] for r in rows}


async def get_wishlist_data() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM games ORDER BY name COLLATE NOCASE") as cur:
            games = await cur.fetchall()
        result = []
        for g in games:
            app_id = g["app_id"]
            async with db.execute("SELECT * FROM prices WHERE app_id=?", (app_id,)) as cur:
                price_rows = await cur.fetchall()
            prices = {r["region"]: dict(r) for r in price_rows}
            result.append(
                {
                    "app_id": app_id,
                    "name": g["name"],
                    "header_image": g["header_image"],
                    "prices": prices,
                    "is_upcoming": g["is_upcoming"] if "is_upcoming" in g.keys() else 0,
                    "release_date": g["release_date"] if "release_date" in g.keys() else "",
                }
            )
        return result


async def get_last_sync():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT MAX(fetched_at) FROM prices") as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def get_game_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM games") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_stub_app_ids() -> list:
    """Return app_ids whose name is still a stub ('App XXXXX') — need a name fetch."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT app_id FROM games WHERE name LIKE 'App %'") as cur:
            return [r[0] for r in await cur.fetchall()]


async def get_known_app_ids() -> set:
    """Return the set of all app_ids currently in the games table."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT app_id FROM games") as cur:
            return {r[0] for r in await cur.fetchall()}


async def get_games_missing_release() -> list:
    """Return app_ids that have a real name but no release_date yet (need backfill)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT app_id FROM games WHERE (release_date IS NULL OR release_date = '') "
            "AND name NOT LIKE 'App %'"
        ) as cur:
            return [r[0] for r in await cur.fetchall()]


async def upsert_variant(app_id, variant_id, kind, name, region, price_raw,
                         price_usd, currency, discount_pct, header_image,
                         sort_order, fetched_at):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO variants VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (app_id, variant_id, kind, name, region, price_raw, price_usd,
             currency, discount_pct, header_image, sort_order, fetched_at),
        )
        await db.commit()


async def get_variants(app_id: int) -> list:
    """All variant rows for one game (all regions), ordered for display."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM variants WHERE app_id=? ORDER BY kind, sort_order, variant_id",
            (app_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_lowest_regions() -> dict:
    """Return {app_id: region with the lowest positive price_usd}."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT app_id, region, price_usd FROM prices WHERE price_usd > 0"
        ) as cur:
            rows = await cur.fetchall()
    best = {}
    for app_id, region, usd in rows:
        if app_id not in best or usd < best[app_id][1]:
            best[app_id] = (region, usd)
    return {aid: rv[0] for aid, rv in best.items()}


async def get_apps_with_variants() -> set:
    """app_ids that have at least one variant (edition or bundle)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT DISTINCT app_id FROM variants") as cur:
            return {r[0] for r in await cur.fetchall()}


async def delete_stale_variants(before_iso: str):
    """Remove variant rows not refreshed in the latest sync (vanished editions/bundles)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM variants WHERE fetched_at < ?", (before_iso,))
        await db.commit()


async def get_resolved_pairs() -> set:
    """Return the set of (app_id, region) pairs that have a price row.

    A row exists once a region is *resolved* — either a real price or a
    sentinel marking 'confirmed not available'. Pairs NOT in this set are
    ones whose fetch failed transiently and should be retried.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT app_id, region FROM prices") as cur:
            return {(r[0], r[1]) for r in await cur.fetchall()}
