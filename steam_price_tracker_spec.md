# Steam Price Tracker — Claude Code Build Spec

## Project goal
A localhost Progressive Web App (PWA) that fetches regional Steam prices for
your wishlisted games, shows the cheapest region, price differences, and
upcoming game prices. Works offline after first load using cached data.

---

## Tech stack (keep it lightweight)

| Layer | Tech |
|---|---|
| Backend | Python 3.11+, FastAPI, APScheduler, SQLite (via `aiosqlite`) |
| Frontend | React 18 + Vite, TanStack Query, Tailwind CSS |
| Offline | Vite PWA plugin (Workbox), IndexedDB via `idb` |
| Packaging | Single `start.sh` script — runs both backend and frontend |

No Docker. No heavy ORM. SQLite is the only DB. Everything runs locally.

---

## Project structure

```
steam-price-tracker/
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── scheduler.py         # APScheduler fetch jobs
│   ├── fetcher.py           # Steam API calls + FX rate fetch
│   ├── db.py                # SQLite schema + queries
│   ├── models.py            # Pydantic models
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── pages/
│   │   │   ├── Wishlist.jsx      # Main price table
│   │   │   ├── Upcoming.jsx      # Upcoming releases
│   │   │   └── Settings.jsx      # Steam ID + region config
│   │   ├── components/
│   │   │   ├── PriceGrid.jsx     # Region columns table
│   │   │   ├── GameCard.jsx      # Single game row
│   │   │   ├── OfflineBanner.jsx # "Last synced X ago" warning
│   │   │   └── LowestBadge.jsx   # Green badge for cheapest region
│   │   ├── hooks/
│   │   │   ├── useWishlist.js    # TanStack Query + IndexedDB fallback
│   │   │   ├── useOffline.js     # navigator.onLine watcher
│   │   │   └── useRegions.js     # User's chosen regions from settings
│   │   ├── lib/
│   │   │   ├── api.js            # fetch wrappers for backend endpoints
│   │   │   ├── idb.js            # IndexedDB read/write helpers
│   │   │   └── currency.js       # Price formatting per locale
│   │   └── sw.js                 # Service Worker (Workbox-generated)
│   ├── public/
│   │   ├── manifest.json         # PWA manifest
│   │   └── icons/                # 192x192, 512x512 app icons
│   ├── vite.config.js
│   └── package.json
├── start.sh                      # Starts backend + frontend together
└── README.md
```

---

## Phase 1 — Backend

### 1.1 SQLite schema (`db.py`)

Create these tables on startup:

```sql
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS games (
    app_id INTEGER PRIMARY KEY,
    name TEXT,
    header_image TEXT,
    release_date TEXT,
    is_upcoming INTEGER DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    app_id INTEGER,
    region TEXT,          -- e.g. 'us', 'tr', 'ar', 'in', 'bd', 'gb', 'eu'
    currency TEXT,        -- e.g. 'USD', 'TRY'
    price_raw INTEGER,    -- in cents/smallest unit (e.g. 1999 = $19.99)
    price_usd REAL,       -- normalized to USD using FX rate at fetch time
    discount_pct INTEGER,
    fetched_at TEXT,
    PRIMARY KEY (app_id, region)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    currency TEXT PRIMARY KEY,
    rate_to_usd REAL,
    fetched_at TEXT
);
```

### 1.2 Steam API fetcher (`fetcher.py`)

#### Wishlist fetch
```
GET https://store.steampowered.com/wishlist/profiles/{STEAM_ID}/wishlistdata/
```
Returns JSON: `{ "app_id": { "name": ..., "priority": ..., ... }, ... }`
Parse app IDs, store names. Mark `is_upcoming=0`.

#### Price fetch (per game, per region)
```
GET https://store.steampowered.com/api/appdetails
    ?appids={APP_ID}
    &cc={REGION_CODE}
    &filters=price_overview
```
Rate limit: **1 request per second** — use `asyncio.sleep(1)` between calls.
Response path: `data[str(app_id)]["data"]["price_overview"]`
Fields: `final` (price in cents), `currency`, `discount_percent`

If `price_overview` is missing, the game is free — store price as 0.

#### Upcoming games fetch
```
GET https://store.steampowered.com/api/featuredcategories/
```
Parse `coming_soon.items[]` — get app_id, name, header_image.
Then fetch prices for those app IDs too (mark `is_upcoming=1`).

#### FX rates fetch
```
GET https://open.er-api.com/v6/latest/USD
```
Free tier, no key needed. Store rates for: TRY, ARS, INR, BDT, GBP, EUR, BRL.
Refresh FX rates once daily (they change slowly).

### 1.3 Scheduler (`scheduler.py`)

Use APScheduler with AsyncIOScheduler:

- **On startup**: run full wishlist + price fetch immediately if DB is empty
- **Every 6 hours**: re-fetch all prices
- **Every 24 hours**: re-fetch FX rates
- **Every 12 hours**: re-fetch upcoming games list

### 1.4 FastAPI endpoints (`main.py`)

```
GET  /api/settings              → return stored settings (steam_id, regions)
POST /api/settings              → save steam_id and chosen regions
GET  /api/wishlist              → list of games with all region prices + lowest
GET  /api/upcoming              → upcoming games with prices
GET  /api/prices/{app_id}       → price detail for one game across all regions
POST /api/refresh               → trigger immediate re-fetch (manual button)
GET  /api/status                → last_synced timestamp, next_sync ETA
```

#### Response shape for `/api/wishlist`

```json
[
  {
    "app_id": 1091500,
    "name": "Cyberpunk 2077",
    "header_image": "https://...",
    "prices": {
      "us": { "price": 59.99, "price_usd": 59.99, "currency": "USD", "discount_pct": 0 },
      "tr": { "price": 499.00, "price_usd": 15.21, "currency": "TRY", "discount_pct": 0 },
      "ar": { "price": 2999.00, "price_usd": 3.12, "currency": "ARS", "discount_pct": 0 },
      "bd": { "price": 1299.00, "price_usd": 11.83, "currency": "BDT", "discount_pct": 0 }
    },
    "lowest_region": "ar",
    "lowest_price_usd": 3.12,
    "vs_us_diff_usd": -56.87
  }
]
```

### 1.5 CORS + startup

Enable CORS for `http://localhost:5173` (Vite dev server).
On startup, create DB tables, load settings, start scheduler.

### 1.6 requirements.txt

```
fastapi
uvicorn[standard]
aiosqlite
apscheduler
httpx
pydantic
```

---

## Phase 2 — Frontend

### 2.1 Vite + React setup

```bash
npm create vite@latest frontend -- --template react
cd frontend
npm install tailwindcss @tailwindcss/vite
npm install @tanstack/react-query
npm install idb
npm install vite-plugin-pwa
```

### 2.2 Settings page (`Settings.jsx`)

Fields:
- Steam ID input (64-bit SteamID or profile URL — parse either)
- Multi-select checkboxes for regions: US, TR, AR, IN, BD, GB, EU, BR (default: all)
- Currency display toggle: show prices in native currency OR normalized USD

On save → `POST /api/settings` → then trigger `POST /api/refresh`.

### 2.3 Wishlist page (`Wishlist.jsx`)

- Top bar: last synced time + "Refresh now" button
- `OfflineBanner` if `navigator.onLine === false`
- `PriceGrid` table: one row per game, one column per selected region
- Columns: Game (with thumbnail) | [Region cols] | Lowest | vs US Δ
- Sort options: by name, by lowest USD price, by biggest discount

### 2.4 PriceGrid component (`PriceGrid.jsx`)

Table structure:
```
| Game        | US    | TR     | AR    | IN    | BD    | Lowest | vs US  |
|-------------|-------|--------|-------|-------|-------|--------|--------|
| Cyberpunk   | $59.99| ₺499  | $3.12✓| ₹899 | ৳1299 | AR $3.12 | -$56.87|
```

- Lowest cell gets green background badge (`LowestBadge`)
- Δ column: red if more expensive than US, green if cheaper
- Click any region cell → tooltip showing native currency + USD equiv + discount %
- Hovering a game row highlights the whole row

### 2.5 Upcoming page (`Upcoming.jsx`)

- Grid of cards (not table) — each card shows: header image, name, release date, lowest regional price
- Filter: "price announced" vs "TBA"

### 2.6 Offline behavior (`useWishlist.js`)

```js
// 1. On successful fetch from backend → write to IndexedDB
// 2. On failed fetch (offline) → read from IndexedDB
// 3. Show OfflineBanner with "Last synced: X hours ago"
// 4. When navigator.onLine fires true → auto-refetch + update IDB
```

Use TanStack Query's `staleTime: Infinity` + manual invalidation pattern.
Use `idb` library for clean IndexedDB API.

### 2.7 PWA manifest (`manifest.json`)

```json
{
  "name": "Steam Price Tracker",
  "short_name": "SteamPrices",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#1a1a2e",
  "theme_color": "#1a1a2e",
  "icons": [
    { "src": "/icons/192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icons/512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

### 2.8 vite.config.js

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg}'],
        runtimeCaching: [{
          urlPattern: /^http:\/\/localhost:8000\/api\//,
          handler: 'NetworkFirst',
          options: {
            cacheName: 'api-cache',
            networkTimeoutSeconds: 5,
            expiration: { maxAgeSeconds: 86400 }
          }
        }]
      }
    })
  ],
  server: { proxy: { '/api': 'http://localhost:8000' } }
})
```

---

## Phase 3 — Start script

`start.sh`:
```bash
#!/bin/bash
echo "Starting Steam Price Tracker..."

# Backend
cd backend
pip install -r requirements.txt -q
uvicorn main:app --host 127.0.0.1 --port 8000 --reload &
BACKEND_PID=$!
cd ..

# Frontend
cd frontend
npm install -q
npm run dev &
FRONTEND_PID=$!
cd ..

echo ""
echo "✓ Backend:  http://localhost:8000"
echo "✓ Frontend: http://localhost:5173"
echo ""
echo "Open http://localhost:5173 and enter your Steam ID in Settings first."
echo "Press Ctrl+C to stop."

trap "kill $BACKEND_PID $FRONTEND_PID" EXIT
wait
```

---

## Phase 4 — Mobile install (PWA)

No separate app needed. The Vite PWA setup makes it installable on Android and iOS:

- **Android Chrome**: open `http://YOUR_LOCAL_IP:5173` on your phone → browser shows "Add to Home Screen" banner automatically
- **iOS Safari**: open the same URL → Share → Add to Home Screen
- Works offline because of Service Worker cache — last fetched prices are always available

To expose your localhost to your phone:
1. Make sure phone and PC are on the same WiFi
2. Find your PC's local IP (`ipconfig` on Windows, `ip a` on Linux)
3. Open `http://192.168.X.X:5173` on your phone

---

## Default regions to configure

| Region code | Country | Currency | Why it's useful |
|---|---|---|---|
| `us` | United States | USD | Baseline comparison |
| `tr` | Turkey | TRY | Historically cheapest for many games |
| `ar` | Argentina | ARS | Often cheapest region |
| `in` | India | INR | Good discounts |
| `bd` | Bangladesh | BDT | Your local region |
| `gb` | United Kingdom | GBP | EU/UK reference |
| `br` | Brazil | BRL | Often discounted |

---

## Build order for Claude Code

Feed these phases in order:

1. `backend/db.py` — schema and query functions
2. `backend/fetcher.py` — all Steam + FX API calls
3. `backend/scheduler.py` — APScheduler jobs
4. `backend/models.py` — Pydantic response models
5. `backend/main.py` — FastAPI app wiring everything together
6. `frontend/` scaffold — Vite + Tailwind + PWA config
7. `frontend/src/lib/` — api.js, idb.js, currency.js
8. `frontend/src/hooks/` — useWishlist, useOffline, useRegions
9. `frontend/src/pages/Settings.jsx` — first page to build (needs Steam ID)
10. `frontend/src/components/PriceGrid.jsx` — core component
11. `frontend/src/pages/Wishlist.jsx` — main page
12. `frontend/src/pages/Upcoming.jsx` — secondary page
13. `start.sh` — final wiring

---

## Notes for Claude Code

- Steam's price API returns `null` for games not available in a region — handle gracefully (show "N/A")
- Argentina prices are volatile — FX rates matter a lot, refresh daily
- Steam rate-limits at ~200 requests/min — the 1 req/sec sleep is intentional, do not remove it
- If the wishlist is private, `/wishlistdata/` returns `[]` — show a clear error in Settings
- `app_id` from the wishlist endpoint is the string key, not a nested field — parse as `int(key)`
- Bangladesh (`bd`) is a valid Steam country code and returns BDT prices correctly
- For the upcoming games page, not all upcoming games have prices yet — filter those out or show "Price TBA"
