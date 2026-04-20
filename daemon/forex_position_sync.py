#!/usr/bin/env python3
"""
Position sync daemon — second EYE layer.

Polls Capital.com /positions + /accounts every 15 sec (REST). Detects:
  - position_opened         → new deal_id appeared
  - position_closed         → deal_id disappeared (infers SL/TP hit when possible)
  - trail_candidate         → unrealized P&L ≥ 2× initial risk
  - daily_pnl_threshold     → account profit_loss crossed a warning level

Emits to state/forex_events.jsonl (same file as forex_watcher, same consumer).

Claude decides whether to actually trail / close / adjust. This daemon never
modifies positions.
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "forex"))

from technicals import _ensure_session, _load_env  # reuse session cache  # noqa: E402

STATE = REPO / "state"
EVENTS_FILE = STATE / "forex_events.jsonl"
CONTROL_FILE = STATE / "forex_position_sync.control"
STATUS_FILE = STATE / "forex_position_sync_status.json"
RUNTIME_FILE = STATE / ".position_sync_runtime.json"

POLL_SEC = 15
TRAIL_R_MULTIPLE = 2.0     # trail candidate when unrealized P&L ≥ 2× initial risk
DAILY_LOSS_WARN_PCT = -1.0  # warn at -1% daily P&L (before -2% hard stop)
DAILY_LOSS_STOP_PCT = -2.0

# Working capital base — pct thresholds apply to this, not broker balance.
# Broker shows ~$9,849 but we're operating on $1k. See risk_guard.py.
EFFECTIVE_CAPITAL_USD = 1000.0


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def utc_now():
    return datetime.now(timezone.utc)


def read_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception as e:
        log(f"read_json({path.name}) failed: {e}")
    return default


def write_json_atomic(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def append_event(event):
    event.setdefault("event_id",
        f"evt_{int(time.time()*1000)}_{event.get('type','unknown')}")
    event.setdefault("ts_utc", utc_now().isoformat())
    event.setdefault("consumed_by_claude", False)
    event.setdefault("source", "position_sync")
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_FILE.open("a") as f:
        f.write(json.dumps(event, default=str) + "\n")
    log(f"EVENT {event['type']} {event.get('instrument','')} dealId={event.get('payload',{}).get('deal_id')}")


def read_control():
    if not CONTROL_FILE.exists():
        return "run"
    try:
        val = CONTROL_FILE.read_text().strip().lower()
        return val if val in ("run", "pause", "stop") else "run"
    except Exception:
        return "run"


# ── Broker API ───────────────────────────────────────────────────────────────

def fetch_positions_and_account():
    """Return (positions_list, account_dict) using cached session."""
    import requests
    s = _ensure_session()
    hdrs = {
        "X-CAP-API-KEY": s["api_key"], "CST": s["cst"],
        "X-SECURITY-TOKEN": s["tok"], "Content-Type": "application/json",
    }

    r = requests.get(f"{s['base']}/api/v1/positions", headers=hdrs, timeout=12)
    if r.status_code == 401:
        # Force session refresh
        from technicals import _SESSION_CACHE, _SESSION_FILE
        _SESSION_CACHE.update({"cst": None, "tok": None})
        try:
            _SESSION_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        s = _ensure_session()
        hdrs.update({"CST": s["cst"], "X-SECURITY-TOKEN": s["tok"]})
        r = requests.get(f"{s['base']}/api/v1/positions", headers=hdrs, timeout=12)
    r.raise_for_status()
    positions_raw = r.json().get("positions", [])

    positions = []
    for p in positions_raw:
        pos, mkt = p.get("position", {}), p.get("market", {})
        positions.append({
            "deal_id": pos.get("dealId"),
            "epic": mkt.get("epic"),
            "name": mkt.get("instrumentName"),
            "direction": pos.get("direction"),
            "size": pos.get("size"),
            "level": pos.get("level"),
            "stop_level": pos.get("stopLevel"),
            "profit_level": pos.get("profitLevel"),
            "upl": pos.get("upl"),
            "bid": mkt.get("bid"),
            "offer": mkt.get("offer"),
            "created_utc": pos.get("createdDateUTC"),
        })

    ar = requests.get(f"{s['base']}/api/v1/accounts", headers=hdrs, timeout=12).json()
    acc = ar.get("accounts", [{}])[0]
    bal = acc.get("balance", {})
    account = {
        "balance": bal.get("balance"),
        "available": bal.get("available"),
        "deposit": bal.get("deposit"),
        "profit_loss": bal.get("profitLoss"),
    }
    return positions, account


# ── Event logic ──────────────────────────────────────────────────────────────

def _initial_risk_points(pos):
    """Distance from entry to SL — the 'R' unit for trail detection."""
    lvl, sl = pos.get("level"), pos.get("stop_level")
    if lvl is None or sl is None:
        return None
    return abs(lvl - sl)


def _unrealized_points(pos):
    """Signed points in favor (positive = in profit)."""
    lvl = pos.get("level")
    direction = pos.get("direction", "").upper()
    bid, offer = pos.get("bid"), pos.get("offer")
    if lvl is None:
        return None
    if direction == "BUY" and bid is not None:
        return bid - lvl  # long P&L marks to bid
    if direction == "SELL" and offer is not None:
        return lvl - offer  # short P&L marks to ask
    return None


def detect_position_changes(prev_positions, cur_positions, account, runtime):
    prev_by_id = {p["deal_id"]: p for p in prev_positions}
    cur_by_id = {p["deal_id"]: p for p in cur_positions}

    # Opens
    for deal_id, pos in cur_by_id.items():
        if deal_id not in prev_by_id:
            append_event({
                "type": "position_opened", "instrument": pos["epic"],
                "payload": {
                    "deal_id": deal_id, "direction": pos["direction"],
                    "size": pos["size"], "entry": pos["level"],
                    "sl": pos["stop_level"], "tp": pos["profit_level"],
                    "created": pos["created_utc"],
                },
            })

    # Closes — infer SL/TP hit when possible
    for deal_id, pos in prev_by_id.items():
        if deal_id in cur_by_id:
            continue
        # Compare last-seen bid/offer to SL/TP
        sl, tp = pos.get("stop_level"), pos.get("profit_level")
        direction = (pos.get("direction") or "").upper()
        last_bid, last_offer = pos.get("bid"), pos.get("offer")
        reason = "closed"
        if sl is not None and tp is not None and last_bid is not None and last_offer is not None:
            if direction == "BUY":
                # Long exits on bid
                if last_bid <= sl * 1.0005:
                    reason = "sl_hit"
                elif last_bid >= tp * 0.9995:
                    reason = "tp_hit"
            elif direction == "SELL":
                # Short exits on offer
                if last_offer >= sl * 0.9995:
                    reason = "sl_hit"
                elif last_offer <= tp * 1.0005:
                    reason = "tp_hit"
        append_event({
            "type": "position_closed", "instrument": pos.get("epic"),
            "payload": {
                "deal_id": deal_id, "direction": direction,
                "entry": pos.get("level"), "sl": sl, "tp": tp,
                "last_bid": last_bid, "last_offer": last_offer,
                "inferred_reason": reason, "last_upl": pos.get("upl"),
            },
        })

    # Trail candidates — only fire each threshold once per position
    for deal_id, pos in cur_by_id.items():
        initial_risk = _initial_risk_points(pos)
        unrealized = _unrealized_points(pos)
        if initial_risk is None or unrealized is None or initial_risk == 0:
            continue
        r_multiple = unrealized / initial_risk
        fired = runtime["trail_fired"].get(deal_id, 0)
        # Fire at 2R, 3R, 4R tiers
        for tier in (TRAIL_R_MULTIPLE, 3.0, 4.0, 5.0):
            if r_multiple >= tier and fired < tier:
                append_event({
                    "type": "trail_candidate", "instrument": pos["epic"],
                    "payload": {
                        "deal_id": deal_id, "direction": pos["direction"],
                        "entry": pos["level"], "current_bid": pos["bid"],
                        "current_offer": pos["offer"],
                        "r_multiple": round(r_multiple, 2),
                        "threshold_r": tier,
                        "initial_risk_points": round(initial_risk, 5),
                        "unrealized_points": round(unrealized, 5),
                        "upl": pos["upl"], "sl": pos["stop_level"], "tp": pos["profit_level"],
                    },
                })
                runtime["trail_fired"][deal_id] = tier

    # Daily P&L thresholds — percentage applied to WORKING CAPITAL, not broker
    # balance. That way a -$20 drawdown on $1k working capital fires 2% stop
    # instead of silently sliding past (would be 0.2% of $9,849 real balance).
    balance = account.get("balance")
    pnl = account.get("profit_loss")
    capital_base = min(balance or 0, EFFECTIVE_CAPITAL_USD) if balance else None
    if capital_base and pnl is not None and capital_base > 0:
        pnl_pct = (pnl / capital_base) * 100
        prev_tier = runtime.get("daily_pnl_tier")
        cur_tier = None
        if pnl_pct <= DAILY_LOSS_STOP_PCT:
            cur_tier = "stop"
        elif pnl_pct <= DAILY_LOSS_WARN_PCT:
            cur_tier = "warn"
        if cur_tier and cur_tier != prev_tier:
            append_event({
                "type": "daily_pnl_threshold",
                "payload": {
                    "tier": cur_tier, "pnl": pnl,
                    "broker_balance": balance, "capital_base": capital_base,
                    "pnl_pct_of_capital": round(pnl_pct, 2),
                    "threshold_pct": DAILY_LOSS_STOP_PCT if cur_tier == "stop" else DAILY_LOSS_WARN_PCT,
                    "note": ("Daily loss limit HIT — stop trading today"
                             if cur_tier == "stop"
                             else "Daily loss warning — size down, conservative only"),
                },
            })
            runtime["daily_pnl_tier"] = cur_tier

    # Clean up trail flags for closed deals
    for deal_id in list(runtime["trail_fired"].keys()):
        if deal_id not in cur_by_id:
            del runtime["trail_fired"][deal_id]


# ── Main loop ────────────────────────────────────────────────────────────────

_STARTED_AT = utc_now().isoformat()


def main():
    log(f"forex_position_sync starting, pid={os.getpid()}")
    STATE.mkdir(parents=True, exist_ok=True)

    _load_env()
    runtime = read_json(RUNTIME_FILE, {
        "prev_positions": [],
        "trail_fired": {},       # {deal_id: last_tier_fired}
        "daily_pnl_tier": None,  # "warn" | "stop" | None
    })

    polls, errors, events_start = 0, 0, _count_events()
    last_error = None

    while True:
        control = read_control()
        if control == "stop":
            log("control=stop → exiting")
            write_json_atomic(STATUS_FILE, {"status": "stopped", "polls_total": polls})
            write_json_atomic(RUNTIME_FILE, runtime)
            return 0
        if control == "pause":
            write_json_atomic(STATUS_FILE, {"status": "paused", "polls_total": polls})
            time.sleep(10)
            continue

        try:
            positions, account = fetch_positions_and_account()
            detect_position_changes(runtime["prev_positions"], positions, account, runtime)
            runtime["prev_positions"] = positions
            write_json_atomic(RUNTIME_FILE, runtime)
            last_error = None
        except Exception as e:
            errors += 1
            last_error = f"{type(e).__name__}: {e}"
            log(f"tick error: {last_error}\n{traceback.format_exc()[-600:]}")

        polls += 1
        write_json_atomic(STATUS_FILE, {
            "status": "running", "pid": os.getpid(),
            "started_at": _STARTED_AT,
            "last_poll": utc_now().isoformat(),
            "polls_total": polls,
            "events_emitted": _count_events() - events_start,
            "errors": errors, "last_error": last_error,
            "open_position_count": len(runtime["prev_positions"]),
            "account_balance": (runtime["prev_positions"] and None) or None,  # see below
        })

        time.sleep(POLL_SEC)


def _count_events():
    if not EVENTS_FILE.exists():
        return 0
    try:
        return sum(1 for _ in EVENTS_FILE.open())
    except Exception:
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("KeyboardInterrupt → exiting")
        sys.exit(0)
