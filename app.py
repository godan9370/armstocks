"""
@RM!2T0CKS — The Office Stock Exchange
"""

from flask import Flask, request, jsonify, send_from_directory, render_template
import json, os, random, threading, time, uuid, requests as http_requests
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "armbets-neon-secret-2026"

# ─────────────────────────── CONFIG ───────────────────────────
_static_hs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "headshots")
HEADSHOTS_DIR  = _static_hs if os.path.isdir(_static_hs) else \
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

DATA_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "market.json")

# PostgreSQL — set DATABASE_URL on Render for persistent storage.
# Falls back to local JSON file if not set (local dev / testing).
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
if _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Spotify — optional. Set SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET on Render.
SPOTIFY_CLIENT_ID     = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
_spotify_token        = None
_spotify_token_expiry = 0
ADMIN_PASSWORD = "armbets"
IPO_PRICE      = 1.0
STARTING_BUCKS = 30.0
TOTAL_SHARES   = 1000
BUY_IMPACT     = 0.025
SELL_IMPACT    = 0.025
DRIFT_INTERVAL = 300
DIV_INTERVAL   = 3600

# ── Futures ──
FUTURES_COST      = 1.0
FUTURES_PAYOUT    = 1.80
FUTURES_DURATIONS = [30, 60, 120]

# ── Predictions (parimutuel pool) ──
PREDICTION_COST = 1.0   # ₳ per bet; payout is pool-proportional at resolution

# ── Booster Packs ──
PACK_COST  = 5.0
PACK_SIZE  = 5
# (tier_name, 1-in-N chance, value_multiplier_over_base_price)
SHINY_TIERS = [
    ("PLATINUM", 250, 5.0),
    ("GOLD",     100, 4.0),
    ("SILVER",    50, 3.0),
    ("BRONZE",    25, 2.0),
]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# ── Spin the Wheel ──
WHEEL_COST = 2.0
# (outcome_name, weight_out_of_100)  — must sum to 100
WHEEL_OUTCOMES = [
    ("pack",       20),   # 20.0%  — full booster pack   (was 33%)
    ("free_stock", 22),   # 22.0%  — 1 random stock share
    ("bust",       35),   # 35.0%  — lose the ₳2         (was 20%)
    ("cash",        8),   #  8.0%  — ₳4 back             (was 10%)
    ("spin_again", 11),   # 11.0%  — free re-spin (auto)
    ("bronze",      4),   #  4.0%  — bronze shiny card   (unchanged)
]
# Visual segment arc ranges (° from top, clockwise) — must match frontend
# pack=72° · free_stock=79.2° · bust=126° · cash=28.8° · spin_again=39.6° · bronze=14.4°
WHEEL_ANGLE_RANGES = {
    "pack":       (0,     72.0),
    "free_stock": (72.0,  151.2),
    "bust":       (151.2, 277.2),
    "cash":       (277.2, 306.0),
    "spin_again": (306.0, 345.6),
    "bronze":     (345.6, 360.0),
}

# ── ETF Funds ──
ETF_START_PRICE      = 5.0    # ₳ per unit at IPO (NAV tracks constituent performance)
ETF_BASE_DIV_RATE    = 0.003  # 0.3 %/hr base dividend on holdings value
ETF_MIN_DIV_RATE     = 0.001  # floor: 0.1 %/hr
ETF_MAX_DIV_RATE     = 0.005  # ceiling: 0.5 %/hr

ETF_FUNDS = {
    "directors": {
        "name":    "Directors Fund",
        "emoji":   "◈",
        "color":   "#00f5ff",
        "members": ["Andrew Hayne", "Andrew Lilleyman", "Mark", "Jesse", "Amber Stewart", "Howard", "Ian"],
        "desc":    "The leadership portfolio",
    },
    "admin": {
        "name":    "Admin Fund",
        "emoji":   "◉",
        "color":   "#cc00ff",
        "members": ["Svetlana", "Meg", "Rhiannon", "Lucy"],
        "desc":    "Operations & admin excellence",
    },
    "graduate": {
        "name":    "Student & Grad Fund",
        "emoji":   "▲",
        "color":   "#00ff88",
        "members": ["Nadia Poppen", "Raman", "Bella", "Ryan", "Zihe Chen"],
        "desc":    "High growth emerging talent",
    },
    "meme": {
        "name":    "Meme Stocks",
        "emoji":   "★",
        "color":   "#ff0090",
        "members": ["KatherineChair", "Nesbubu"],
        "desc":    "Chaotic neutral. High risk, high reward",
    },
    "bids": {
        "name":    "Bids Team Fund",
        "emoji":   "⚡",
        "color":   "#ff8800",
        "members": ["Dan", "Axeris", "Kirsten"],
        "desc":    "Winning contracts, growing value",
    },
}

# ─────────────────────────── HELPERS ──────────────────────────
def now_iso():
    return datetime.utcnow().isoformat()

def make_ticker(name: str, used: set) -> str:
    parts = name.split()
    base  = "".join(c for c in parts[0].upper() if c.isalpha())[:4]
    tick  = base
    i = 1
    while tick in used:
        tick = base[:3] + str(i)
        i += 1
    used.add(tick)
    return tick

def roll_shiny():
    """Returns (tier, multiplier) or None. Checked rarest-first."""
    for tier, denom, mult in SHINY_TIERS:
        if random.random() < 1.0 / denom:
            return (tier, mult)
    return None

def etf_constituent_tickers(fund_id):
    """Return list of tickers matching the fund's member names."""
    fund = ETF_FUNDS.get(fund_id)
    if not fund: return []
    tickers = []
    for member_name in fund["members"]:
        for t, s in market["stocks"].items():
            if s["name"].lower() == member_name.lower():
                tickers.append(t)
                break
    return tickers

def compute_etf_nav(fund_id):
    """NAV = ETF_START_PRICE × avg(current/ipo) across constituents."""
    tickers = etf_constituent_tickers(fund_id)
    if not tickers:
        return ETF_START_PRICE
    ratios = []
    for t in tickers:
        s = market["stocks"].get(t)
        if s and s["ipo_price"] > 0:
            ratios.append(s["current_price"] / s["ipo_price"])
    if not ratios:
        return ETF_START_PRICE
    return round(ETF_START_PRICE * (sum(ratios) / len(ratios)), 4)

def _generate_pack_cards(u):
    """Generate PACK_SIZE cards for a user (no cost deduction). Returns card list."""
    tickers = list(market["stocks"].keys())
    cards   = []
    for _ in range(PACK_SIZE):
        base_ticker = random.choice(tickers)
        base_stock  = market["stocks"][base_ticker]
        shiny_roll  = roll_shiny()
        if shiny_roll:
            tier, mult = shiny_roll
            shiny_key  = f"{base_ticker}_{tier}"
            if shiny_key not in market["shiny_registry"]:
                market["shiny_registry"][shiny_key] = {
                    "shiny_key":   shiny_key,
                    "base_ticker": base_ticker,
                    "name":        base_stock["name"],
                    "image":       base_stock["image"],
                    "tier":        tier,
                    "multiplier":  mult,
                }
            u["shiny_portfolio"][shiny_key] = u["shiny_portfolio"].get(shiny_key, 0) + 1
            cards.append({
                "type":        "shiny",
                "shiny_key":   shiny_key,
                "base_ticker": base_ticker,
                "name":        base_stock["name"],
                "image":       base_stock["image"],
                "tier":        tier,
                "multiplier":  mult,
                "base_price":  round(base_stock["current_price"], 4),
                "shiny_price": round(base_stock["current_price"] * mult, 4),
            })
        else:
            u["portfolio"][base_ticker] = u["portfolio"].get(base_ticker, 0) + 1
            cards.append({
                "type":  "normal",
                "ticker": base_ticker,
                "name":   base_stock["name"],
                "image":  base_stock["image"],
                "price":  round(base_stock["current_price"], 4),
            })
    return cards


def _resolve_wheel_spin(u, free=False):
    """Execute one wheel spin. Charges WHEEL_COST unless free=True.
    Returns (outcome, land_angle, reward_dict)."""
    if not free:
        u["arm_bucks"] -= WHEEL_COST

    names   = [o[0] for o in WHEEL_OUTCOMES]
    weights = [o[1] for o in WHEEL_OUTCOMES]
    outcome = random.choices(names, weights=weights, k=1)[0]

    lo, hi     = WHEEL_ANGLE_RANGES[outcome]
    land_angle = round(random.uniform(lo + 0.5, hi - 0.5), 1)

    reward = {"type": outcome}

    if outcome == "pack":
        cards = _generate_pack_cards(u)
        reward["cards"] = cards

    elif outcome == "free_stock":
        tickers     = list(market["stocks"].keys())
        base_ticker = random.choice(tickers)
        base_stock  = market["stocks"][base_ticker]
        u["portfolio"][base_ticker] = u["portfolio"].get(base_ticker, 0) + 1
        reward["ticker"] = base_ticker
        reward["name"]   = base_stock["name"]
        reward["image"]  = base_stock["image"]
        reward["price"]  = round(base_stock["current_price"], 4)

    elif outcome == "cash":
        u["arm_bucks"] += 4.0
        reward["amount"] = 4.0

    elif outcome == "bronze":
        tickers     = list(market["stocks"].keys())
        base_ticker = random.choice(tickers)
        base_stock  = market["stocks"][base_ticker]
        shiny_key   = f"{base_ticker}_BRONZE"
        if shiny_key not in market["shiny_registry"]:
            market["shiny_registry"][shiny_key] = {
                "shiny_key":   shiny_key,
                "base_ticker": base_ticker,
                "name":        base_stock["name"],
                "image":       base_stock["image"],
                "tier":        "BRONZE",
                "multiplier":  2.0,
            }
        u["shiny_portfolio"][shiny_key] = u["shiny_portfolio"].get(shiny_key, 0) + 1
        reward["shiny_key"]  = shiny_key
        reward["name"]       = base_stock["name"]
        reward["image"]      = base_stock["image"]
        reward["tier"]       = "BRONZE"
        reward["multiplier"] = 2.0
        reward["base_price"] = round(base_stock["current_price"], 4)
        reward["shiny_price"]= round(base_stock["current_price"] * 2.0, 4)

    # bust: nothing — just lost the ₳2

    return outcome, land_angle, reward


def pred_odds(p):
    """Return (yes_mult, no_mult) — how much ₳ back per ₳1 bet if that side wins."""
    y, n = p["yes_bets"], p["no_bets"]
    total = y + n
    if total == 0:
        return None, None
    yes_mult = round(total / y, 2) if y > 0 else None
    no_mult  = round(total / n, 2) if n > 0 else None
    return yes_mult, no_mult

# ─────────────────────────── DATA I/O ─────────────────────────
def _get_db_conn(retries=3, delay=2):
    """Return a psycopg2 connection if DATABASE_URL is set, else None.
    Retries on failure so transient startup errors don't cause data loss."""
    if not _DATABASE_URL:
        return None
    import psycopg2
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(_DATABASE_URL)
            return conn
        except Exception as e:
            last_err = e
            print(f"  [DB] connect attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(delay)
    # All retries exhausted — raise so callers know DB is unavailable
    raise RuntimeError(f"[DB] Could not connect after {retries} attempts: {last_err}")

def _db_init_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS market_state (
            id   INTEGER PRIMARY KEY,
            data TEXT NOT NULL
        );
    """)
    conn.commit()
    cur.close()

def save(data):
    payload = json.dumps(data, default=str)
    if _DATABASE_URL:
        try:
            conn = _get_db_conn()
            _db_init_table(conn)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO market_state (id, data) VALUES (1, %s)
                ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data;
            """, (payload,))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print(f"  [DB] save error: {e}")
    else:
        # Local dev fallback: JSON file
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w") as f:
            f.write(payload)

def load():
    if _DATABASE_URL:
        # DATABASE_URL is set — ONLY use Postgres. Never fall back to JSON file
        # (the JSON file has stale/empty state and would cause a data reset).
        try:
            conn = _get_db_conn()
            _db_init_table(conn)
            cur = conn.cursor()
            cur.execute("SELECT data FROM market_state WHERE id = 1;")
            row = cur.fetchone()
            cur.close()
            conn.close()
            return json.loads(row[0]) if row else None
        except Exception as e:
            # Re-raise so init_market() does NOT create fresh data.
            # Render will restart the app and retry.
            raise RuntimeError(f"[DB] load failed: {e}")
    else:
        # Local dev — use JSON file
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as f:
                    d = json.load(f)
                    return d if d else None
            except Exception:
                return None
        return None

def init_market():
    existing = load()
    if existing:
        # Back-compat: add new top-level keys if missing
        if "predictions"      not in existing: existing["predictions"]      = []
        if "shiny_registry"   not in existing: existing["shiny_registry"]   = {}
        if "etf_dividends_paid" not in existing: existing["etf_dividends_paid"] = 0.0
        if "shiny_listings"   not in existing: existing["shiny_listings"]   = []
        if "radio_playlist"   not in existing: existing["radio_playlist"]   = ""
        save(existing)   # persist any new keys immediately
        return existing

    # Build a deduplicated name→filename map (prefer .jpg/.jpeg over .png/others)
    name_to_file = {}
    PREFER_ORDER = [".jpg", ".jpeg", ".png", ".gif", ".webp"]
    for fname in sorted(os.listdir(HEADSHOTS_DIR)):
        ext = os.path.splitext(fname)[1].lower()
        if ext not in IMAGE_EXTS:
            continue
        name = os.path.splitext(fname)[0]
        if name not in name_to_file:
            name_to_file[name] = fname
        else:
            existing_ext = os.path.splitext(name_to_file[name])[1].lower()
            if PREFER_ORDER.index(ext) < PREFER_ORDER.index(existing_ext):
                name_to_file[name] = fname

    stocks, used = {}, set()
    for name, fname in sorted(name_to_file.items()):
        ticker = make_ticker(name, used)
        stocks[ticker] = {
            "name":              name,
            "image":             fname,
            "ticker":            ticker,
            "ipo_price":         IPO_PRICE,
            "current_price":     IPO_PRICE,
            "shares_outstanding":TOTAL_SHARES,
            "shares_held":       0,
            "total_volume":      0,
            "price_history":     [
                {"ts": now_iso(), "price": IPO_PRICE, "volume": 0, "type": "ipo"}
            ],
        }

    data = {
        "phase":          "ipo",
        "created":        now_iso(),
        "last_drift":     now_iso(),
        "last_dividend":  now_iso(),
        "users":          {},
        "stocks":         stocks,
        "events":         [],
        "predictions":    [],
        "shiny_registry": {},   # shiny_key → {base_ticker, name, image, tier, multiplier}
        "shiny_listings": [],   # peer-to-peer resale listings
        "radio_playlist": "",   # Spotify playlist URL set by admin
    }
    save(data)
    print(f"  → Initialised {len(stocks)} stocks from headshots")
    return data

def _ensure_user_fields(u):
    if "futures"          not in u: u["futures"]          = []
    if "prediction_bets"  not in u: u["prediction_bets"]  = []
    if "shiny_portfolio"  not in u: u["shiny_portfolio"]  = {}
    if "pack_history"     not in u: u["pack_history"]     = []
    if "etf_portfolio"    not in u: u["etf_portfolio"]    = {}   # fund_id → units held

# ─────────────────────────── GLOBAL STATE ─────────────────────
market = None
lock   = threading.Lock()

# ─────────────────────────── AI EVENT INTERPRETER ─────────────
def interpret_event(event_text, stocks, sentiment="auto"):
    """Interpret an admin market event into stock impacts.
    sentiment: 'positive', 'negative', or 'auto' (let AI decide).
    """
    names_map = {t: stocks[t]["name"] for t in stocks}
    sentiment_hint = ""
    fallback_positive = None
    if sentiment == "positive":
        sentiment_hint = "The admin has flagged this as a POSITIVE outcome — affected stocks should go UP."
        fallback_positive = True
    elif sentiment == "negative":
        sentiment_hint = "The admin has flagged this as a NEGATIVE outcome — affected stocks should go DOWN."
        fallback_positive = False
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": (
                    f"You are the event engine for @RM!2T0CKS, a retro office stock exchange.\n"
                    f"Available stocks (ticker → person):\n{json.dumps(names_map)}\n\n"
                    f"Event: \"{event_text}\"\n"
                    + (f"{sentiment_hint}\n" if sentiment_hint else "") +
                    f"\nDetermine which stocks are affected and by what % (-50 to +50). "
                    f"Only include stocks clearly relevant to the event. "
                    f"Respond ONLY with valid JSON:\n"
                    f"{{\"impacts\":{{\"TICKER\":number}},\"summary\":\"exciting ticker headline ≤60 chars\"}}"
                )
            }]
        )
        result  = json.loads(resp.content[0].text.strip())
        impacts = {k.upper(): float(v) for k, v in result.get("impacts", {}).items() if k.upper() in stocks}
        summary = result.get("summary", event_text)[:80]
        return impacts, summary
    except Exception as e:
        print(f"  [AI] fallback ({e})")
        impacts = {}
        for ticker, stock in stocks.items():
            first = stock["name"].split()[0].lower()
            if first in event_text.lower():
                mag = round(random.uniform(5, 30), 1)
                if fallback_positive is True:
                    impacts[ticker] = mag
                elif fallback_positive is False:
                    impacts[ticker] = -mag
                else:
                    impacts[ticker] = mag * (1 if random.random() > 0.3 else -1)
        if not impacts:
            t = random.choice(list(stocks.keys()))
            mag = round(random.uniform(5, 25), 1)
            impacts[t] = mag if fallback_positive is not False else -mag
        return impacts, f"MARKET UPDATE: {event_text[:55]}"

# ─────────────────────────── ROUTES ───────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/headshots/<path:filename>")
def serve_headshot(filename):
    return send_from_directory(os.path.abspath(HEADSHOTS_DIR), filename)

# ── Register ──
@app.route("/api/register", methods=["POST"])
def register():
    body     = request.json or {}
    username = body.get("username", "").strip()
    if len(username) < 2:
        return jsonify({"error": "Username must be ≥ 2 characters"}), 400
    with lock:
        for uid, u in market["users"].items():
            if u["username"].lower() == username.lower():
                _ensure_user_fields(u)
                return jsonify({"user_id": uid, "username": u["username"],
                                "arm_bucks": u["arm_bucks"]})
        uid = str(uuid.uuid4())
        market["users"][uid] = {
            "username":        username,
            "arm_bucks":       STARTING_BUCKS,
            "portfolio":       {},
            "futures":         [],
            "prediction_bets": [],
            "shiny_portfolio": {},
            "pack_history":    [],
            "joined":          now_iso(),
            "trade_history":   [],
        }
        save(market)
    return jsonify({"user_id": uid, "username": username, "arm_bucks": STARTING_BUCKS})

# ── Market snapshot ──
@app.route("/api/market")
def get_market():
    with lock:
        stocks_out = {}
        for ticker, s in market["stocks"].items():
            hist = s["price_history"]
            prev = hist[-2]["price"] if len(hist) > 1 else s["ipo_price"]
            chg  = s["current_price"] - prev
            pct  = (chg / prev * 100) if prev else 0
            stocks_out[ticker] = {
                "name":             s["name"],
                "image":            s["image"],
                "ticker":           ticker,
                "current_price":    round(s["current_price"], 4),
                "ipo_price":        s["ipo_price"],
                "change":           round(chg, 4),
                "change_pct":       round(pct, 2),
                "shares_available": s["shares_outstanding"] - s["shares_held"],
                "total_volume":     s["total_volume"],
                "sparkline":        [p["price"] for p in hist[-20:]],
            }
        return jsonify({
            "phase":         market["phase"],
            "stocks":        stocks_out,
            "recent_events": market["events"][-15:],
        })

# ── Stock detail ──
@app.route("/api/stock/<ticker>")
def get_stock(ticker):
    with lock:
        s = market["stocks"].get(ticker.upper())
        if not s:
            return jsonify({"error": "Not found"}), 404
        return jsonify({**s, "price_history": s["price_history"][-200:]})

# ── Portfolio ──
@app.route("/api/portfolio")
def get_portfolio():
    uid = request.args.get("user_id", "")
    with lock:
        u = market["users"].get(uid)
        if not u:
            return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)
        holdings, port_val = [], 0.0
        for ticker, shares in u["portfolio"].items():
            s = market["stocks"].get(ticker)
            if s and shares > 0:
                val       = shares * s["current_price"]
                port_val += val
                holdings.append({
                    "ticker":        ticker,
                    "name":          s["name"],
                    "image":         s["image"],
                    "shares":        shares,
                    "current_price": round(s["current_price"], 4),
                    "ipo_price":     s["ipo_price"],
                    "value":         round(val, 4),
                    "change_pct":    round((s["current_price"] - s["ipo_price"]) / s["ipo_price"] * 100, 2),
                })
        # Shiny holdings
        shiny_holdings = []
        shiny_val = 0.0
        reg = market.get("shiny_registry", {})
        for sk, qty in u["shiny_portfolio"].items():
            if qty <= 0: continue
            info = reg.get(sk)
            if not info: continue
            base = market["stocks"].get(info["base_ticker"])
            if not base: continue
            price = round(base["current_price"] * info["multiplier"], 4)
            val   = round(price * qty, 4)
            shiny_val += val
            shiny_holdings.append({
                "shiny_key":   sk,
                "base_ticker": info["base_ticker"],
                "name":        info["name"],
                "image":       info["image"],
                "tier":        info["tier"],
                "multiplier":  info["multiplier"],
                "qty":         qty,
                "price":       price,
                "value":       val,
            })

        active_fut  = len([f for f in u["futures"]         if f["status"] == "active"])
        active_pred = len([b for b in u["prediction_bets"] if b["status"] == "pending"])
        return jsonify({
            "username":        u["username"],
            "arm_bucks":       round(u["arm_bucks"], 4),
            "portfolio_value": round(port_val + shiny_val, 4),
            "total_value":     round(u["arm_bucks"] + port_val + shiny_val, 4),
            "holdings":        holdings,
            "shiny_holdings":  shiny_holdings,
            "active_futures":  active_fut,
            "active_predictions": active_pred,
        })

# ── Buy ──
@app.route("/api/buy", methods=["POST"])
def buy():
    body   = request.json or {}
    uid    = body.get("user_id", "")
    ticker = body.get("ticker", "").upper()
    shares = int(body.get("shares", 1))
    if shares < 1:
        return jsonify({"error": "Must buy at least 1 share"}), 400
    with lock:
        u = market["users"].get(uid)
        s = market["stocks"].get(ticker)
        if not u: return jsonify({"error": "User not found"}), 404
        if not s: return jsonify({"error": "Stock not found"}), 404
        available = s["shares_outstanding"] - s["shares_held"]
        if shares > available:
            return jsonify({"error": f"Only {available} shares available"}), 400
        cost = round(s["current_price"] * shares, 4)
        if u["arm_bucks"] < cost:
            return jsonify({"error": f"Need ₳{cost:.2f}, you have ₳{u['arm_bucks']:.2f}"}), 400
        u["arm_bucks"]            -= cost
        u["portfolio"][ticker]     = u["portfolio"].get(ticker, 0) + shares
        s["shares_held"]          += shares
        s["total_volume"]         += shares
        s["current_price"]         = round(s["current_price"] * (1 + BUY_IMPACT) ** shares, 4)
        s["price_history"].append({"ts": now_iso(), "price": s["current_price"],
                                   "volume": shares, "type": "buy"})
        u["trade_history"].append({"ts": now_iso(), "type": "buy", "ticker": ticker,
                                   "shares": shares, "price": s["current_price"], "total": cost})
        save(market)
    return jsonify({"success": True, "new_price": s["current_price"],
                    "arm_bucks": round(u["arm_bucks"], 4),
                    "shares_owned": u["portfolio"][ticker]})

# ── Sell ──
@app.route("/api/sell", methods=["POST"])
def sell():
    body   = request.json or {}
    uid    = body.get("user_id", "")
    ticker = body.get("ticker", "").upper()
    shares = int(body.get("shares", 1))
    with lock:
        u = market["users"].get(uid)
        s = market["stocks"].get(ticker)
        if not u: return jsonify({"error": "User not found"}), 404
        if not s: return jsonify({"error": "Stock not found"}), 404
        owned = u["portfolio"].get(ticker, 0)
        if shares > owned:
            return jsonify({"error": f"You only own {owned} shares"}), 400
        proceeds               = round(s["current_price"] * shares, 4)
        u["arm_bucks"]        += proceeds
        u["portfolio"][ticker] = owned - shares
        s["shares_held"]       = max(0, s["shares_held"] - shares)
        s["total_volume"]     += shares
        s["current_price"]     = round(max(0.01, s["current_price"] * (1 - SELL_IMPACT) ** shares), 4)
        s["price_history"].append({"ts": now_iso(), "price": s["current_price"],
                                   "volume": shares, "type": "sell"})
        u["trade_history"].append({"ts": now_iso(), "type": "sell", "ticker": ticker,
                                   "shares": shares, "price": s["current_price"], "total": proceeds})
        save(market)
    return jsonify({"success": True, "new_price": s["current_price"],
                    "arm_bucks": round(u["arm_bucks"], 4),
                    "shares_owned": u["portfolio"][ticker]})

# ─────────────────────────── FUTURES ──────────────────────────

@app.route("/api/futures/buy", methods=["POST"])
def futures_buy():
    body      = request.json or {}
    uid       = body.get("user_id", "")
    ticker    = body.get("ticker", "").upper()
    direction = body.get("direction", "").upper()
    duration  = int(body.get("duration_minutes", 30))
    if direction not in ("UP", "DOWN"):
        return jsonify({"error": "Direction must be UP or DOWN"}), 400
    if duration not in FUTURES_DURATIONS:
        return jsonify({"error": f"Duration must be one of {FUTURES_DURATIONS} minutes"}), 400
    with lock:
        u = market["users"].get(uid)
        s = market["stocks"].get(ticker)
        if not u: return jsonify({"error": "User not found"}), 404
        if not s: return jsonify({"error": "Stock not found"}), 404
        _ensure_user_fields(u)
        if len([f for f in u["futures"] if f["status"] == "active"]) >= 10:
            return jsonify({"error": "Max 10 active contracts at a time"}), 400
        if u["arm_bucks"] < FUTURES_COST:
            return jsonify({"error": f"Need ₳{FUTURES_COST:.2f}, you have ₳{u['arm_bucks']:.2f}"}), 400
        expiry   = (datetime.utcnow() + timedelta(minutes=duration)).isoformat()
        contract = {
            "id":            str(uuid.uuid4()),
            "ticker":        ticker,
            "name":          s["name"],
            "direction":     direction,
            "entry_price":   round(s["current_price"], 4),
            "cost":          FUTURES_COST,
            "duration_min":  duration,
            "expires_at":    expiry,
            "status":        "active",
            "settled_price": None,
            "settled_at":    None,
            "payout":        None,
        }
        u["arm_bucks"] -= FUTURES_COST
        u["futures"].append(contract)
        save(market)
    return jsonify({"success": True, "contract": contract,
                    "arm_bucks": round(u["arm_bucks"], 4)})

@app.route("/api/futures/my-contracts")
def futures_my_contracts():
    uid = request.args.get("user_id", "")
    with lock:
        u = market["users"].get(uid)
        if not u:
            return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)
        now_ts   = datetime.utcnow()
        active   = [f for f in u["futures"] if f["status"] == "active"]
        settled  = sorted([f for f in u["futures"] if f["status"] != "active"],
                          key=lambda x: x.get("settled_at", ""), reverse=True)[:20]
        enriched = []
        for f in active:
            s         = market["stocks"].get(f["ticker"])
            expiry    = datetime.fromisoformat(f["expires_at"])
            secs_left = max(0, int((expiry - now_ts).total_seconds()))
            enriched.append({**f,
                "current_price": round(s["current_price"], 4) if s else None,
                "seconds_left":  secs_left,
            })
        return jsonify({"active": enriched, "settled": settled,
                        "cost": FUTURES_COST, "payout": FUTURES_PAYOUT,
                        "durations": FUTURES_DURATIONS})

# ─────────────────────────── PREDICTIONS ──────────────────────

@app.route("/api/predictions")
def get_predictions():
    """Return all predictions enriched with parimutuel odds and user's bet."""
    with lock:
        preds = market.get("predictions", [])
        uid   = request.args.get("user_id", "")
        u     = market["users"].get(uid)
        user_bets = {}
        if u:
            _ensure_user_fields(u)
            for b in u["prediction_bets"]:
                user_bets[b["prediction_id"]] = b

        out = []
        for p in reversed(preds):
            yes_mult, no_mult = pred_odds(p)
            out.append({
                **p,
                "yes_odds": yes_mult,   # e.g. 2.4 means bet ₳1, win ₳2.40
                "no_odds":  no_mult,
                "my_bet":   user_bets.get(p["id"]),
            })
        return jsonify(out)

@app.route("/api/predictions/bet", methods=["POST"])
def prediction_bet():
    """User places a YES or NO bet on an open prediction."""
    body    = request.json or {}
    uid     = body.get("user_id", "")
    pred_id = body.get("prediction_id", "")
    bet     = body.get("bet", "").upper()

    if bet not in ("YES", "NO"):
        return jsonify({"error": "Bet must be YES or NO"}), 400

    with lock:
        u    = market["users"].get(uid)
        pred = next((p for p in market.get("predictions", []) if p["id"] == pred_id), None)
        if not u:    return jsonify({"error": "User not found"}), 404
        if not pred: return jsonify({"error": "Prediction not found"}), 404
        if pred["status"] != "open":
            return jsonify({"error": "This prediction is already resolved"}), 400

        _ensure_user_fields(u)
        existing = next((b for b in u["prediction_bets"] if b["prediction_id"] == pred_id), None)
        if existing:
            return jsonify({"error": f"You already voted {existing['bet']} on this question"}), 400

        if u["arm_bucks"] < PREDICTION_COST:
            return jsonify({"error": f"Need ₳{PREDICTION_COST:.2f}, you have ₳{u['arm_bucks']:.2f}"}), 400

        u["arm_bucks"] -= PREDICTION_COST
        u["prediction_bets"].append({
            "prediction_id": pred_id,
            "question":      pred["question"],
            "bet":           bet,
            "amount":        PREDICTION_COST,
            "status":        "pending",
            "payout":        None,
            "placed_at":     now_iso(),
        })

        if bet == "YES":
            pred["yes_bets"] += 1
        else:
            pred["no_bets"] += 1
        pred["total_pot"] += PREDICTION_COST

        yes_mult, no_mult = pred_odds(pred)
        save(market)

    return jsonify({
        "success":   True,
        "arm_bucks": round(u["arm_bucks"], 4),
        "bet":       bet,
        "question":  pred["question"],
        "yes_odds":  yes_mult,
        "no_odds":   no_mult,
    })

@app.route("/api/admin/predictions/create", methods=["POST"])
def admin_create_prediction():
    body = request.json or {}
    if body.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "Wrong password"}), 403

    question = body.get("question", "").strip()
    if not question:
        return jsonify({"error": "Question text required"}), 400

    impacts_yes = {k.upper(): float(v) for k, v in body.get("impacts_yes", {}).items()}
    impacts_no  = {k.upper(): float(v) for k, v in body.get("impacts_no",  {}).items()}

    with lock:
        valid_tickers = set(market["stocks"].keys())
        bad = [t for t in list(impacts_yes.keys()) + list(impacts_no.keys()) if t not in valid_tickers]
        if bad:
            return jsonify({"error": f"Unknown tickers: {bad}"}), 400

        pred = {
            "id":          str(uuid.uuid4()),
            "question":    question,
            "created_at":  now_iso(),
            "status":      "open",
            "resolved_at": None,
            "resolved_by": None,
            "impacts_yes": impacts_yes,
            "impacts_no":  impacts_no,
            "yes_bets":    0,
            "no_bets":     0,
            "total_pot":   0.0,
        }
        market["predictions"].append(pred)
        save(market)

    return jsonify({"success": True, "prediction": pred})

@app.route("/api/admin/predictions/resolve", methods=["POST"])
def admin_resolve_prediction():
    """Admin resolves a prediction — parimutuel payout to winners."""
    body    = request.json or {}
    if body.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "Wrong password"}), 403

    pred_id = body.get("prediction_id", "")
    outcome = body.get("outcome", "").upper()

    if outcome not in ("YES", "NO"):
        return jsonify({"error": "Outcome must be YES or NO"}), 400

    with lock:
        pred = next((p for p in market.get("predictions", []) if p["id"] == pred_id), None)
        if not pred:
            return jsonify({"error": "Prediction not found"}), 404
        if pred["status"] != "open":
            return jsonify({"error": "Already resolved"}), 400

        # Apply stock impacts
        impacts = pred["impacts_yes"] if outcome == "YES" else pred["impacts_no"]
        for ticker, pct in impacts.items():
            s = market["stocks"].get(ticker)
            if s:
                s["current_price"] = round(max(0.01, s["current_price"] * (1 + pct / 100)), 4)
                s["price_history"].append({
                    "ts": now_iso(), "price": s["current_price"],
                    "volume": 0, "type": "prediction",
                    "event": f"Prediction resolved {outcome}: {pred['question'][:40]}"
                })

        # Parimutuel payout
        total_pot      = pred["yes_bets"] + pred["no_bets"]     # ₳1 per bet
        winning_count  = pred["yes_bets"] if outcome == "YES" else pred["no_bets"]
        losing_count   = pred["no_bets"]  if outcome == "YES" else pred["yes_bets"]

        if winning_count > 0 and losing_count > 0:
            payout_each = round(total_pot / winning_count, 4)   # true pool split
        elif winning_count > 0 and losing_count == 0:
            payout_each = PREDICTION_COST                        # no opposition — refund
        else:
            payout_each = 0.0                                    # no winners

        winners, losers = 0, 0
        for uid, u in market["users"].items():
            _ensure_user_fields(u)
            for b in u["prediction_bets"]:
                if b["prediction_id"] != pred_id or b["status"] != "pending":
                    continue
                if b["bet"] == outcome:
                    b["status"] = "won"
                    b["payout"] = payout_each
                    u["arm_bucks"] += payout_each
                    winners += 1
                else:
                    b["status"] = "lost"
                    b["payout"] = 0.0
                    losers += 1

        pred["status"]      = f"resolved_{outcome.lower()}"
        pred["resolved_at"] = now_iso()
        pred["resolved_by"] = outcome
        pred["payout_each"] = payout_each

        market["events"].append({
            "ts":         now_iso(),
            "admin_text": f"Prediction resolved: {pred['question']}",
            "summary":    f"PREDICTION {outcome}: {pred['question'][:50]}  (₳{payout_each:.2f}/winner)",
            "impacts":    impacts,
        })

        save(market)

    return jsonify({
        "success":     True,
        "outcome":     outcome,
        "payout_each": payout_each,
        "winners":     winners,
        "losers":      losers,
        "stock_impacts": impacts,
    })

# ─────────────────────────── MARKETPLACE ──────────────────────

@app.route("/api/marketplace/buy_pack", methods=["POST"])
def buy_pack():
    """Buy and open a booster pack for ₳5. Returns 5 card reveals."""
    body = request.json or {}
    uid  = body.get("user_id", "")

    with lock:
        u = market["users"].get(uid)
        if not u:
            return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)

        if u["arm_bucks"] < PACK_COST:
            return jsonify({"error": f"Need ₳{PACK_COST:.2f}, you have ₳{u['arm_bucks']:.2f}"}), 400

        u["arm_bucks"] -= PACK_COST
        cards = _generate_pack_cards(u)

        u["pack_history"].append({"ts": now_iso(), "cards": len(cards)})
        save(market)

    return jsonify({
        "success":   True,
        "cards":     cards,
        "arm_bucks": round(u["arm_bucks"], 4),
    })

@app.route("/api/spin", methods=["POST"])
def spin_wheel():
    """Spin the wheel for ₳2. Returns outcome + wheel landing angle for animation."""
    body = request.json or {}
    uid  = body.get("user_id", "")
    with lock:
        u = market["users"].get(uid)
        if not u:
            return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)
        if u["arm_bucks"] < WHEEL_COST:
            return jsonify({"error": f"Need ₳{WHEEL_COST:.2f} to spin, you have ₳{u['arm_bucks']:.2f}"}), 400

        outcome, land_angle, reward = _resolve_wheel_spin(u, free=False)

        # If spin_again — resolve a free second spin immediately
        spin2 = None
        if outcome == "spin_again":
            o2, a2, r2 = _resolve_wheel_spin(u, free=True)
            spin2 = {"outcome": o2, "land_angle": a2, "reward": r2}

        save(market)

    return jsonify({
        "success":    True,
        "outcome":    outcome,
        "land_angle": land_angle,
        "reward":     reward,
        "spin_again": spin2,
        "arm_bucks":  round(u["arm_bucks"], 4),
    })


@app.route("/api/marketplace/shinies")
def get_shinies():
    """Return all shiny types that have ever been pulled, with current prices."""
    uid = request.args.get("user_id", "")
    with lock:
        u   = market["users"].get(uid)
        if u: _ensure_user_fields(u)
        reg = market.get("shiny_registry", {})
        out = []
        for sk, info in reg.items():
            base = market["stocks"].get(info["base_ticker"])
            if not base: continue
            price = round(base["current_price"] * info["multiplier"], 4)
            owned = u["shiny_portfolio"].get(sk, 0) if u else 0
            out.append({
                **info,
                "current_price": price,
                "base_price":    round(base["current_price"], 4),
                "owned":         owned,
            })
        out.sort(key=lambda x: (["PLATINUM","GOLD","SILVER","BRONZE"].index(x["tier"]), x["name"]))
        return jsonify(out)

# buy_shiny and sell_shiny routes removed.
# Shiny cards are only obtainable via booster packs / spin the wheel.
# Trading is exclusively through the Shiny Resale Market (/api/shiny-market/*).

# ─────────────────────────── ETF FUNDS ───────────────────────

@app.route("/api/etf/overview")
def etf_overview():
    """Return all funds with current NAV, performance, and user holdings."""
    uid = request.args.get("user_id", "")
    with lock:
        u = market["users"].get(uid)
        if u: _ensure_user_fields(u)
        out = []
        for fund_id, fund in ETF_FUNDS.items():
            nav        = compute_etf_nav(fund_id)
            perf_pct   = round((nav / ETF_START_PRICE - 1) * 100, 2)
            units_held = u["etf_portfolio"].get(fund_id, 0) if u else 0
            holding_val= round(units_held * nav, 4)
            # Dividend rate scales with fund performance (capped)
            perf_ratio = nav / ETF_START_PRICE
            div_rate   = max(ETF_MIN_DIV_RATE, min(ETF_MAX_DIV_RATE,
                             ETF_BASE_DIV_RATE * (perf_ratio ** 0.5)))
            # Constituents with current prices
            tickers    = etf_constituent_tickers(fund_id)
            members_out = []
            for t in tickers:
                s = market["stocks"].get(t)
                if s:
                    members_out.append({
                        "ticker": t, "name": s["name"],
                        "image": s["image"],
                        "price": round(s["current_price"], 4),
                        "chg_pct": round((s["current_price"] - s["ipo_price"]) / s["ipo_price"] * 100, 2),
                    })
            out.append({
                "fund_id":       fund_id,
                "name":          fund["name"],
                "emoji":         fund["emoji"],
                "color":         fund["color"],
                "desc":          fund["desc"],
                "nav":           nav,
                "start_price":   ETF_START_PRICE,
                "perf_pct":      perf_pct,
                "div_rate_pct":  round(div_rate * 100, 3),
                "units_held":    units_held,
                "holding_value": holding_val,
                "members":       members_out,
            })
    return jsonify(out)

@app.route("/api/etf/buy", methods=["POST"])
def etf_buy():
    body    = request.json or {}
    uid     = body.get("user_id", "")
    fund_id = body.get("fund_id", "")
    units   = max(1, int(body.get("units", 1)))
    if fund_id not in ETF_FUNDS:
        return jsonify({"error": "Unknown fund"}), 400
    with lock:
        u = market["users"].get(uid)
        if not u: return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)
        nav  = compute_etf_nav(fund_id)
        cost = round(nav * units, 4)
        if u["arm_bucks"] < cost:
            return jsonify({"error": f"Need ₳{cost:.2f}, you have ₳{u['arm_bucks']:.2f}"}), 400
        u["arm_bucks"] -= cost
        u["etf_portfolio"][fund_id] = u["etf_portfolio"].get(fund_id, 0) + units
        save(market)
    return jsonify({"success": True, "units": units, "cost": cost,
                    "nav": nav, "arm_bucks": round(u["arm_bucks"], 4),
                    "units_held": u["etf_portfolio"][fund_id]})

@app.route("/api/etf/sell", methods=["POST"])
def etf_sell():
    body    = request.json or {}
    uid     = body.get("user_id", "")
    fund_id = body.get("fund_id", "")
    units   = max(1, int(body.get("units", 1)))
    if fund_id not in ETF_FUNDS:
        return jsonify({"error": "Unknown fund"}), 400
    with lock:
        u = market["users"].get(uid)
        if not u: return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)
        owned = u["etf_portfolio"].get(fund_id, 0)
        if units > owned:
            return jsonify({"error": f"You only hold {owned} units"}), 400
        nav      = compute_etf_nav(fund_id)
        proceeds = round(nav * units, 4)
        u["arm_bucks"] += proceeds
        u["etf_portfolio"][fund_id] -= units
        save(market)
    return jsonify({"success": True, "units": units, "proceeds": proceeds,
                    "nav": nav, "arm_bucks": round(u["arm_bucks"], 4),
                    "units_held": u["etf_portfolio"][fund_id]})

# ─────────────────────────── CHARTS ───────────────────────────

@app.route("/api/charts")
def get_charts():
    """Return normalised price history (% from IPO) for all stocks."""
    with lock:
        out = {}
        for ticker, s in market["stocks"].items():
            hist = s["price_history"][-60:]   # last 60 data points
            ipo  = s["ipo_price"]
            out[ticker] = {
                "name":   s["name"],
                "color":  "#00ff88" if s["current_price"] >= ipo else "#ff2255",
                "points": [round((p["price"] - ipo) / ipo * 100, 2) for p in hist],
            }
        return jsonify(out)

# ─────────────────────────── SHINY RESALE MARKET ──────────────

@app.route("/api/shiny-market")
def shiny_market_list():
    """Return all active listings with their offers."""
    uid = request.args.get("user_id", "")
    with lock:
        reg = market.get("shiny_registry", {})
        out = []
        for listing in market.get("shiny_listings", []):
            if listing["status"] != "active":
                continue
            base = market["stocks"].get(listing["base_ticker"])
            current_price = round(base["current_price"] * listing["multiplier"], 4) if base else None
            out.append({**listing, "current_value": current_price,
                        "is_mine": listing["seller_id"] == uid})
        return jsonify(out)

@app.route("/api/shiny-market/list", methods=["POST"])
def shiny_market_create_listing():
    """Seller lists a shiny card. Card is held in escrow until sold/delisted."""
    body        = request.json or {}
    uid         = body.get("user_id", "")
    shiny_key   = body.get("shiny_key", "")
    asking_price = float(body.get("asking_price", 0))
    if asking_price <= 0:
        return jsonify({"error": "Asking price must be > 0"}), 400
    with lock:
        u = market["users"].get(uid)
        if not u: return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)
        owned = u["shiny_portfolio"].get(shiny_key, 0)
        if owned < 1:
            return jsonify({"error": "You don't own this shiny card"}), 400
        info = market.get("shiny_registry", {}).get(shiny_key)
        if not info: return jsonify({"error": "Shiny type not found"}), 404
        # Check not already listed
        already = [l for l in market["shiny_listings"]
                   if l["shiny_key"] == shiny_key and l["seller_id"] == uid and l["status"] == "active"]
        if already:
            return jsonify({"error": "You already have an active listing for this card"}), 400
        # Move card to escrow (deduct from portfolio)
        u["shiny_portfolio"][shiny_key] -= 1
        listing = {
            "id":            str(uuid.uuid4()),
            "seller_id":     uid,
            "seller_name":   u["username"],
            "shiny_key":     shiny_key,
            "base_ticker":   info["base_ticker"],
            "name":          info["name"],
            "image":         info["image"],
            "tier":          info["tier"],
            "multiplier":    info["multiplier"],
            "asking_price":  round(asking_price, 2),
            "listed_at":     now_iso(),
            "status":        "active",
            "offers":        [],
        }
        market["shiny_listings"].append(listing)
        save(market)
    return jsonify({"success": True, "listing": listing})

@app.route("/api/shiny-market/delist", methods=["POST"])
def shiny_market_delist():
    """Seller removes their listing — card is returned from escrow."""
    body       = request.json or {}
    uid        = body.get("user_id", "")
    listing_id = body.get("listing_id", "")
    with lock:
        u = market["users"].get(uid)
        if not u: return jsonify({"error": "User not found"}), 404
        listing = next((l for l in market["shiny_listings"] if l["id"] == listing_id), None)
        if not listing: return jsonify({"error": "Listing not found"}), 404
        if listing["seller_id"] != uid: return jsonify({"error": "Not your listing"}), 403
        if listing["status"] != "active": return jsonify({"error": "Listing is not active"}), 400
        listing["status"] = "delisted"
        # Return card from escrow
        _ensure_user_fields(u)
        u["shiny_portfolio"][listing["shiny_key"]] = u["shiny_portfolio"].get(listing["shiny_key"], 0) + 1
        save(market)
    return jsonify({"success": True})

@app.route("/api/shiny-market/offer", methods=["POST"])
def shiny_market_offer():
    """Buyer makes an offer. Amount is locked immediately."""
    body       = request.json or {}
    uid        = body.get("user_id", "")
    listing_id = body.get("listing_id", "")
    amount     = float(body.get("amount", 0))
    if amount <= 0: return jsonify({"error": "Offer must be > 0"}), 400
    with lock:
        u = market["users"].get(uid)
        if not u: return jsonify({"error": "User not found"}), 404
        listing = next((l for l in market["shiny_listings"] if l["id"] == listing_id), None)
        if not listing: return jsonify({"error": "Listing not found"}), 404
        if listing["status"] != "active": return jsonify({"error": "Listing is no longer active"}), 400
        if listing["seller_id"] == uid: return jsonify({"error": "You can't offer on your own listing"}), 400
        # Check already has pending offer
        existing = next((o for o in listing["offers"] if o["buyer_id"] == uid and o["status"] == "pending"), None)
        if existing: return jsonify({"error": "You already have a pending offer on this listing"}), 400
        _ensure_user_fields(u)
        if u["arm_bucks"] < amount:
            return jsonify({"error": f"Need ₳{amount:.2f}, you have ₳{u['arm_bucks']:.2f}"}), 400
        u["arm_bucks"] -= amount   # lock funds
        offer = {
            "id":          str(uuid.uuid4()),
            "buyer_id":    uid,
            "buyer_name":  u["username"],
            "amount":      round(amount, 2),
            "offered_at":  now_iso(),
            "status":      "pending",
        }
        listing["offers"].append(offer)
        save(market)
    return jsonify({"success": True, "offer": offer, "arm_bucks": round(u["arm_bucks"], 4)})

@app.route("/api/shiny-market/accept", methods=["POST"])
def shiny_market_accept():
    """Seller accepts an offer — card transfers, funds released."""
    body       = request.json or {}
    uid        = body.get("user_id", "")
    listing_id = body.get("listing_id", "")
    offer_id   = body.get("offer_id", "")
    with lock:
        u = market["users"].get(uid)
        if not u: return jsonify({"error": "User not found"}), 404
        listing = next((l for l in market["shiny_listings"] if l["id"] == listing_id), None)
        if not listing: return jsonify({"error": "Listing not found"}), 404
        if listing["seller_id"] != uid: return jsonify({"error": "Not your listing"}), 403
        offer = next((o for o in listing["offers"] if o["id"] == offer_id), None)
        if not offer: return jsonify({"error": "Offer not found"}), 404
        if offer["status"] != "pending": return jsonify({"error": "Offer is no longer pending"}), 400
        buyer = market["users"].get(offer["buyer_id"])
        if not buyer: return jsonify({"error": "Buyer not found"}), 404
        _ensure_user_fields(buyer)
        # Transfer card to buyer
        sk = listing["shiny_key"]
        buyer["shiny_portfolio"][sk] = buyer["shiny_portfolio"].get(sk, 0) + 1
        # Pay seller (funds already locked from buyer)
        u["arm_bucks"] += offer["amount"]
        # Close listing and mark other offers declined (refund their locked funds)
        offer["status"] = "accepted"
        listing["status"] = "sold"
        for o in listing["offers"]:
            if o["id"] != offer_id and o["status"] == "pending":
                o["status"] = "declined"
                refund_buyer = market["users"].get(o["buyer_id"])
                if refund_buyer:
                    refund_buyer["arm_bucks"] += o["amount"]
        save(market)
    return jsonify({"success": True, "arm_bucks": round(u["arm_bucks"], 4)})

@app.route("/api/shiny-market/decline", methods=["POST"])
def shiny_market_decline():
    """Seller declines an offer — buyer's funds are returned."""
    body       = request.json or {}
    uid        = body.get("user_id", "")
    listing_id = body.get("listing_id", "")
    offer_id   = body.get("offer_id", "")
    with lock:
        u = market["users"].get(uid)
        if not u: return jsonify({"error": "User not found"}), 404
        listing = next((l for l in market["shiny_listings"] if l["id"] == listing_id), None)
        if not listing: return jsonify({"error": "Listing not found"}), 404
        if listing["seller_id"] != uid: return jsonify({"error": "Not your listing"}), 403
        offer = next((o for o in listing["offers"] if o["id"] == offer_id), None)
        if not offer or offer["status"] != "pending":
            return jsonify({"error": "Offer not found or not pending"}), 404
        offer["status"] = "declined"
        # Refund buyer
        buyer = market["users"].get(offer["buyer_id"])
        if buyer:
            buyer["arm_bucks"] += offer["amount"]
        save(market)
    return jsonify({"success": True})

# ─────────────────────────── SPOTIFY / RADIO ──────────────────

def _get_spotify_token():
    global _spotify_token, _spotify_token_expiry
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    if _spotify_token and time.time() < _spotify_token_expiry - 60:
        return _spotify_token
    try:
        import base64
        creds   = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
        resp    = http_requests.post("https://accounts.spotify.com/api/token",
                    data={"grant_type": "client_credentials"},
                    headers={"Authorization": f"Basic {creds}"}, timeout=8)
        data    = resp.json()
        _spotify_token        = data.get("access_token")
        _spotify_token_expiry = time.time() + data.get("expires_in", 3600)
        return _spotify_token
    except Exception as e:
        print(f"  [Spotify] token error: {e}")
        return None

@app.route("/api/radio/debug")
def radio_debug():
    """Public debug endpoint — shows config state without exposing secrets."""
    with lock:
        playlist_url = market.get("radio_playlist", "")
    has_id     = bool(SPOTIFY_CLIENT_ID)
    has_secret = bool(SPOTIFY_CLIENT_SECRET)
    token      = None
    token_err  = None
    if has_id and has_secret:
        try:
            token = _get_spotify_token()
        except Exception as e:
            token_err = str(e)
    pid = playlist_url.strip().split("/")[-1].split("?")[0] if playlist_url else ""
    # Try a quick playlist fetch if we have everything
    fetch_status = None
    if token and pid:
        try:
            resp = http_requests.get(
                f"https://api.spotify.com/v1/playlists/{pid}?fields=name,public,tracks(total)",
                headers={"Authorization": f"Bearer {token}"}, timeout=8)
            fetch_status = {"status_code": resp.status_code, "body": resp.json()}
        except Exception as e:
            fetch_status = {"error": str(e)}
    return jsonify({
        "spotify_client_id_set":     has_id,
        "spotify_client_secret_set": has_secret,
        "token_obtained":            bool(token),
        "token_error":               token_err,
        "playlist_url_saved":        playlist_url,
        "playlist_id_parsed":        pid,
        "playlist_fetch":            fetch_status,
    })

@app.route("/api/radio")
def get_radio():
    """Fetch tracks from the configured Spotify playlist."""
    with lock:
        playlist_url = market.get("radio_playlist", "")
    if not playlist_url:
        return jsonify({"error": "no_playlist", "tracks": []})
    # Extract playlist ID from URL
    pid = playlist_url.strip().split("/")[-1].split("?")[0]
    token = _get_spotify_token()
    if not token:
        return jsonify({"error": "no_spotify_credentials", "tracks": []})
    try:
        tracks_out = []
        url = f"https://api.spotify.com/v1/playlists/{pid}/tracks?limit=50"
        headers = {"Authorization": f"Bearer {token}"}
        while url and len(tracks_out) < 200:
            resp = http_requests.get(url, headers=headers, timeout=8)
            data = resp.json()
            for item in data.get("items", []):
                t = item.get("track")
                if not t: continue
                tracks_out.append({
                    "name":        t.get("name", ""),
                    "artist":      ", ".join(a["name"] for a in t.get("artists", [])),
                    "album":       t.get("album", {}).get("name", ""),
                    "image":       (t.get("album", {}).get("images") or [{}])[0].get("url", ""),
                    "preview_url": t.get("preview_url"),
                    "spotify_url": t.get("external_urls", {}).get("spotify", ""),
                    "duration_ms": t.get("duration_ms", 0),
                })
            url = data.get("next")
        return jsonify({"tracks": tracks_out, "playlist_url": playlist_url})
    except Exception as e:
        return jsonify({"error": str(e), "tracks": []})

@app.route("/api/admin/radio/set-playlist", methods=["POST"])
def admin_set_playlist():
    body = request.json or {}
    if body.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "Wrong password"}), 403
    url = body.get("playlist_url", "").strip()
    with lock:
        market["radio_playlist"] = url
        save(market)
    return jsonify({"success": True, "playlist_url": url})

# ─────────────────────────── ADMIN ────────────────────────────

@app.route("/api/admin/event", methods=["POST"])
def admin_event():
    body = request.json or {}
    if body.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "Wrong password"}), 403
    text      = body.get("event_text", "").strip()
    sentiment = body.get("sentiment", "auto").lower()   # "positive", "negative", or "auto"
    if sentiment not in ("positive", "negative", "auto"):
        sentiment = "auto"
    if not text:
        return jsonify({"error": "Event text required"}), 400
    with lock:
        snap = {k: dict(v) for k, v in market["stocks"].items()}
    impacts, summary = interpret_event(text, snap, sentiment)
    with lock:
        for ticker, pct in impacts.items():
            if ticker in market["stocks"]:
                s = market["stocks"][ticker]
                s["current_price"] = round(max(0.01, s["current_price"] * (1 + pct / 100)), 4)
                s["price_history"].append({"ts": now_iso(), "price": s["current_price"],
                                           "volume": 0, "type": "event", "event": summary})
        event = {
            "id":         str(uuid.uuid4()),
            "ts":         now_iso(),
            "admin_text": text,
            "summary":    summary,
            "sentiment":  sentiment,
            "impacts":    impacts,
            "recalled":   False,
        }
        market["events"].append(event)
        save(market)
    return jsonify({"success": True, "event": event})


@app.route("/api/admin/event/recall", methods=["POST"])
def admin_recall_event():
    """Remove an event from the news feed (price changes are not reversed)."""
    body     = request.json or {}
    if body.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "Wrong password"}), 403
    event_id = body.get("event_id", "")
    if not event_id:
        return jsonify({"error": "event_id required"}), 400
    with lock:
        original = len(market["events"])
        market["events"] = [e for e in market["events"] if e.get("id") != event_id]
        removed = original - len(market["events"])
        save(market)
    if removed == 0:
        return jsonify({"error": "Event not found"}), 404
    return jsonify({"success": True, "removed": removed})

@app.route("/api/admin/phase", methods=["POST"])
def admin_phase():
    body = request.json or {}
    if body.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "Wrong password"}), 403
    phase = body.get("phase", "")
    if phase not in ("ipo", "trading"):
        return jsonify({"error": "Phase must be 'ipo' or 'trading'"}), 400
    with lock:
        market["phase"] = phase
        save(market)
    return jsonify({"success": True, "phase": phase})

@app.route("/api/admin/set-balance", methods=["POST"])
def admin_set_balance():
    """Admin: manually set a user's armbucks balance (e.g. to restore after data loss)."""
    body = request.json or {}
    if body.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "Wrong password"}), 403
    username = body.get("username", "").strip()
    amount   = body.get("amount")
    if not username or amount is None:
        return jsonify({"error": "username and amount required"}), 400
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a number"}), 400
    with lock:
        uid = username.lower()
        if uid not in market["users"]:
            return jsonify({"error": f"User '{username}' not found"}), 404
        market["users"][uid]["armbucks"] = round(amount, 4)
        save(market)
    return jsonify({"success": True, "username": username, "armbucks": amount})

@app.route("/api/admin/reset", methods=["POST"])
def admin_reset():
    body = request.json or {}
    if body.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "Wrong password"}), 403
    global market
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    with lock:
        market = init_market()
    return jsonify({"success": True})

@app.route("/api/leaderboard")
def leaderboard():
    with lock:
        reg = market.get("shiny_registry", {})
        rows = []
        for uid, u in market["users"].items():
            pv = sum(
                u["portfolio"].get(t, 0) * market["stocks"][t]["current_price"]
                for t in u.get("portfolio", {}) if t in market["stocks"]
            )
            shiny_v = sum(
                u.get("shiny_portfolio", {}).get(sk, 0)
                * market["stocks"].get(reg[sk]["base_ticker"], {}).get("current_price", 0)
                * reg[sk]["multiplier"]
                for sk in u.get("shiny_portfolio", {})
                if sk in reg and reg[sk]["base_ticker"] in market["stocks"]
            )
            etf_v = sum(
                u.get("etf_portfolio", {}).get(fid, 0) * compute_etf_nav(fid)
                for fid in ETF_FUNDS
            )
            fut_locked  = sum(f["cost"]   for f in u.get("futures", [])         if f["status"] == "active")
            pred_locked = sum(b["amount"] for b in u.get("prediction_bets", []) if b["status"] == "pending")
            rows.append({
                "username":        u["username"],
                "arm_bucks":       round(u["arm_bucks"], 2),
                "portfolio_value": round(pv + shiny_v + etf_v, 2),
                "total":           round(u["arm_bucks"] + pv + shiny_v + etf_v + fut_locked + pred_locked, 2),
            })
        rows.sort(key=lambda x: x["total"], reverse=True)
    return jsonify(rows)

# ─────────────────────────── BACKGROUND THREAD ────────────────
def background_loop():
    global market
    while True:
        time.sleep(60)
        try:
            with lock:
                n = datetime.utcnow()

                # ── Futures settlement ──
                for uid, u in market["users"].items():
                    _ensure_user_fields(u)
                    for contract in u["futures"]:
                        if contract["status"] != "active":
                            continue
                        if n >= datetime.fromisoformat(contract["expires_at"]):
                            s   = market["stocks"].get(contract["ticker"])
                            if s:
                                cur  = s["current_price"]
                                won  = (contract["direction"] == "UP"   and cur > contract["entry_price"]) or \
                                       (contract["direction"] == "DOWN" and cur < contract["entry_price"])
                                contract["status"]        = "won" if won else "lost"
                                contract["settled_price"] = round(cur, 4)
                                contract["settled_at"]    = n.isoformat()
                                contract["payout"]        = FUTURES_PAYOUT if won else 0.0
                                if won:
                                    u["arm_bucks"] += FUTURES_PAYOUT
                    settled = [c for c in u["futures"] if c["status"] != "active"]
                    active  = [c for c in u["futures"] if c["status"] == "active"]
                    u["futures"] = active + settled[-30:]

                # ── Micro-fluctuation (every tick) ──
                for ticker, s in market["stocks"].items():
                    micro = random.gauss(0, 0.003)   # ±0.3% std per minute
                    s["current_price"] = round(max(0.01, s["current_price"] * (1 + micro)), 4)
                    s["price_history"].append({"ts": n.isoformat(),
                                               "price": s["current_price"],
                                               "volume": 0, "type": "micro"})

                # ── Larger random drift (every DRIFT_INTERVAL seconds) ──
                last_drift = datetime.fromisoformat(market.get("last_drift", n.isoformat()))
                if (n - last_drift).total_seconds() >= DRIFT_INTERVAL:
                    for ticker, s in market["stocks"].items():
                        drift = random.gauss(0, 0.010)
                        s["current_price"] = round(max(0.01, s["current_price"] * (1 + drift)), 4)
                        s["price_history"].append({"ts": n.isoformat(),
                                                   "price": s["current_price"],
                                                   "volume": 0, "type": "drift"})
                    market["last_drift"] = n.isoformat()

                # ── Stock & ETF Dividends (hourly) ──
                last_div = datetime.fromisoformat(market.get("last_dividend", n.isoformat()))
                if (n - last_div).total_seconds() >= DIV_INTERVAL:
                    for uid, u in market["users"].items():
                        _ensure_user_fields(u)
                        # Stock dividends (only on profitable stocks)
                        for ticker, shares in u.get("portfolio", {}).items():
                            if shares > 0 and ticker in market["stocks"]:
                                s = market["stocks"][ticker]
                                if s["current_price"] > s["ipo_price"]:
                                    u["arm_bucks"] += shares * s["current_price"] * 0.001
                        # ETF dividends — rate scales with fund performance
                        for fund_id, units in u.get("etf_portfolio", {}).items():
                            if units <= 0: continue
                            nav        = compute_etf_nav(fund_id)
                            perf_ratio = nav / ETF_START_PRICE
                            div_rate   = max(ETF_MIN_DIV_RATE, min(ETF_MAX_DIV_RATE,
                                             ETF_BASE_DIV_RATE * (perf_ratio ** 0.5)))
                            u["arm_bucks"] += round(units * nav * div_rate, 4)
                    market["last_dividend"] = n.isoformat()

                save(market)
        except Exception as e:
            print(f"  [BG] error: {e}")

# ─────────────────────────── STARTUP ──────────────────────────
market = init_market()
threading.Thread(target=background_loop, daemon=True).start()

# ─────────────────────────── MAIN ─────────────────────────────
if __name__ == "__main__":
    import socket
    try:
        host = socket.gethostbyname(socket.gethostname())
    except Exception:
        host = "localhost"
    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║   @RM!2T0CKS  —  EXCHANGE IS LIVE   ║")
    print("  ╚══════════════════════════════════════╝")
    print(f"  Local:    http://localhost:5000")
    print(f"  Network:  http://{host}:5000")
    print(f"  Admin pw: {ADMIN_PASSWORD}")
    print()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
