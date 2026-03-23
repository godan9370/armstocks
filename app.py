"""
@RM!2T0CKS — The Office Stock Exchange
"""

from flask import Flask, request, jsonify, send_from_directory, render_template
import json, os, random, threading, time, uuid
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "armbets-neon-secret-2026"

# ─────────────────────────── CONFIG ───────────────────────────
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
    ("PLATINUM", 1000, 5.0),
    ("GOLD",      500, 4.0),
    ("SILVER",    250, 3.0),
    ("BRONZE",    100, 2.0),
]

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

def roll_shiny():
    """Returns (tier, multiplier) or None. Checked rarest-first."""
    for tier, denom, mult in SHINY_TIERS:
        if random.random() < 1.0 / denom:
            return (tier, mult)
    return None

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
def save(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

def load():
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
        if "predictions"    not in existing: existing["predictions"]    = []
        if "shiny_registry" not in existing: existing["shiny_registry"] = {}
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
    }
    save(data)
    print(f"  → Initialised {len(stocks)} stocks from headshots")
    return data

def _ensure_user_fields(u):
    if "futures"          not in u: u["futures"]          = []
    if "prediction_bets"  not in u: u["prediction_bets"]  = []
    if "shiny_portfolio"  not in u: u["shiny_portfolio"]  = {}
    if "pack_history"     not in u: u["pack_history"]     = []

# ─────────────────────────── GLOBAL STATE ─────────────────────
market = None
lock   = threading.Lock()

# ─────────────────────────── AI EVENT INTERPRETER ─────────────
def interpret_event(event_text, stocks):
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
        tickers = list(market["stocks"].keys())
        cards   = []

        for _ in range(PACK_SIZE):
            base_ticker = random.choice(tickers)
            base_stock  = market["stocks"][base_ticker]
            shiny_roll  = roll_shiny()

            if shiny_roll:
                tier, mult = shiny_roll
                shiny_key = f"{base_ticker}_{tier}"

                # Register this shiny type if it's new
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
                shiny_price_val = round(base_stock["current_price"] * mult, 4)

                cards.append({
                    "type":        "shiny",
                    "shiny_key":   shiny_key,
                    "base_ticker": base_ticker,
                    "name":        base_stock["name"],
                    "image":       base_stock["image"],
                    "tier":        tier,
                    "multiplier":  mult,
                    "base_price":  round(base_stock["current_price"], 4),
                    "shiny_price": shiny_price_val,
                })
            else:
                # Normal share — give 1 share, no market-impact (pack gift)
                u["portfolio"][base_ticker] = u["portfolio"].get(base_ticker, 0) + 1

                cards.append({
                    "type":    "normal",
                    "ticker":  base_ticker,
                    "name":    base_stock["name"],
                    "image":   base_stock["image"],
                    "price":   round(base_stock["current_price"], 4),
                })

        u["pack_history"].append({"ts": now_iso(), "cards": len(cards)})
        save(market)

    return jsonify({
        "success":   True,
        "cards":     cards,
        "arm_bucks": round(u["arm_bucks"], 4),
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

@app.route("/api/marketplace/buy_shiny", methods=["POST"])
def buy_shiny():
    """Buy a shiny stock from the market at current base × multiplier price."""
    body      = request.json or {}
    uid       = body.get("user_id", "")
    shiny_key = body.get("shiny_key", "")
    qty       = max(1, int(body.get("qty", 1)))

    with lock:
        u = market["users"].get(uid)
        if not u: return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)

        info = market.get("shiny_registry", {}).get(shiny_key)
        if not info: return jsonify({"error": "Shiny type not found (pull from a pack first)"}), 404

        base = market["stocks"].get(info["base_ticker"])
        if not base: return jsonify({"error": "Base stock not found"}), 404

        price      = round(base["current_price"] * info["multiplier"], 4)
        total_cost = round(price * qty, 4)

        if u["arm_bucks"] < total_cost:
            return jsonify({"error": f"Need ₳{total_cost:.2f}, you have ₳{u['arm_bucks']:.2f}"}), 400

        u["arm_bucks"] -= total_cost
        u["shiny_portfolio"][shiny_key] = u["shiny_portfolio"].get(shiny_key, 0) + qty
        save(market)

    return jsonify({
        "success":   True,
        "cost":      total_cost,
        "arm_bucks": round(u["arm_bucks"], 4),
    })

@app.route("/api/marketplace/sell_shiny", methods=["POST"])
def sell_shiny():
    """Sell a shiny stock back at current base × multiplier price."""
    body      = request.json or {}
    uid       = body.get("user_id", "")
    shiny_key = body.get("shiny_key", "")
    qty       = max(1, int(body.get("qty", 1)))

    with lock:
        u = market["users"].get(uid)
        if not u: return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)

        owned = u["shiny_portfolio"].get(shiny_key, 0)
        if qty > owned:
            return jsonify({"error": f"You only own {owned}"}), 400

        info = market.get("shiny_registry", {}).get(shiny_key)
        if not info: return jsonify({"error": "Shiny type not found"}), 404

        base = market["stocks"].get(info["base_ticker"])
        if not base: return jsonify({"error": "Base stock not found"}), 404

        price    = round(base["current_price"] * info["multiplier"], 4)
        proceeds = round(price * qty, 4)

        u["arm_bucks"] -= 0   # sanity
        u["arm_bucks"] += proceeds
        u["shiny_portfolio"][shiny_key] -= qty
        save(market)

    return jsonify({
        "success":   True,
        "proceeds":  proceeds,
        "arm_bucks": round(u["arm_bucks"], 4),
    })

# ─────────────────────────── ADMIN ────────────────────────────

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
                s["current_price"] = round(max(0.01, s["current_price"] * (1 + pct / 100)), 4)
                s["price_history"].append({"ts": now_iso(), "price": s["current_price"],
                                           "volume": 0, "type": "event", "event": summary})
        event = {"ts": now_iso(), "admin_text": text, "summary": summary, "impacts": impacts}
        market["events"].append(event)
        save(market)
    return jsonify({"success": True, "event": event})

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
            fut_locked  = sum(f["cost"]   for f in u.get("futures", [])         if f["status"] == "active")
            pred_locked = sum(b["amount"] for b in u.get("prediction_bets", []) if b["status"] == "pending")
            rows.append({
                "username":        u["username"],
                "arm_bucks":       round(u["arm_bucks"], 2),
                "portfolio_value": round(pv + shiny_v, 2),
                "total":           round(u["arm_bucks"] + pv + shiny_v + fut_locked + pred_locked, 2),
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

                # ── Random price drift ──
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
