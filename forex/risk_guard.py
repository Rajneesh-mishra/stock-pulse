#!/usr/bin/env python3
"""
Hard safety rails. Claude CANNOT override these.
Called before every order to validate or reject.

Usage:
  python3 forex/risk_guard.py check <epic> <direction> <size> <sl> <tp>
  python3 forex/risk_guard.py status   # Current risk exposure
"""

import json, sys, os, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── Load env + session ───────────────────────────────────────────────────────
env_path = Path(__file__).parent.parent / ".env"
for line in env_path.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

API_KEY  = os.environ["CAPITAL_API_KEY"]
EMAIL    = os.environ["CAPITAL_EMAIL"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]
ENV      = os.environ.get("CAPITAL_ENV", "demo")
BASE = ("https://api-capital.backend-capital.com" if ENV == "live"
        else "https://demo-api-capital.backend-capital.com")

FOREX_STATE_PATH = Path(__file__).parent.parent / "state" / "forex_state.json"
_SESSION_FILE = Path(__file__).parent.parent / "state" / ".capital_session.json"
_SESSION_TTL_SEC = 480  # 8 min (same pattern as api.py + technicals.py)

# ── Effective capital base ───────────────────────────────────────────────────
# The broker shows ~$9,849 but we're operating on $1k working capital.
# All pct-of-capital risk checks use min(broker_balance, EFFECTIVE_CAPITAL_USD)
# as the denominator so sizing matches the intended risk budget.
# Broker balance is still used for available-margin checks (real constraint).
EFFECTIVE_CAPITAL_USD = 1000.0

# ── HARD LIMITS ──────────────────────────────────────────────────────────────
MAX_RISK_PER_TRADE_PCT = 0.02       # 2% of capital per trade
MAX_TOTAL_EXPOSURE_PCT = 0.06       # 6% total open risk
MAX_POSITIONS_PER_THEME = 2         # Max 2 positions same theme
MAX_TOTAL_POSITIONS = 4             # Max 4 open positions
MAX_DAILY_LOSS_PCT = 0.05           # 5% daily loss → halt
MAX_CONSECUTIVE_LOSSES = 4          # 4 consecutive losses → 24hr pause
WEEKEND_CLOSE_HOUR_UTC_FRIDAY = 20  # Close risky positions by Friday 20:00 UTC

THEME_MAP = {
    "GOLD": "geopolitical",
    "OIL_CRUDE": "geopolitical",
    "USDJPY": "geopolitical",
    "EURUSD": "dollar_macro",
    "BTCUSD": "crypto_macro",
}

# High-impact events: no new trades ±30 min
# Format: (name, check_func or None)
# In production, this would check an economic calendar API
# For now, Claude is responsible for knowing the calendar

# ── Helpers ──────────────────────────────────────────────────────────────────

def create_session():
    """Reuses disk-cached session if fresh (<8 min), else creates new with
    429 retry. Shared with api.py + technicals.py to avoid rate limits."""
    import time
    if _SESSION_FILE.exists():
        try:
            cached = json.loads(_SESSION_FILE.read_text())
            if (time.time() - cached.get("ts", 0) < _SESSION_TTL_SEC
                    and cached.get("base") == BASE):
                return cached["cst"], cached["tok"]
        except Exception:
            pass
    for attempt in range(4):
        r = requests.post(f"{BASE}/api/v1/session",
            headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
            json={"identifier": EMAIL, "password": PASSWORD, "encryptedPassword": False},
            timeout=15)
        if r.status_code == 200:
            break
        if r.status_code == 429:
            time.sleep(2 + attempt * 2)
            continue
        r.raise_for_status()
    r.raise_for_status()
    cst, tok = r.headers["CST"], r.headers["X-SECURITY-TOKEN"]
    try:
        _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SESSION_FILE.write_text(json.dumps({
            "cst": cst, "tok": tok, "base": BASE, "ts": time.time(),
        }))
        _SESSION_FILE.chmod(0o600)
    except Exception:
        pass
    return cst, tok

def h(cst, tok):
    return {"CST": cst, "X-SECURITY-TOKEN": tok}

def get_account(cst, tok):
    return requests.get(f"{BASE}/api/v1/accounts", headers=h(cst,tok), timeout=10).json()

def get_positions(cst, tok):
    return requests.get(f"{BASE}/api/v1/positions", headers=h(cst,tok), timeout=10).json()

def get_price(cst, tok, epic):
    r = requests.get(f"{BASE}/api/v1/markets/{epic}", headers=h(cst,tok), timeout=10).json()
    snap = r.get("snapshot", {})
    return snap.get("bid"), snap.get("offer")

def load_forex_state():
    if FOREX_STATE_PATH.exists():
        return json.loads(FOREX_STATE_PATH.read_text())
    return {"trade_history": [], "consecutive_losses": 0, "daily_pnl": 0,
            "last_loss_halt": None, "open_positions": []}

# ── Risk Checks ──────────────────────────────────────────────────────────────

def check_order(epic, direction, size, sl, tp):
    """Run all risk checks. Returns (approved: bool, reasons: list)"""
    cst, tok = create_session()
    acc = get_account(cst, tok)["accounts"][0]
    broker_balance = acc["balance"]["balance"]
    available = acc["balance"]["available"]
    # Sizing denominator — smaller of real broker balance and intended working
    # capital. Keeps pct-of-capital limits meaningful when demo balance != WC.
    capital_base = min(broker_balance, EFFECTIVE_CAPITAL_USD)
    positions = get_positions(cst, tok).get("positions", [])
    bid, offer = get_price(cst, tok, epic)
    state = load_forex_state()

    size = float(size)
    sl = float(sl)
    tp = float(tp)

    entry_price = offer if direction == "BUY" else bid
    rejections = []
    warnings = []

    # 1. SL and TP must exist
    if sl == 0 or tp == 0:
        rejections.append("REJECT: SL and TP are mandatory. No naked orders.")

    # 2. SL must be on correct side
    if direction == "BUY" and sl >= entry_price:
        rejections.append(f"REJECT: BUY stop ({sl}) must be BELOW entry ({entry_price})")
    if direction == "SELL" and sl <= entry_price:
        rejections.append(f"REJECT: SELL stop ({sl}) must be ABOVE entry ({entry_price})")

    # 3. Per-trade risk
    risk_per_unit = abs(entry_price - sl)
    risk_amount = risk_per_unit * size
    risk_pct = risk_amount / capital_base if capital_base > 0 else 1.0
    if risk_pct > MAX_RISK_PER_TRADE_PCT:
        rejections.append(
            f"REJECT: Trade risk {risk_pct:.1%} exceeds {MAX_RISK_PER_TRADE_PCT:.0%} limit. "
            f"Risk=${risk_amount:.2f} on ${capital_base:.2f} capital base "
            f"(broker=${broker_balance:.2f}). Reduce size or widen stop.")

    # 4. Total open exposure
    total_open_risk = 0
    for p in positions:
        pos = p["position"]
        mkt = p["market"]
        if pos.get("stopLevel"):
            pos_risk = abs(pos["level"] - pos["stopLevel"]) * pos["size"]
            total_open_risk += pos_risk
    new_total = total_open_risk + risk_amount
    new_total_pct = new_total / capital_base if capital_base > 0 else 1.0
    if new_total_pct > MAX_TOTAL_EXPOSURE_PCT:
        rejections.append(
            f"REJECT: Total exposure would be {new_total_pct:.1%} (>${MAX_TOTAL_EXPOSURE_PCT:.0%}). "
            f"Close existing positions first.")

    # 5. Max positions
    if len(positions) >= MAX_TOTAL_POSITIONS:
        rejections.append(f"REJECT: Already {len(positions)} positions open (max {MAX_TOTAL_POSITIONS})")

    # 6. Theme limit
    theme = THEME_MAP.get(epic, "other")
    same_theme = sum(1 for p in positions
                     if THEME_MAP.get(p["market"]["epic"], "other") == theme)
    if same_theme >= MAX_POSITIONS_PER_THEME:
        rejections.append(
            f"REJECT: Already {same_theme} positions in '{theme}' theme (max {MAX_POSITIONS_PER_THEME})")

    # 7. Duplicate check — same instrument same direction
    for p in positions:
        if p["market"]["epic"] == epic and p["position"]["direction"] == direction:
            warnings.append(
                f"WARNING: Already have {direction} {epic}. This would be a double-up.")

    # 8. Consecutive loss halt
    if state.get("consecutive_losses", 0) >= MAX_CONSECUTIVE_LOSSES:
        last_halt = state.get("last_loss_halt")
        if last_halt:
            halt_until = datetime.fromisoformat(last_halt) + timedelta(hours=24)
            if datetime.now(timezone.utc) < halt_until:
                rejections.append(
                    f"REJECT: {MAX_CONSECUTIVE_LOSSES} consecutive losses. "
                    f"Trading halted until {halt_until.isoformat()}")

    # 9. Daily loss halt
    daily_loss_pct = abs(state.get("daily_pnl", 0)) / capital_base if capital_base > 0 and state.get("daily_pnl", 0) < 0 else 0
    if daily_loss_pct >= MAX_DAILY_LOSS_PCT:
        rejections.append(f"REJECT: Daily loss {daily_loss_pct:.1%} hit {MAX_DAILY_LOSS_PCT:.0%} limit. No more trades today.")

    # 10. Weekend check (Friday evening)
    now = datetime.now(timezone.utc)
    if now.weekday() == 4 and now.hour >= WEEKEND_CLOSE_HOUR_UTC_FRIDAY:
        if epic in ["BTCUSD", "OIL_CRUDE", "GOLD"]:
            warnings.append(f"WARNING: Friday {now.hour}:00 UTC — weekend gap risk on {epic}. Consider smaller size.")

    # 11. Available margin
    # Rough margin check — actual margin depends on leverage settings
    if available < risk_amount * 2:
        rejections.append(f"REJECT: Available margin ${available:.2f} too low for this trade risk.")

    approved = len(rejections) == 0

    result = {
        "approved": approved,
        "epic": epic,
        "direction": direction,
        "size": size,
        "entry_price": entry_price,
        "sl": sl,
        "tp": tp,
        "risk_amount": round(risk_amount, 2),
        "risk_pct": round(risk_pct, 4),
        "total_exposure_pct": round(new_total_pct, 4),
        "theme": theme,
        "broker_balance": broker_balance,
        "capital_base": capital_base,
        "available": available,
        "open_positions": len(positions),
        "rejections": rejections,
        "warnings": warnings,
    }
    print(json.dumps(result, indent=2))
    return approved

def cmd_status(cst, tok):
    acc = get_account(cst, tok)["accounts"][0]
    bal = acc["balance"]
    positions = get_positions(cst, tok).get("positions", [])
    state = load_forex_state()

    total_risk = 0
    pos_summary = []
    for p in positions:
        pos = p["position"]
        mkt = p["market"]
        risk = abs(pos["level"] - pos.get("stopLevel", pos["level"])) * pos["size"] if pos.get("stopLevel") else 0
        total_risk += risk
        pos_summary.append({
            "epic": mkt["epic"],
            "direction": pos["direction"],
            "risk": round(risk, 2),
            "theme": THEME_MAP.get(mkt["epic"], "other"),
            "upl": pos.get("upl"),
        })

    broker_balance = bal["balance"]
    capital_base = min(broker_balance, EFFECTIVE_CAPITAL_USD)
    print(json.dumps({
        "broker_balance": broker_balance,
        "capital_base": capital_base,
        "available": bal["available"],
        "open_pnl": bal.get("profitLoss", 0),
        "total_open_risk": round(total_risk, 2),
        "total_risk_pct": round(total_risk / capital_base, 4) if capital_base > 0 else 0,
        "positions": pos_summary,
        "consecutive_losses": state.get("consecutive_losses", 0),
        "daily_pnl": state.get("daily_pnl", 0),
        "limits": {
            "max_per_trade": f"{MAX_RISK_PER_TRADE_PCT:.0%} of ${capital_base:.0f}",
            "max_total": f"{MAX_TOTAL_EXPOSURE_PCT:.0%} of ${capital_base:.0f}",
            "max_positions": MAX_TOTAL_POSITIONS,
            "max_daily_loss": f"{MAX_DAILY_LOSS_PCT:.0%} of ${capital_base:.0f}",
        }
    }, indent=2))

# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: risk_guard.py check <epic> <BUY|SELL> <size> <sl> <tp>")
        print("       risk_guard.py status")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "check":
        if len(sys.argv) < 7:
            print("Usage: risk_guard.py check <epic> <BUY|SELL> <size> <sl> <tp>")
            sys.exit(1)
        approved = check_order(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6])
        sys.exit(0 if approved else 1)

    elif cmd == "status":
        cst, tok = create_session()
        cmd_status(cst, tok)
    else:
        print(f"Unknown: {cmd}")
        sys.exit(1)
