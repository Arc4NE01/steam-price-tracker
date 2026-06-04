# Steam Price Tracker

Tracks the price of my Steam wishlist games across regions: mainly China, Ukraine, and
whichever region is cheapest. Runs locally, Python only, no Node or API keys.

## What it does

- Shows each wishlisted game's price in China, Ukraine, and the cheapest of 8 regions (US, Turkey, Argentina, India, UK, Brazil), in local currency and USD.
- Caches everything in a local SQLite file, so opening the app is instant. Prices refresh once a day in the background.
- Flags unreleased games with a countdown to launch.
- Shows editions (Standard/Gold/Deluxe) and store bundles in a dropdown when a game has them.
- Has a small chat box to look up any game's regional price in plain English, e.g. "Elden Ring price in China vs Ukraine".
- Search, sort, and filter (discounted / unreleased).

## Stack

Python 3.11+, FastAPI, SQLite, plain HTML/CSS/JS. Data from Steam's public store API and a free FX rate API.

## Running it (Windows)

1. Install [Python 3.11+](https://www.python.org/downloads/).
2. Double-click `start.bat`. It installs the dependencies, starts the server, and opens the app.
3. Click Settings and paste your Steam ID or profile URL. Your wishlist needs to be public (Steam → Edit Profile → Privacy → Game details → Public).

First sync takes a few minutes (Steam rate-limits to ~1 request/sec). After that it's instant.

On macOS/Linux there's no script, but you can run it by hand:

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8770
```

## Layout

```
backend/    FastAPI app, Steam fetching, SQLite, chat
frontend/   index.html, app.js, style.css
start.bat   launcher
```

Your wishlist/price data lives in `backend/prices.db`, which is gitignored so nothing personal gets committed.
