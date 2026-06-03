# Steam Price Tracker

A lightweight, self-hosted web app that tracks the **regional prices** of the games on your
Steam wishlist — focused on **China**, **Ukraine**, and the **cheapest region**
across 8 markets. Includes a price assistant chatbot and an editions/bundles browser.
Runs entirely on your own machine.

---

## Features

- **Regional price table** - China, Ukraine, and the cheapest of 8 regions (US, Turkey, Argentina, India, UK, Brazil) for every wishlisted game, in native currency + USD.
- **Smart sync** - first sync fetches everything; afterwards prices auto-refresh once a day in the background. Opening the app is always instant (reads from a local database).
- **Unreleased tags & countdown** - upcoming games are flagged with a release-date countdown ("in 62 days").
- **Editions & bundles** - games with multiple editions (Standard / Gold / Deluxe) or store bundles get a dropdown showing each variant's price and cheapest region.
- **Price assistant chatbot** - ask in plain English, e.g. *"Elden Ring price in China vs Ukraine"*, for **any** Steam game (not just your wishlist).
- **Search, sort & filters** - search by name; sort by price/discount; filter by discounted or unreleased.

---

## Tech stack

| Layer | Tech |
|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn, APScheduler, httpx, aiosqlite |
| Storage | SQLite (single local file) |
| Frontend | Vanilla HTML / CSS / JS (served by FastAPI) |
| Data | Public Steam store APIs + open.er-api.com for FX rates |

---

## Getting started (Windows)

### 1. Install Python
Download **Python 3.11+** from [python.org](https://www.python.org/downloads/) and during
install **tick "Add Python to PATH"**.

### 2. Run it
Double-click **`start.bat`** (or run it from a terminal). It will:
- install the Python dependencies,
- start the server at `http://127.0.0.1:8000`,
- open the app in your browser.

Keep that window open while you use the app (minimize it). Closing it stops the server.

### 3. Configure
Click **⚙ Settings** and enter your Steam ID or profile URL — any of these work:
- `76561198XXXXXXXXX`
- `https://steamcommunity.com/profiles/76561198XXXXXXXXX`
- `https://steamcommunity.com/id/yourname`

> Your wishlist must be **Public**: Steam → Edit Profile → Privacy Settings →
> *Game details* → **Public**.

The first sync pulls prices for every wishlisted game across 8 regions at ~1 request/second
(Steam's rate limit), so it can take several minutes. After that, opening the app is instant.

### Other platforms
There's no shell script included, but on macOS/Linux you can run it manually:
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
# then open http://127.0.0.1:8000
```

---

## Notes

- Prices come from Steam's public store API; availability and currency vary by region (some games show "N/A" where they aren't sold).
- FX rates are refreshed daily from a free, no-key source.
- This is a **localhost** app intended for personal use. Your wishlist data stays in a local SQLite file (`backend/prices.db`), which is **not** committed to the repo.
