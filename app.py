"""
@RM!2T0CKS — The Office Stock Exchange
"""

from flask import Flask, request, jsonify, send_from_directory, render_template
import json, os, random, threading, time, uuid
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "armbets-neon-secret-2026"

# ─────────────────────────── CONFIG ───────────────────────────
# Serve headshots from static/headshots/ if it exists (web hosting),
# otherwise fall back to the parent folder (local use)
_static_hs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "headshots")
HEADSHOTS_DIR  = _static_hs if os.path.isdir(_static_hs) else \
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

DATA_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "market.json")
ADMIN_PASSWORD = "armbets"
IPO_PRICE      = 1.0
STARTING_BUCKS = 10.0
TOTAL_SHARES   = 1000
BUY_IMPACT     = 0.025
SELL_IMPACT    = 0.025
DRIFT_INTERVAL = 300        # seconds between random drift ticks
DIV_INTERVAL   = 3600       # seconds between dividend payouts

# ── Futures ──
FUTURES_COST      = 1.0     # ARM Bucks per contract
FUTURES_PAYOUT    = 1.80    # total returned if you win (profit = 0.80)
FUTURES_DURATIONS = [30, 60, 120]   # minutes

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

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

# ─────────────────────────── DATA I/O ─────────────────────────
def save(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

def load() -> dict | None:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                d = json.load(f)
                return d if d else None
        except Exception:
            return None
    return None

def init_market() -> dict:
    existing = load()
    if existing:
        return existing

    stocks, used = {}, set()
    for fname in sorted(os.listdir(HEADSHOTS_DIR)):
        ext = os.path.splitext(fname)[1].lower()
        if ext in IMAGE_EXTS:
            name   = os.path.splitext(fname)[0]
            ticker = make_ticker(name, used)
            stocks[ticker] = {
                "name":        name,
                "image":       fname,
                "ticker":      ticker,
                "ipo_price":   IPO_PRICE,
                "current_price": IPO_PRICE,
                "shares_outstanding": TOTAL_SHARES,
                "shares_held": 0,
                "total_volume": 0,
                "price_history": [
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
    }
    save(data)
    print(f"  → Initialised {len(stocks)} stocks from headshots")
    return data

def _ensure_user_futures(u):
    """Back-compat: add futures list to older user records."""
    if "futures" not in u:
        u["futures"] = []

# ─────────────────────────── GLOBAL STATE ─────────────────────
market = None
lock   = threading.Lock()

# ─────────────────────────── AI EVENT INTERPRETER ─────────────
def interpret_event(event_text: str, stocks: dict) -> tuple[dict, str]:
    names_map = {t: stocks[t]["name"] for t in stocks}
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
                    f"Event: \"{event_text}\"\n\n"
                    f"Determine which stocks are affected and by what % (-50 to +50). "
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
                impacts[ticker] = round(random.uniform(5, 30) * (1 if random.random() > 0.3 else -1), 1)
        if not impacts:
            t = random.choice(list(stocks.keys()))
            impacts[t] = round(random.uniform(-20, 30), 1)
        return impacts, f"MARKET UPDATE: {event_text[:55]}"

# ─────────────────────────── ROUTES ───────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/headshots/<path:filename>")
def serve_headshot(filename):
    return send_from_directory(os.path.abspath(HEADSHOTS_DIR), filename)

# ── User registration / lookup ──
@app.route("/api/register", methods=["POST"])
def register():
    body     = request.json or {}
    username = body.get("username", "").strip()
    if len(username) < 2:
        return jsonify({"error": "Username must be ≥ 2 characters"}), 400

    with lock:
        for uid, u in market["users"].items():
            if u["username"].lower() == username.lower():
                _ensure_user_futures(u)
                return jsonify({"user_id": uid, "username": u["username"],
                                "arm_bucks": u["arm_bucks"]})
        uid = str(uuid.uuid4())
        market["users"][uid] = {
            "username":      username,
            "arm_bucks":     STARTING_BUCKS,
            "portfolio":     {},
            "futures":       [],
            "joined":        now_iso(),
            "trade_history": [],
        }
        save(market)
    return jsonify({"user_id": uid, "username": username, "arm_bucks": STARTING_BUCKS})

# ── Full market snapshot ──
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
                "name":          s["name"],
                "image":         s["image"],
                "ticker":        ticker,
                "current_price": round(s["current_price"], 4),
                "ipo_price":     s["ipo_price"],
                "change":        round(chg, 4),
                "change_pct":    round(pct, 2),
                "shares_available": s["shares_outstanding"] - s["shares_held"],
                "total_volume":  s["total_volume"],
                "sparkline": [p["price"] for p in hist[-20:]],
            }
        return jsonify({
            "phase":         market["phase"],
            "stocks":        stocks_out,
            "recent_events": market["events"][-15:],
        })

# ── Individual stock detail ──
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
        _ensure_user_futures(u)
        holdings, port_val = [], 0.0
        for ticker, shares in u["portfolio"].items():
            s = market["stocks"].get(ticker)
            if s and shares > 0:
                val      = shares * s["current_price"]
                port_val += val
                holdings.append({
                    "ticker":         ticker,
                    "name":           s["name"],
                    "image":          s["image"],
                    "shares":         shares,
                    "current_price":  round(s["current_price"], 4),
                    "ipo_price":      s["ipo_price"],
                    "value":          round(val, 4),
                    "change_pct":     round((s["current_price"] - s["ipo_price"]) / s["ipo_price"] * 100, 2),
                })
        return jsonify({
            "username":        u["username"],
            "arm_bucks":       round(u["arm_bucks"], 4),
            "portfolio_value": round(port_val, 4),
            "total_value":     round(u["arm_bucks"] + port_val, 4),
            "holdings":        holdings,
            "active_futures":  len([f for f in u["futures"] if f["status"] == "active"]),
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
            return jsonify({"error": f"Need {cost:.2f} ARM Bucks, you have {u['arm_bucks']:.2f}"}), 400

        u["arm_bucks"] -= cost
        u["portfolio"][ticker] = u["portfolio"].get(ticker, 0) + shares
        s["shares_held"]  += shares
        s["total_volume"] += shares

        new_price = s["current_price"] * (1 + BUY_IMPACT) ** shares
        s["current_price"] = round(new_price, 4)
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

        proceeds = round(s["current_price"] * shares, 4)
        u["arm_bucks"] += proceeds
        u["portfolio"][ticker] = owned - shares
        s["shares_held"]  = max(0, s["shares_held"] - shares)
        s["total_volume"] += shares

        new_price = max(0.01, s["current_price"] * (1 - SELL_IMPACT) ** shares)
        s["current_price"] = round(new_price, 4)
        s["price_history"].append({"ts": now_iso(), "price": s["current_price"],
                                   "volume": shares, "type": "sell"})
        u["trade_history"].append({"ts": now_iso(), "type": "sell", "ticker": ticker,
                                   "shares": shares, "price": s["current_price"], "total": proceeds})
        save(market)

    return jsonify({"success": True, "new_price": s["current_price"],
                    "arm_bucks": round(u["arm_bucks"], 4),
                    "shares_owned": u["portfolio"][ticker]})

# ─────────────────────────── FUTURES ROUTES ───────────────────

@app.route("/api/futures/buy", methods=["POST"])
def futures_buy():
    body      = request.json or {}
    uid       = body.get("user_id", "")
    ticker    = body.get("ticker", "").upper()
    direction = body.get("direction", "").upper()   # "UP" or "DOWN"
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
        _ensure_user_futures(u)

        # Limit active contracts
        active = [f for f in u["futures"] if f["status"] == "active"]
        if len(active) >= 10:
            return jsonify({"error": "Max 10 active contracts at a time"}), 400

        if u["arm_bucks"] < FUTURES_COST:
            return jsonify({"error": f"Need ₳{FUTURES_COST:.2f}, you have ₳{u['arm_bucks']:.2f}"}), 400

        expiry = (datetime.utcnow() + timedelta(minutes=duration)).isoformat()
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

    return jsonify({
        "success":   True,
        "contract":  contract,
        "arm_bucks": round(u["arm_bucks"], 4),
    })

@app.route("/api/futures/my-contracts")
def futures_my_contracts():
    uid = request.args.get("user_id", "")
    with lock:
        u = market["users"].get(uid)
        if not u:
            return jsonify({"error": "User not found"}), 404
        _ensure_user_futures(u)
        now_ts = datetime.utcnow()
        active   = [f for f in u["futures"] if f["status"] == "active"]
        settled  = [f for f in u["futures"] if f["status"] != "active"]
        settled  = sorted(settled, key=lambda x: x.get("settled_at",""), reverse=True)[:20]

        # Enrich active with current price + seconds remaining
        enriched_active = []
        for f in active:
            s = market["stocks"].get(f["ticker"])
            expiry = datetime.fromisoformat(f["expires_at"])
            secs_left = max(0, int((expiry - now_ts).total_seconds()))
            enriched_active.append({**f,
                "current_price": round(s["current_price"], 4) if s else None,
                "seconds_left":  secs_left,
            })

        return jsonify({
            "active":  enriched_active,
            "settled": settled,
            "cost":    FUTURES_COST,
            "payout":  FUTURES_PAYOUT,
            "durations": FUTURES_DURATIONS,
        })

# ── Admin: fire an event ──
@app.route("/api/admin/event", methods=["POST"])
def admin_event():
    body = request.json or {}
    if body.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "Wrong password"}), 403
    text = body.get("event_text", "").strip()
    if not text:
        return jsonify({"error": "Event text required"}), 400

    with lock:
        snap = {k: dict(v) for k, v in market["stocks"].items()}

    impacts, summary = interpret_event(text, snap)

    with lock:
        for ticker, pct in impacts.items():
            if ticker in market["stocks"]:
                s = market["stocks"][ticker]
                mult = 1 + pct / 100
                s["current_price"] = round(max(0.01, s["current_price"] * mult), 4)
                s["price_history"].append({"ts": now_iso(), "price": s["current_price"],
                                           "volume": 0, "type": "event", "event": summary})
        event = {"ts": now_iso(), "admin_text": text, "summary": summary, "impacts": impacts}
        market["events"].append(event)
        save(market)

    return jsonify({"success": True, "event": event})

# ── Admin: change market phase ──
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

# ── Admin: reset market ──
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

# ── Leaderboard ──
@app.route("/api/leaderboard")
def leaderboard():
    with lock:
        rows = []
        for uid, u in market["users"].items():
            pv = sum(
                u["portfolio"].get(t, 0) * market["stocks"][t]["current_price"]
                for t in u.get("portfolio", {})
                if t in market["stocks"]
            )
            futures_locked = sum(
                f["cost"] for f in u.get("futures", []) if f["status"] == "active"
            )
            rows.append({
                "username":        u["username"],
                "arm_bucks":       round(u["arm_bucks"], 2),
                "portfolio_value": round(pv, 2),
                "futures_locked":  round(futures_locked, 2),
                "total":           round(u["arm_bucks"] + pv + futures_locked, 2),
            })
        rows.sort(key=lambda x: x["total"], reverse=True)
    return jsonify(rows)

# ─────────────────────────── BACKGROUND THREAD ────────────────
def background_loop():
    global market
    while True:
        time.sleep(60)   # tick every minute (drift + futures settlement)
        try:
            with lock:
                n = datetime.utcnow()

                # ── Futures settlement ──
                for uid, u in market["users"].items():
                    _ensure_user_futures(u)
                    newly_settled = False
                    for contract in u["futures"]:
                        if contract["status"] != "active":
                            continue
                        expiry = datetime.fromisoformat(contract["expires_at"])
                        if n >= expiry:
                            s = market["stocks"].get(contract["ticker"])
                            if s:
                                current = s["current_price"]
                                entry   = contract["entry_price"]
                                won = (contract["direction"] == "UP"  and current > entry) or \
                                      (contract["direction"] == "DOWN" and current < entry)
                                contract["status"]        = "won" if won else "lost"
                                contract["settled_price"] = round(current, 4)
                                contract["settled_at"]    = n.isoformat()
                                contract["payout"]        = FUTURES_PAYOUT if won else 0.0
                                if won:
                                    u["arm_bucks"] += FUTURES_PAYOUT
                                newly_settled = True

                    # Prune old settled contracts (keep last 30)
                    settled = [c for c in u["futures"] if c["status"] != "active"]
                    active  = [c for c in u["futures"] if c["status"] == "active"]
                    u["futures"] = active + settled[-30:]

                # ── Random price drift (every DRIFT_INTERVAL seconds) ──
                last_drift = datetime.fromisoformat(market.get("last_drift", n.isoformat()))
                if (n - last_drift).total_seconds() >= DRIFT_INTERVAL:
                    for ticker, s in market["stocks"].items():
                        drift = random.gauss(0, 0.012)
                        s["current_price"] = round(max(0.01, s["current_price"] * (1 + drift)), 4)
                        s["price_history"].append({"ts": n.isoformat(),
                                                   "price": s["current_price"],
                                                   "volume": 0, "type": "drift"})
                    market["last_drift"] = n.isoformat()

                # ── Dividends ──
                last_div = datetime.fromisoformat(market.get("last_dividend", n.isoformat()))
                if (n - last_div).total_seconds() >= DIV_INTERVAL:
                    for uid, u in market["users"].items():
                        for ticker, shares in u.get("portfolio", {}).items():
                            if shares > 0 and ticker in market["stocks"]:
                                s = market["stocks"][ticker]
                                if s["current_price"] > s["ipo_price"]:
                                    u["arm_bucks"] += shares * s["current_price"] * 0.001
                    market["last_dividend"] = n.isoformat()

                save(market)
        except Exception as e:
            print(f"  [BG] error: {e}")

# ─────────────────────────── MAIN ─────────────────────────────
if __name__ == "__main__":
    market = init_market()
    t = threading.Thread(target=background_loop, daemon=True)
    t.start()

    import socket
    host = socket.gethostbyname(socket.gethostname())
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
