"""
@RM!2T0CKS — The Office Stock Exchange
"""

from flask import Flask, request, jsonify, send_from_directory, render_template
import atexit, json, os, random, signal, sys, threading, time, uuid, requests as http_requests
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

# ── Card Battle — Seniority HP tiers ──
# Maps lowercase name fragments to HP values
SENIORITY_HP = {
    # Directors / C-Suite (30 HP)
    "andrew hayne": 30, "andrew lilleyman": 30, "mark": 30, "jesse": 30,
    "howard": 30, "ian": 30, "lucy": 30, "nesbubu": 30,
    # Associate / Design Director (25 HP)
    "amber": 25, "neil": 25,
    # Principal (20 HP)
    "joshua": 20, "philippe": 20, "rhonda": 20, "aaron": 20, "katherine": 20,
    "andrea": 20, "jenny": 20, "jeremy": 20, "ray": 20, "caroline": 20,
    # General Staff (15 HP)
    "ark": 15, "cecilia": 15, "nessie": 15, "chris": 15, "hannah": 15,
    "dylan": 15, "eliza": 15, "jason": 15, "jawad": 15, "liam": 15, "tom jones": 15,
    # Graduate / Student (10 HP)
    "anita": 10, "nadia": 10, "bella": 10, "raman": 10, "ryan": 10, "renae": 10, "zihe": 10,
    # Support / Admin (5 HP)
    "svetlana": 5, "rhiannon": 5, "meg": 5, "dan": 5, "axeris": 5, "kirstin": 5,
    "tanya king": 5, "katherine chair": 5,
}
SENIORITY_TIER = {
    # Directors
    "andrew hayne": "director", "andrew lilleyman": "director", "mark": "director",
    "jesse": "director", "howard": "director", "ian": "director",
    "lucy": "director", "nesbubu": "director",
    # Associate Director
    "amber": "assoc_director", "neil": "assoc_director",
    # Principal
    "joshua": "principal", "philippe": "principal", "rhonda": "principal",
    "aaron": "principal", "katherine": "principal", "andrea": "principal",
    "jenny": "principal", "jeremy": "principal", "ray": "principal", "caroline": "principal",
    # General Staff
    "ark": "general", "cecilia": "general", "nessie": "general", "chris": "general",
    "hannah": "general", "dylan": "general", "eliza": "general", "jason": "general",
    "jawad": "general", "liam": "general", "tom jones": "general",
    # Graduate
    "anita": "graduate", "nadia": "graduate", "bella": "graduate", "raman": "graduate",
    "ryan": "graduate", "renae": "graduate", "zihe": "graduate",
    # Support/Admin
    "svetlana": "support", "rhiannon": "support", "meg": "support", "dan": "support",
    "axeris": "support", "kirstin": "support", "tanya king": "support",
    "katherine chair": "support",
}
BATTLE_DECK_SIZE     = 10
BATTLE_START_HAND    = 5
BATTLE_MAX_BOARD     = 5
BATTLE_MAX_DIRECTORS = 2        # max director-tier cards per deck
BATTLE_GAME_NAME     = "Sunday Best Good Battles"

# ── Special card effects (by name fragment, lowercase) ──
# Each entry: name_fragment → effect_key
CARD_SPECIAL_EFFECTS = {
    "tanya king":       "tanya_early_exit",     # removed after turn 3
    "mark":             "mark_jesse_chaos",      # 30% friendly-fire with Jesse
    "jesse":            "mark_jesse_chaos",      # same
    "lucy":             "lucy_aura",             # admin cards lose 5 HP while Lucy on board
    "jenny":            "jenny_tea",             # heals friendly board +3 HP on play
    "renae":            "renae_prepared",        # no summoning sickness
    "anita":            "anita_good_vibes",      # start-of-turn: random friendly card +1 HP
    "bella":            "bella_enthusiasm",      # +1 bonus damage while on board
    "nadia":            "nadia_eager",           # draw 1 extra card on play
    "ray":              "ray_slow",              # summoning sickness lasts 2 turns
    "tom jones":        "tom_jones_meeting",     # 20% chance can't attack (in a meeting)
    "ark":              "ark_self_damage",       # takes 1 self-damage after attacking
    "raman":            "raman_chaos",           # random good/bad effect each turn
}

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


def _battle_get_hp(name):
    """Return HP for a stock name based on seniority."""
    nl = name.lower().strip()
    for key, hp in SENIORITY_HP.items():
        if key in nl or nl in key:
            return hp
    return 15   # default General Staff

def _battle_get_tier(name):
    nl = name.lower().strip()
    for key, tier in SENIORITY_TIER.items():
        if key in nl or nl in key:
            return tier
    return "general"

def _battle_get_effect(name):
    """Return effect_key for a card name, or None."""
    nl = name.lower().strip()
    for fragment, effect in CARD_SPECIAL_EFFECTS.items():
        if fragment in nl or nl in fragment:
            return effect
    return None

def _battle_make_card(ticker, stock, shiny_key=None, shiny_info=None):
    """Build a battle card dict from a stock and optional shiny info."""
    multiplier = 1.0
    tier_label = "NORMAL"
    if shiny_info:
        multiplier = shiny_info.get("multiplier", 1.0)
        tier_label = shiny_info.get("tier", "NORMAL")
    atk = round(stock["current_price"] * multiplier, 2)
    max_hp = _battle_get_hp(stock["name"])
    seniority = _battle_get_tier(stock["name"])
    effect    = _battle_get_effect(stock["name"])
    # Ray has double summoning sickness
    sick_turns = 2 if effect == "ray_slow" else 1
    return {
        "id":            str(uuid.uuid4()),
        "ticker":        ticker,
        "name":          stock["name"],
        "image":         stock["image"],
        "tier":          tier_label,
        "shiny_key":     shiny_key,
        "multiplier":    multiplier,
        "max_hp":        max_hp,
        "hp":            max_hp,
        "atk":           atk,
        "seniority":     seniority,
        "effect":        effect,       # special effect key or None
        "sick":          False,        # summoning sickness — set True when played to board
        "sick_turns":    sick_turns,   # how many end-of-turns before sick clears
        "ability_used":  False,        # for support heal — once per turn
        "in_meeting":    False,        # Tom Jones: set True 20% turns to block attack
    }

def _battle_notify(user, msg, battle_id, notif_type="info"):
    _ensure_user_fields(user)
    user["notifications"].append({
        "id":        str(uuid.uuid4()),
        "type":      notif_type,
        "battle_id": battle_id,
        "msg":       msg,
        "ts":        now_iso(),
        "read":      False,
    })
    # Keep only last 30 notifications
    user["notifications"] = user["notifications"][-30:]

def _battle_check_win(battle):
    """Check if battle is over. Returns winning player key ('challenger'/'responder') or None."""
    for side in ("challenger", "responder"):
        s = battle[side]
        if len(s["board"]) == 0 and len(s["hand"]) == 0 and len(s["deck"]) == 0:
            other = "responder" if side == "challenger" else "challenger"
            return other
    return None

def _battle_apply_director_aura(battle, side):
    """If a Director card is on the board, deal 1 damage to all friendly board cards."""
    s = battle[side]
    has_director = any(c["seniority"] == "director" for c in s["board"])
    if not has_director:
        return []
    log_msgs = []
    dead = []
    for card in s["board"]:
        card["hp"] = max(0, card["hp"] - 1)
        if card["hp"] == 0:
            dead.append(card)
    if dead:
        log_msgs.append(f"Director aura: {', '.join(c['name'] for c in dead)} perished from the aura!")
        s["board"] = [c for c in s["board"] if c["hp"] > 0]
    elif has_director:
        log_msgs.append(f"Director aura deals 1 damage to all {side} cards.")
    return log_msgs

def _battle_start_turn(battle):
    """Apply start-of-turn effects: director aura, draw card, special card effects."""
    logs = []
    side = "challenger" if battle["current_turn"] == battle["challenger_id"] else "responder"
    s    = battle[side]
    turn = battle.get("turn_number", 0)

    # ── Tanya King early exit (all boards) ──
    for bside in ("challenger", "responder"):
        bs = battle[bside]
        gone = [c for c in bs["board"] if c.get("effect") == "tanya_early_exit" and turn >= 3]
        if gone:
            bs["board"] = [c for c in bs["board"] if c not in gone]
            for g in gone:
                logs.append(f"🚪 {g['name']} has gone home early — she's only available during turns 1-3.")
        # Also purge from hand/deck silently (she can't even be played)
        bs["hand"] = [c for c in bs["hand"]  if not (c.get("effect") == "tanya_early_exit" and turn >= 3)]
        bs["deck"] = [c for c in bs["deck"]  if not (c.get("effect") == "tanya_early_exit" and turn >= 3)]

    # ── Director aura ──
    logs += _battle_apply_director_aura(battle, side)

    # ── Tom Jones: 20% chance 'in a meeting' ──
    for card in s["board"]:
        if card.get("effect") == "tom_jones_meeting":
            card["in_meeting"] = random.random() < 0.20
            if card["in_meeting"]:
                logs.append(f"📋 {card['name']} is in a meeting this turn and can't attack.")

    # ── Anita good vibes: random friendly card gains +1 HP ──
    anita_cards = [c for c in s["board"] if c.get("effect") == "anita_good_vibes"]
    if anita_cards:
        candidates = [c for c in s["board"] if c["hp"] < c["max_hp"]]
        if candidates:
            target = random.choice(candidates)
            target["hp"] = min(target["max_hp"], target["hp"] + 1)
            logs.append(f"✨ Anita's good vibes restore 1 HP to {target['name']} ({target['hp']}/{target['max_hp']} HP).")

    # ── Raman chaos: random good OR bad effect ──
    raman_cards = [c for c in s["board"] if c.get("effect") == "raman_chaos"]
    for raman in raman_cards:
        roll = random.random()
        chaos_options = [
            ("good", "heal_all"),
            ("good", "atk_boost"),
            ("good", "draw_card"),
            ("bad",  "self_damage"),
            ("bad",  "atk_drop"),
            ("bad",  "hit_random_friendly"),
        ]
        kind, effect_type = random.choice(chaos_options)
        if effect_type == "heal_all":
            healed = 0
            for c in s["board"]:
                if c["hp"] < c["max_hp"]:
                    c["hp"] = min(c["max_hp"], c["hp"] + 3)
                    healed += 1
            logs.append(f"🎲 Raman energy: GOOD — healed all friendly cards +3 HP! (healed {healed} cards)")
        elif effect_type == "atk_boost":
            raman["atk"] = round(raman["atk"] + 2, 2)
            logs.append(f"🎲 Raman energy: GOOD — Raman's ATK jumps to {raman['atk']}!")
        elif effect_type == "draw_card":
            if s["deck"]:
                drawn = s["deck"].pop(0)
                s["hand"].append(drawn)
                logs.append(f"🎲 Raman energy: GOOD — Raman draws {drawn['name']} for free!")
            else:
                logs.append(f"🎲 Raman energy: GOOD — Raman tries to draw but deck is empty!")
        elif effect_type == "self_damage":
            raman["hp"] = max(0, raman["hp"] - 3)
            logs.append(f"🎲 Raman energy: CHAOS — Raman takes 3 self-damage! ({raman['hp']} HP left)")
        elif effect_type == "atk_drop":
            raman["atk"] = max(0.1, round(raman["atk"] - 1, 2))
            logs.append(f"🎲 Raman energy: CHAOS — Raman's ATK drops to {raman['atk']}!")
        elif effect_type == "hit_random_friendly":
            victims = [c for c in s["board"] if c["id"] != raman["id"]]
            if victims:
                victim = random.choice(victims)
                victim["hp"] = max(0, victim["hp"] - 2)
                logs.append(f"🎲 Raman energy: CHAOS — Raman accidentally hits {victim['name']} for 2 damage! ({victim['hp']} HP left)")
            else:
                logs.append(f"🎲 Raman energy: CHAOS — Raman swings wildly but misses!")
        # Remove dead cards from raman chaos
        s["board"] = [c for c in s["board"] if c["hp"] > 0]

    # ── Draw 1 card ──
    if s["deck"]:
        drawn = s["deck"].pop(0)
        s["hand"].append(drawn)
        logs.append(f"{battle[side + '_name']} draws {drawn['name']}.")

    battle["log"].extend(logs)

def _battle_end_turn(battle):
    """Remove summoning sickness from board cards (respecting Ray's 2-turn sick), switch turn."""
    side = "challenger" if battle["current_turn"] == battle["challenger_id"] else "responder"
    s = battle[side]
    for card in s["board"]:
        if card.get("sick"):
            # Decrement sick_turns counter; clear sick only when it reaches 0
            remaining = card.get("sick_turns", 1) - 1
            card["sick_turns"] = remaining
            if remaining <= 0:
                card["sick"] = False
                if card.get("effect") == "ray_slow":
                    battle["log"].append(f"🐢 {card['name']} is finally ready to act.")
            else:
                if card.get("effect") == "ray_slow":
                    battle["log"].append(f"🐢 {card['name']} is still getting settled ({remaining} turns of summoning sickness left).")
        card["ability_used"] = False
        card["in_meeting"]   = False
    battle["turn_number"] += 1
    # Switch turn
    if battle["current_turn"] == battle["challenger_id"]:
        battle["current_turn"] = battle["responder_id"]
    else:
        battle["current_turn"] = battle["challenger_id"]
    # Apply start-of-turn for new active player
    _battle_start_turn(battle)


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
def _get_db_conn():
    """Return a psycopg2 connection if DATABASE_URL is set, else None."""
    if not _DATABASE_URL:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(_DATABASE_URL)
        return conn
    except Exception as e:
        print(f"  [DB] connect error: {e}")
        return None

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
    # SAFETY: never persist a stub/unavailable market — would wipe real DB data
    if data.get("_db_unavailable"):
        print("  [DB] save SKIPPED — market is DB_UNAVAILABLE stub")
        return
    payload = json.dumps(data, default=str)
    n_users = len(data.get('users', {}))
    # Sample first user's arm_bucks for read-back verification
    sample_uid, sample_bucks = None, None
    for uid, u in data.get('users', {}).items():
        sample_uid   = uid
        sample_bucks = round(u.get('arm_bucks', 0), 4)
        break
    conn = _get_db_conn()
    if conn:
        try:
            _db_init_table(conn)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO market_state (id, data) VALUES (1, %s)
                ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data;
            """, (payload,))
            conn.commit()
            cur.close()
            # ── Read-back verification ──
            try:
                vcur = conn.cursor()
                vcur.execute("SELECT data FROM market_state WHERE id = 1;")
                vrow = vcur.fetchone()
                vcur.close()
                if vrow:
                    vdata   = json.loads(vrow[0])
                    vbucks  = round(vdata.get('users', {}).get(sample_uid, {}).get('arm_bucks', -1), 4) if sample_uid else '?'
                    match   = "✓ MATCH" if vbucks == sample_bucks else f"✗ MISMATCH (wrote {sample_bucks}, read back {vbucks})"
                    print(f"  [DB] save OK — users={n_users}  verify={match}")
                else:
                    print(f"  [DB] save OK — users={n_users}  verify=NO_ROW_FOUND")
            except Exception as ve:
                print(f"  [DB] save OK — users={n_users}  verify-read error: {ve}")
        except Exception as e:
            print(f"  [DB] save ERROR: {e}")
        finally:
            conn.close()
    elif not _DATABASE_URL:
        # Local dev only — JSON file fallback
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w") as f:
            f.write(payload)
    else:
        print("  [DB] WARNING: DATABASE_URL set but connection failed — save skipped to avoid data loss")

def load():
    if not _DATABASE_URL:
        # Local dev only — JSON file fallback
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as f:
                    d = json.load(f)
                    return d if d else None
            except Exception:
                return None
        return None

    # DATABASE_URL is set — always try DB, never fall back to JSON
    conn = _get_db_conn()
    if not conn:
        # Could not connect at all — return sentinel to prevent fresh-market overwrite
        print("  [DB] WARNING: DATABASE_URL set but connection failed — will not initialise fresh market")
        return "DB_UNAVAILABLE"

    try:
        _db_init_table(conn)
        cur = conn.cursor()
        cur.execute("SELECT data FROM market_state WHERE id = 1;")
        row = cur.fetchone()
        cur.close()
        return json.loads(row[0]) if row else None
    except Exception as e:
        # Connected but query failed — treat as unavailable to prevent overwrite
        print(f"  [DB] load error (query failed): {e} — returning DB_UNAVAILABLE sentinel")
        return "DB_UNAVAILABLE"
    finally:
        conn.close()

def init_market():
    # Retry loading up to 5 times — Render free tier DB can take a few seconds
    # to accept connections after a cold deploy. Without retries, a brief hiccup
    # means load() returns DB_UNAVAILABLE and init wipes real data with a fresh market.
    existing = None
    if _DATABASE_URL:
        for attempt in range(1, 6):
            result = load()
            if result != "DB_UNAVAILABLE":
                existing = result
                print(f"  [DB] load succeeded on attempt {attempt}")
                break
            print(f"  [DB] load attempt {attempt}/5 failed — retrying in 3s…")
            time.sleep(3)
        else:
            # All 5 attempts failed
            print("  [DB] All load attempts failed — starting with empty stub. DO NOT overwrite DB.")
            return {
                "phase": "trading", "created": now_iso(), "last_drift": now_iso(),
                "last_dividend": now_iso(), "users": {}, "stocks": {}, "events": [],
                "predictions": [], "shiny_registry": {}, "shiny_listings": [],
                "radio_playlist": "", "etf_dividends_paid": 0.0,
                "_db_unavailable": True,
            }
    else:
        existing = load()   # local dev JSON path

    if existing == "DB_UNAVAILABLE":
        # Shouldn't reach here after retry loop, but guard anyway
        print("  [DB] Starting with empty stub — DB unavailable at startup. DO NOT overwrite DB.")
        return {
            "phase": "trading", "created": now_iso(), "last_drift": now_iso(),
            "last_dividend": now_iso(), "users": {}, "stocks": {}, "events": [],
            "predictions": [], "shiny_registry": {}, "shiny_listings": [],
            "radio_playlist": "", "etf_dividends_paid": 0.0,
            "_db_unavailable": True,
        }
    if existing:
        # Strip any accidentally-persisted _db_unavailable flag
        existing.pop("_db_unavailable", None)
        # Back-compat: add new top-level keys if missing
        new_keys_added = False
        for key, default in [
            ("predictions",        []),
            ("shiny_registry",     {}),
            ("etf_dividends_paid", 0.0),
            ("shiny_listings",     []),
            ("battles",            []),
            ("radio_playlist",     ""),
        ]:
            if key not in existing:
                existing[key] = default
                new_keys_added = True
        # Force trading phase — IPO phase is removed
        existing["phase"] = "trading"
        # Only save back to DB if we actually added new keys — avoids racing with
        # the old instance which may still be serving requests during a blue-green deploy
        if new_keys_added:
            print("  [DB] Back-compat keys added — saving updated schema to DB")
            save(existing)
        else:
            print("  [DB] No schema changes — skipping startup save to preserve in-flight writes")
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
        "phase":          "trading",
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
        "battles":        [],   # card battle games
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
    if "notifications"    not in u: u["notifications"]    = []   # battle notifications

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

@app.route("/api/db-status")
def db_status():
    """Diagnostic: test DB connection and show what's stored."""
    result = {"database_url_set": bool(_DATABASE_URL), "connected": False, "has_data": False, "user_count": 0, "error": None}
    if not _DATABASE_URL:
        result["error"] = "DATABASE_URL not set — using local JSON file"
        return jsonify(result)
    try:
        conn = _get_db_conn()
        if not conn:
            result["error"] = "Connection returned None"
            return jsonify(result)
        result["connected"] = True
        _db_init_table(conn)
        cur = conn.cursor()
        cur.execute("SELECT data FROM market_state WHERE id = 1;")
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            result["has_data"] = True
            data = json.loads(row[0])
            result["user_count"] = len(data.get("users", {}))
            result["phase"] = data.get("phase")
            result["users"] = {
                uid: {"name": u.get("name"), "armbucks": round(u.get("arm_bucks", 0), 2)}
                for uid, u in data.get("users", {}).items()
            }
        else:
            result["error"] = "No row in DB yet — market not saved"
    except Exception as e:
        result["error"] = str(e)
    return jsonify(result)

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
    """Return the configured Spotify playlist URL for frontend embedding."""
    with lock:
        playlist_url = market.get("radio_playlist", "")
    if not playlist_url:
        return jsonify({"error": "no_playlist", "playlist_url": ""})
    pid = playlist_url.strip().split("/")[-1].split("?")[0]
    embed_url = f"https://open.spotify.com/embed/playlist/{pid}?utm_source=generator&theme=0"
    return jsonify({"playlist_url": playlist_url, "embed_url": embed_url, "pid": pid})

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
            total_cards = sum(u.get("portfolio", {}).values()) + sum(u.get("shiny_portfolio", {}).values())
            rows.append({
                "username":        u["username"],
                "arm_bucks":       round(u["arm_bucks"], 2),
                "portfolio_value": round(pv + shiny_v + etf_v, 2),
                "total":           round(u["arm_bucks"] + pv + shiny_v + etf_v + fut_locked + pred_locked, 2),
                "total_cards":     int(total_cards),
            })
        rows.sort(key=lambda x: x["total"], reverse=True)
    return jsonify(rows)

# ═══════════════════════════════════════════════════════════════
#  CARD BATTLE
# ═══════════════════════════════════════════════════════════════

@app.route("/api/battle/list")
def battle_list():
    uid = request.args.get("user_id", "")
    with lock:
        u = market["users"].get(uid)
        if not u:
            return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)
        battles = market.get("battles", [])
        my_battles = [b for b in battles if b["challenger_id"] == uid or b["responder_id"] == uid]
        # Return minimal info for listing
        out = []
        for b in reversed(my_battles[-50:]):
            my_side  = "challenger" if b["challenger_id"] == uid else "responder"
            opp_side = "responder"  if my_side == "challenger" else "challenger"
            out.append({
                "id":             b["id"],
                "status":         b["status"],
                "opponent":       b[opp_side + "_name"],
                "bet":            b["bet"],
                "current_turn":   b["current_turn"],
                "my_turn":        b["current_turn"] == uid and b["status"] == "active",
                "my_side":        my_side,
                "winner_id":      b.get("winner_id"),
                "i_won":          b.get("winner_id") == uid,
                "created_at":     b.get("created_at", ""),
                "deck_ready":     b.get(my_side + "_deck_ready", False),
                "turn_number":    b.get("turn_number", 0),
            })
        # Unread notification count
        notif_count = sum(1 for n in u["notifications"] if not n["read"])
        return jsonify({"battles": out, "notif_count": notif_count})


@app.route("/api/battle/state")
def battle_state():
    uid       = request.args.get("user_id", "")
    battle_id = request.args.get("battle_id", "")
    with lock:
        battle = next((b for b in market.get("battles", []) if b["id"] == battle_id), None)
        if not battle:
            return jsonify({"error": "Battle not found"}), 404
        if uid not in (battle["challenger_id"], battle["responder_id"]):
            return jsonify({"error": "Not your battle"}), 403
        my_side  = "challenger" if battle["challenger_id"] == uid else "responder"
        opp_side = "responder"  if my_side == "challenger" else "challenger"
        # Hide opponent hand (show count only)
        opp_state = battle[opp_side]
        my_state  = battle[my_side]
        return jsonify({
            "id":              battle["id"],
            "status":          battle["status"],
            "my_side":         my_side,
            "my_name":         battle[my_side + "_name"],
            "opp_name":        battle[opp_side + "_name"],
            "bet":             battle["bet"],
            "current_turn":    battle["current_turn"],
            "my_turn":         battle["current_turn"] == uid and battle["status"] == "active",
            "turn_number":     battle.get("turn_number", 0),
            "winner_id":       battle.get("winner_id"),
            "i_won":           battle.get("winner_id") == uid,
            "my_hand":         my_state["hand"],
            "my_board":        my_state["board"],
            "my_deck_count":   len(my_state["deck"]),
            "opp_hand_count":  len(opp_state["hand"]),
            "opp_board":       opp_state["board"],
            "opp_deck_count":  len(opp_state["deck"]),
            "log":             battle["log"][-30:],
            "deck_ready":      battle.get(my_side + "_deck_ready", False),
            "opp_deck_ready":  battle.get(opp_side + "_deck_ready", False),
        })


@app.route("/api/battle/challenge", methods=["POST"])
def battle_challenge():
    body            = request.json or {}
    uid             = body.get("user_id", "")
    target_username = body.get("target_username", "").strip()
    bet             = float(body.get("bet", 0))
    with lock:
        u = market["users"].get(uid)
        if not u:
            return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)
        if bet < 0:
            return jsonify({"error": "Bet cannot be negative"}), 400
        if u["arm_bucks"] < bet:
            return jsonify({"error": f"Not enough ₳ — you have ₳{u['arm_bucks']:.2f}"}), 400
        # Find target
        target_uid, target_u = None, None
        for tid, tu in market["users"].items():
            if tu["username"].lower() == target_username.lower():
                target_uid, target_u = tid, tu
                break
        if not target_uid:
            return jsonify({"error": f"Player '{target_username}' not found"}), 404
        if target_uid == uid:
            return jsonify({"error": "Cannot challenge yourself"}), 400
        _ensure_user_fields(target_u)
        # Check holdings — need ≥ 10 cards (normal + shiny combined)
        total_my = sum(u["portfolio"].values()) + sum(u["shiny_portfolio"].values())
        if total_my < BATTLE_DECK_SIZE:
            return jsonify({"error": f"You need at least {BATTLE_DECK_SIZE} stock cards to battle (you have {total_my})"}), 400
        # Lock bet immediately
        u["arm_bucks"] -= bet
        battle_id = str(uuid.uuid4())
        battle = {
            "id":                    battle_id,
            "status":                "pending_response",
            "challenger_id":         uid,
            "challenger_name":       u["username"],
            "challenger_deck_ready": False,
            "responder_id":          target_uid,
            "responder_name":        target_u["username"],
            "responder_deck_ready":  False,
            "bet":                   round(bet, 2),
            "current_turn":          target_uid,   # responder goes first
            "turn_number":           0,
            "created_at":            now_iso(),
            "winner_id":             None,
            "challenger":            {"hand": [], "board": [], "deck": []},
            "responder":             {"hand": [], "board": [], "deck": []},
            "log":                   [f"{u['username']} challenges {target_u['username']} to a card battle! Bet: ₳{bet:.2f}"],
        }
        market["battles"].append(battle)
        _battle_notify(target_u, f"⚔ {u['username']} challenges you to a battle! Bet: ₳{bet:.2f}", battle_id, "challenge")
        save(market)
    return jsonify({"success": True, "battle_id": battle_id})


@app.route("/api/battle/respond", methods=["POST"])
def battle_respond():
    body      = request.json or {}
    uid       = body.get("user_id", "")
    battle_id = body.get("battle_id", "")
    accept    = bool(body.get("accept", False))
    with lock:
        battle = next((b for b in market.get("battles", []) if b["id"] == battle_id), None)
        if not battle:
            return jsonify({"error": "Battle not found"}), 404
        if battle["responder_id"] != uid:
            return jsonify({"error": "Not the challenged player"}), 403
        if battle["status"] != "pending_response":
            return jsonify({"error": "Battle is no longer pending"}), 400
        challenger_u = market["users"].get(battle["challenger_id"])
        responder_u  = market["users"].get(uid)
        _ensure_user_fields(responder_u)
        _ensure_user_fields(challenger_u)
        if not accept:
            battle["status"] = "declined"
            # Refund challenger
            if challenger_u:
                challenger_u["arm_bucks"] += battle["bet"]
                _battle_notify(challenger_u, f"❌ {responder_u['username']} declined your battle challenge. Bet refunded.", battle_id, "info")
            save(market)
            return jsonify({"success": True, "accepted": False})
        # Check responder has enough cards and ₳
        total_resp = sum(responder_u["portfolio"].values()) + sum(responder_u["shiny_portfolio"].values())
        if total_resp < BATTLE_DECK_SIZE:
            return jsonify({"error": f"You need at least {BATTLE_DECK_SIZE} stock cards to battle (you have {total_resp})"}), 400
        if responder_u["arm_bucks"] < battle["bet"]:
            return jsonify({"error": f"Not enough ₳ to cover the bet — you have ₳{responder_u['arm_bucks']:.2f}"}), 400
        responder_u["arm_bucks"] -= battle["bet"]
        battle["status"] = "pending_decks"
        battle["log"].append(f"{responder_u['username']} accepted the challenge! Both players must select their 10-card decks.")
        _battle_notify(challenger_u, f"✅ {responder_u['username']} accepted your battle! Select your deck to begin.", battle_id, "your_turn")
        _battle_notify(responder_u,  f"✅ Battle accepted! Select your 10-card deck to begin.", battle_id, "your_turn")
        save(market)
    return jsonify({"success": True, "accepted": True, "battle_id": battle_id})


@app.route("/api/battle/select-deck", methods=["POST"])
def battle_select_deck():
    """Player submits their 10-card deck selection.
    Body: {user_id, battle_id, selections: [{ticker, shiny_key}...]}
    shiny_key is provided for shiny cards, null/absent for normal.
    """
    body        = request.json or {}
    uid         = body.get("user_id", "")
    battle_id   = body.get("battle_id", "")
    selections  = body.get("selections", [])    # list of {ticker, shiny_key}
    with lock:
        battle = next((b for b in market.get("battles", []) if b["id"] == battle_id), None)
        if not battle:
            return jsonify({"error": "Battle not found"}), 404
        if uid not in (battle["challenger_id"], battle["responder_id"]):
            return jsonify({"error": "Not your battle"}), 403
        if battle["status"] != "pending_decks":
            return jsonify({"error": "Not in deck selection phase"}), 400
        u = market["users"].get(uid)
        if not u:
            return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)
        if len(selections) != BATTLE_DECK_SIZE:
            return jsonify({"error": f"Must select exactly {BATTLE_DECK_SIZE} cards"}), 400
        side = "challenger" if battle["challenger_id"] == uid else "responder"
        if battle.get(side + "_deck_ready"):
            return jsonify({"error": "Deck already submitted"}), 400
        # ── Director limit: max 2 per deck ──
        director_count = 0
        for sel in selections:
            ticker = sel.get("ticker", "")
            stock  = market["stocks"].get(ticker)
            if stock and _battle_get_tier(stock["name"]) == "director":
                director_count += 1
        if director_count > BATTLE_MAX_DIRECTORS:
            return jsonify({"error": f"Too many directors — max {BATTLE_MAX_DIRECTORS} director-tier cards per deck (you selected {director_count})"}), 400

        # Validate selections against holdings and build card objects
        port_copy   = dict(u["portfolio"])
        shiny_copy  = dict(u["shiny_portfolio"])
        cards = []
        for sel in selections:
            ticker    = sel.get("ticker", "")
            shiny_key = sel.get("shiny_key")
            stock = market["stocks"].get(ticker)
            if not stock:
                return jsonify({"error": f"Unknown ticker: {ticker}"}), 400
            if shiny_key:
                if shiny_copy.get(shiny_key, 0) < 1:
                    return jsonify({"error": f"You don't own shiny card {shiny_key}"}), 400
                shiny_info = market.get("shiny_registry", {}).get(shiny_key)
                if not shiny_info:
                    return jsonify({"error": f"Shiny not in registry: {shiny_key}"}), 400
                shiny_copy[shiny_key] -= 1
                card = _battle_make_card(ticker, stock, shiny_key=shiny_key, shiny_info=shiny_info)
            else:
                if port_copy.get(ticker, 0) < 1:
                    return jsonify({"error": f"You don't own enough shares of {ticker}"}), 400
                port_copy[ticker] -= 1
                card = _battle_make_card(ticker, stock)
            cards.append(card)
        random.shuffle(cards)
        battle[side]["deck"] = cards
        battle[side + "_deck_ready"] = True

        # If both ready → start game
        if battle["challenger_deck_ready"] and battle["responder_deck_ready"]:
            battle["status"] = "active"
            for s in ("challenger", "responder"):
                deck = battle[s]["deck"]
                battle[s]["hand"] = deck[:BATTLE_START_HAND]
                battle[s]["deck"] = deck[BATTLE_START_HAND:]
            battle["log"].append("Both decks ready! Game begins. Responder goes first.")
            # Apply start-of-turn effects for first player (responder)
            _battle_start_turn(battle)
            resp_u = market["users"].get(battle["responder_id"])
            chal_u = market["users"].get(battle["challenger_id"])
            _battle_notify(resp_u,  "⚔ Your battle has started — it's YOUR TURN!", battle_id, "your_turn")
            _battle_notify(chal_u,  "⚔ Your battle has started — waiting for opponent's first move.", battle_id, "info")
        else:
            other_id = battle["responder_id"] if side == "challenger" else battle["challenger_id"]
            other_u  = market["users"].get(other_id)
            if other_u:
                _battle_notify(other_u, f"⚔ Opponent has selected their deck. Select yours to begin!", battle_id, "your_turn")
        save(market)
    return jsonify({"success": True})


@app.route("/api/battle/play-card", methods=["POST"])
def battle_play_card():
    """Play a card from hand to board."""
    body      = request.json or {}
    uid       = body.get("user_id", "")
    battle_id = body.get("battle_id", "")
    card_id   = body.get("card_id", "")
    with lock:
        battle = next((b for b in market.get("battles", []) if b["id"] == battle_id), None)
        if not battle: return jsonify({"error": "Battle not found"}), 404
        if battle["status"] != "active": return jsonify({"error": "Battle not active"}), 400
        if battle["current_turn"] != uid: return jsonify({"error": "Not your turn"}), 403
        side     = "challenger" if battle["challenger_id"] == uid else "responder"
        opp_side = "responder"  if side == "challenger" else "challenger"
        s = battle[side]
        if len(s["board"]) >= BATTLE_MAX_BOARD:
            return jsonify({"error": f"Board full — max {BATTLE_MAX_BOARD} cards"}), 400
        card = next((c for c in s["hand"] if c["id"] == card_id), None)
        if not card: return jsonify({"error": "Card not in hand"}), 400
        effect = card.get("effect")
        s["hand"].remove(card)

        # ── Renae: no summoning sickness ──
        if effect == "renae_prepared":
            card["sick"]       = False
            card["sick_turns"] = 0
            battle["log"].append(f"⚡ {card['name']} comes prepared — no summoning sickness!")
        else:
            card["sick"]       = True
            card["sick_turns"] = card.get("sick_turns", 1)

        s["board"].append(card)
        base_msg = f"{battle[side + '_name']} plays {card['name']} (ATK {card['atk']} / HP {card['hp']})."
        if effect:
            base_msg += f" [{effect.replace('_',' ').title()}]"
        battle["log"].append(base_msg)

        # ── Jenny: heals all friendly board cards +3 HP on play ──
        if effect == "jenny_tea":
            healed = 0
            for c in s["board"]:
                if c["id"] != card["id"] and c["hp"] < c["max_hp"]:
                    c["hp"] = min(c["max_hp"], c["hp"] + 3)
                    healed += 1
            battle["log"].append(f"☕ {card['name']} brings morning tea! All friendly cards healed +3 HP ({healed} cards).")

        # ── Nadia: draw 1 extra card on play ──
        if effect == "nadia_eager":
            if s["deck"]:
                drawn = s["deck"].pop(0)
                s["hand"].append(drawn)
                battle["log"].append(f"📚 {card['name']} is eager to help — draws {drawn['name']}!")

        # ── Lucy aura: admin cards on ALL boards immediately lose 5 HP ──
        if effect == "lucy_aura":
            admin_names = ["rhiannon", "meg", "svetlana"]
            affected = []
            for bside in ("challenger", "responder"):
                for bc in battle[bside]["board"]:
                    if any(n in bc["name"].lower() for n in admin_names):
                        bc["hp"] = max(0, bc["hp"] - 5)
                        affected.append(f"{bc['name']} ({bc['hp']} HP)")
            if affected:
                battle["log"].append(f"👠 Lucy enters — admin cards tremble! {', '.join(affected)} each lost 5 HP.")
            else:
                battle["log"].append(f"👠 Lucy enters — no admin cards in play right now, but they better watch out.")
            # Remove dead admins
            for bside in ("challenger", "responder"):
                battle[bside]["board"] = [c for c in battle[bside]["board"] if c["hp"] > 0]

        # ── Lucy already on board: newly played admin card loses 5 HP ──
        if effect in ("rhiannon_support", None) or card.get("seniority") == "support":
            # Check if Lucy is anywhere on the board
            lucy_in_play = any(
                c.get("effect") == "lucy_aura"
                for bside in ("challenger", "responder")
                for c in battle[bside]["board"]
            )
            admin_names = ["rhiannon", "meg", "svetlana"]
            if lucy_in_play and any(n in card["name"].lower() for n in admin_names):
                card["hp"] = max(0, card["hp"] - 5)
                battle["log"].append(f"👠 Lucy's aura hits {card['name']} — loses 5 HP on entry ({card['hp']} HP).")
                if card["hp"] <= 0:
                    s["board"] = [c for c in s["board"] if c["hp"] > 0]
                    battle["log"].append(f"💀 {card['name']} immediately perished from Lucy's aura!")

        save(market)
    return jsonify({"success": True})


@app.route("/api/battle/attack", methods=["POST"])
def battle_attack():
    """Attack with a board card targeting an opponent's board card."""
    body        = request.json or {}
    uid         = body.get("user_id", "")
    battle_id   = body.get("battle_id", "")
    attacker_id = body.get("attacker_id", "")
    target_id   = body.get("target_id", "")
    with lock:
        battle = next((b for b in market.get("battles", []) if b["id"] == battle_id), None)
        if not battle: return jsonify({"error": "Battle not found"}), 404
        if battle["status"] != "active": return jsonify({"error": "Battle not active"}), 400
        if battle["current_turn"] != uid: return jsonify({"error": "Not your turn"}), 403
        my_side  = "challenger" if battle["challenger_id"] == uid else "responder"
        opp_side = "responder"  if my_side == "challenger" else "challenger"
        my_s  = battle[my_side]
        opp_s = battle[opp_side]
        attacker = next((c for c in my_s["board"] if c["id"] == attacker_id), None)
        target   = next((c for c in opp_s["board"] if c["id"] == target_id),  None)
        if not attacker: return jsonify({"error": "Attacker not on your board"}), 400
        if not target:   return jsonify({"error": "Target not on opponent's board"}), 400
        if attacker["sick"]:
            return jsonify({"error": "Card has summoning sickness — wait one turn"}), 400
        # ── Tom Jones: 'in a meeting' — can't attack ──
        if attacker.get("in_meeting"):
            return jsonify({"error": f"{attacker['name']} is in a meeting and can't attack this turn! Try a different card."}), 400

        # ── Mark + Jesse friendly fire: 30% chance they attack each other ──
        mark_on_board  = [c for c in my_s["board"] if "mark"  in c["name"].lower() and c.get("effect") == "mark_jesse_chaos"]
        jesse_on_board = [c for c in my_s["board"] if "jesse" in c["name"].lower() and c.get("effect") == "mark_jesse_chaos"]
        if mark_on_board and jesse_on_board and random.random() < 0.30:
            # Pick the one that's NOT the attacker as the chaos victim
            chaos_pair = [c for c in (mark_on_board + jesse_on_board) if c["id"] != attacker["id"]]
            if chaos_pair:
                friendly_target = random.choice(chaos_pair)
                if "jesse" in attacker["name"].lower():
                    msg = f"😤 Jesse got upset and attacked Mark instead!"
                else:
                    msg = f"😤 Mark got upset and attacked Jesse instead!"
                # Both deal damage to each other
                atk_dmg = attacker["atk"]
                def_dmg = friendly_target["atk"]
                attacker["hp"]       = max(0, attacker["hp"]       - def_dmg)
                friendly_target["hp"] = max(0, friendly_target["hp"] - atk_dmg)
                battle["log"].append(f"{msg} {attacker['name']} HP→{attacker['hp']}, {friendly_target['name']} HP→{friendly_target['hp']}.")
                my_s["board"] = [c for c in my_s["board"] if c["hp"] > 0]
                winner_side = _battle_check_win(battle)
                if winner_side:
                    _battle_finish(battle, winner_side)
                save(market)
                return jsonify({"success": True, "chaos": True, "msg": msg})

        # ── Bella enthusiasm: +1 bonus damage while on board ──
        bella_bonus = 1 if any(c.get("effect") == "bella_enthusiasm" for c in my_s["board"]) else 0
        actual_atk  = round(attacker["atk"] + bella_bonus, 2)
        if bella_bonus:
            battle["log"].append(f"🌟 Bella's enthusiasm grants +1 bonus ATK to {attacker['name']} this attack!")

        # ── Hearthstone combat: both deal damage simultaneously ──
        attacker["hp"] = max(0, attacker["hp"] - target["atk"])
        target["hp"]   = max(0, target["hp"]   - actual_atk)
        battle["log"].append(
            f"{attacker['name']} ({actual_atk} ATK) ⚔ {target['name']} ({target['atk']} ATK). "
            f"Scores: {attacker['name']} HP→{attacker['hp']}, {target['name']} HP→{target['hp']}."
        )

        # ── Ark: takes 1 self-damage after attacking ──
        if attacker.get("effect") == "ark_self_damage" and attacker["hp"] > 0:
            attacker["hp"] = max(0, attacker["hp"] - 1)
            battle["log"].append(f"🤦 {attacker['name']} hurt himself in the confusion! (-1 HP, now {attacker['hp']} HP)")

        # Remove dead cards
        dead_mine = [c for c in my_s["board"]  if c["hp"] <= 0]
        dead_opp  = [c for c in opp_s["board"] if c["hp"] <= 0]
        if dead_mine:
            battle["log"].append(f"{battle[my_side + '_name']} loses: {', '.join(c['name'] for c in dead_mine)}.")
        if dead_opp:
            battle["log"].append(f"{battle[opp_side + '_name']} loses: {', '.join(c['name'] for c in dead_opp)}.")
        my_s["board"]  = [c for c in my_s["board"]  if c["hp"] > 0]
        opp_s["board"] = [c for c in opp_s["board"] if c["hp"] > 0]
        # Check win condition
        winner_side = _battle_check_win(battle)
        if winner_side:
            _battle_finish(battle, winner_side)
        save(market)
    return jsonify({"success": True})


@app.route("/api/battle/use-ability", methods=["POST"])
def battle_use_ability():
    """Use a Support/Admin card's heal ability on a friendly board card."""
    body      = request.json or {}
    uid       = body.get("user_id", "")
    battle_id = body.get("battle_id", "")
    healer_id = body.get("healer_id", "")   # the support/admin card doing the healing
    target_id = body.get("target_id", "")   # friendly board card to heal
    with lock:
        battle = next((b for b in market.get("battles", []) if b["id"] == battle_id), None)
        if not battle: return jsonify({"error": "Battle not found"}), 404
        if battle["status"] != "active": return jsonify({"error": "Battle not active"}), 400
        if battle["current_turn"] != uid: return jsonify({"error": "Not your turn"}), 403
        side = "challenger" if battle["challenger_id"] == uid else "responder"
        s = battle[side]
        healer = next((c for c in s["board"] if c["id"] == healer_id), None)
        target = next((c for c in s["board"] if c["id"] == target_id), None)
        if not healer: return jsonify({"error": "Healer not on your board"}), 400
        if not target: return jsonify({"error": "Target not on your board"}), 400
        if healer["seniority"] != "support":
            return jsonify({"error": "Only Support/Admin cards can heal"}), 400
        if healer.get("ability_used"):
            return jsonify({"error": "This card already used its ability this turn"}), 400
        heal_amount = 5
        old_hp = target["hp"]
        target["hp"] = min(target["max_hp"], target["hp"] + heal_amount)
        healer["ability_used"] = True
        battle["log"].append(
            f"{healer['name']} heals {target['name']} for {heal_amount} HP ({old_hp} → {target['hp']})."
        )
        save(market)
    return jsonify({"success": True})


@app.route("/api/battle/end-turn", methods=["POST"])
def battle_end_turn():
    body      = request.json or {}
    uid       = body.get("user_id", "")
    battle_id = body.get("battle_id", "")
    with lock:
        battle = next((b for b in market.get("battles", []) if b["id"] == battle_id), None)
        if not battle: return jsonify({"error": "Battle not found"}), 404
        if battle["status"] != "active": return jsonify({"error": "Battle not active"}), 400
        if battle["current_turn"] != uid: return jsonify({"error": "Not your turn"}), 403
        my_side  = "challenger" if battle["challenger_id"] == uid else "responder"
        opp_side = "responder"  if my_side == "challenger" else "challenger"
        opp_uid  = battle[opp_side + "_id"]
        _battle_end_turn(battle)
        # Check win (director aura might have killed something)
        winner_side = _battle_check_win(battle)
        if winner_side:
            _battle_finish(battle, winner_side)
        else:
            battle["log"].append(f"{battle[my_side + '_name']} ends their turn.")
            opp_u = market["users"].get(opp_uid)
            if opp_u:
                _battle_notify(opp_u, f"⚔ It's your turn in the battle vs {battle[my_side + '_name']}!", battle_id, "your_turn")
        save(market)
    return jsonify({"success": True})


@app.route("/api/battle/forfeit", methods=["POST"])
def battle_forfeit():
    body      = request.json or {}
    uid       = body.get("user_id", "")
    battle_id = body.get("battle_id", "")
    with lock:
        battle = next((b for b in market.get("battles", []) if b["id"] == battle_id), None)
        if not battle: return jsonify({"error": "Battle not found"}), 404
        if uid not in (battle["challenger_id"], battle["responder_id"]):
            return jsonify({"error": "Not your battle"}), 403
        if battle["status"] not in ("active", "pending_decks"):
            return jsonify({"error": "Cannot forfeit this battle"}), 400
        loser_side  = "challenger" if battle["challenger_id"] == uid else "responder"
        winner_side = "responder"  if loser_side == "challenger" else "challenger"
        battle["log"].append(f"{battle[loser_side + '_name']} forfeits!")
        _battle_finish(battle, winner_side)
        save(market)
    return jsonify({"success": True})


@app.route("/api/battle/notifications")
def battle_notifications():
    uid = request.args.get("user_id", "")
    with lock:
        u = market["users"].get(uid)
        if not u: return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)
        notifs = list(reversed(u["notifications"]))
        return jsonify({"notifications": notifs[:20]})


@app.route("/api/battle/notifications/read", methods=["POST"])
def battle_notifs_read():
    body = request.json or {}
    uid  = body.get("user_id", "")
    with lock:
        u = market["users"].get(uid)
        if not u: return jsonify({"error": "User not found"}), 404
        _ensure_user_fields(u)
        for n in u["notifications"]:
            n["read"] = True
        save(market)
    return jsonify({"success": True})


def _battle_finish(battle, winner_side):
    """Settle a finished battle — pay out winner, notify both players."""
    loser_side = "responder" if winner_side == "challenger" else "challenger"
    winner_id  = battle[winner_side + "_id"]
    loser_id   = battle[loser_side  + "_id"]
    winner_u   = market["users"].get(winner_id)
    loser_u    = market["users"].get(loser_id)
    battle["status"]    = "finished"
    battle["winner_id"] = winner_id
    # Payout: winner gets their own bet back + loser's bet
    payout = battle["bet"] * 2
    if winner_u:
        _ensure_user_fields(winner_u)
        winner_u["arm_bucks"] += payout
        _battle_notify(winner_u, f"🏆 You WON the battle vs {battle[loser_side + '_name']}! +₳{payout:.2f}", battle["id"], "info")
    if loser_u:
        _ensure_user_fields(loser_u)
        _battle_notify(loser_u,  f"💀 You LOST the battle vs {battle[winner_side + '_name']}. -₳{battle['bet']:.2f}", battle["id"], "info")
    battle["log"].append(
        f"🏆 {battle[winner_side + '_name']} wins! Payout: ₳{payout:.2f}"
    )


# ─────────────────────────── BACKGROUND THREAD ────────────────
def background_loop():
    global market
    _tick = 0
    while True:
        time.sleep(15)   # tick every 15s; full market tasks run every 4th tick (60s)
        _tick += 1
        try:
            with lock:
                # ── DB stub recovery: if startup failed to load, retry now ──
                if market.get("_db_unavailable") and _DATABASE_URL:
                    print("  [DB] Attempting stub recovery — trying to reload from DB…")
                    recovered = load()
                    if recovered and recovered != "DB_UNAVAILABLE":
                        print(f"  [DB] Recovery SUCCESS — reloaded market with {len(recovered.get('users',{}))} users")
                        market = recovered
                        # Add any missing keys
                        if "battles" not in market: market["battles"] = []
                        if "radio_playlist" not in market: market["radio_playlist"] = ""
                        market["phase"] = "trading"
                    else:
                        print("  [DB] Recovery failed — still unavailable")
                    # Don't run other background tasks if still in stub mode
                    if market.get("_db_unavailable"):
                        continue

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

                # ── Micro-fluctuation (every 60s = every 4th tick) ──
                if _tick % 4 == 0:
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

# ── Graceful shutdown save ──────────────────────────────────────
# Gunicorn sends SIGTERM when a new deploy starts. We intercept it to
# flush the current in-memory market to DB *before* the new instance
# loads — eliminating the race where the new instance loads stale data.
def _shutdown_save():
    sys.stdout.flush()
    print("  [DB] Shutdown triggered — saving market state before exit…", flush=True)
    try:
        with lock:
            if market and not market.get("_db_unavailable"):
                save(market)
                print("  [DB] Shutdown save complete.", flush=True)
            else:
                print("  [DB] Shutdown save skipped (stub/unavailable).", flush=True)
    except Exception as e:
        print(f"  [DB] Shutdown save error: {e}", flush=True)

def _sigterm_handler(signum, frame):
    _shutdown_save()
    # Restore default and re-raise so gunicorn can finish its graceful shutdown
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGQUIT, signal.SIG_DFL)
    os.kill(os.getpid(), signum)

# gunicorn sends SIGTERM to master, SIGQUIT to gthread workers — handle both
signal.signal(signal.SIGTERM, _sigterm_handler)
signal.signal(signal.SIGQUIT, _sigterm_handler)
atexit.register(_shutdown_save)  # belt-and-suspenders for any exit path

# ── Delayed startup reload ───────────────────────────────────────
# Render starts the new instance BEFORE terminating the old one.
# The old instance saves on SIGQUIT/SIGTERM; we wait 30s then reload
# from DB to pick up that final save.
def _delayed_startup_reload():
    time.sleep(30)
    print("  [DB] Delayed startup reload — checking for fresher DB state…", flush=True)
    global market
    try:
        with lock:
            if market.get("_db_unavailable"):
                return
            fresh = load()
            if fresh and fresh != "DB_UNAVAILABLE":
                # Merge: update in-memory market with whatever changed in DB
                market.update(fresh)
                market.pop("_db_unavailable", None)
                market["phase"] = "trading"
                print(f"  [DB] Delayed reload complete — {len(fresh.get('users',{}))} users", flush=True)
            else:
                print("  [DB] Delayed reload skipped — DB unavailable", flush=True)
    except Exception as e:
        print(f"  [DB] Delayed reload error: {e}", flush=True)

threading.Thread(target=_delayed_startup_reload, daemon=True).start()

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
