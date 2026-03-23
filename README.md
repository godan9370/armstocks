# @RM!2T0CKS — Office Stock Exchange

A mock real-time stock exchange for the office. Trade shares of your colleagues, fire market events, and watch prices move live.

## Setup

1. Make sure Python 3 is installed
2. Put headshot images (`.jpg`, `.png`, etc.) in the **parent folder** — each person becomes a tradeable stock
3. Run the app:

**Windows:** double-click `start.bat`
**Mac/Linux:** `bash start.sh`

4. Open the URL printed in the terminal. Share the network URL with the office.

## Features

- Auto-discovers stocks from headshot images in the parent folder
- IPO phase → open trading
- Buy/sell shares, portfolio tracking, leaderboard
- Admin panel: fire market events (AI-interpreted), switch phases
- Prices move with demand + random drift + dividends

## Admin

Default password: `armbets` (change in `app.py` line 13)

## Requirements

- Python 3.8+
- `flask` and `anthropic` packages (`pip install flask anthropic`)
- Optional: `ANTHROPIC_API_KEY` env var for AI-powered event interpretation
