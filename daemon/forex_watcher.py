#!/usr/bin/env python3
"""
Forex watcher daemon — the EYES layer.

Watches prices + candle closes continuously between Claude's reasoning ticks.
Emits mechanical events to state/forex_events.jsonl. Does NOT make trading
decisions — Claude reads events and decides.

Event types emitted:
  - level_enter / level_exit / level_cross — watchlist zone triggers
  - bar_close — a new candle just closed (with full SMC snapshot)
  - structure_bos / structure_choch — SMC transition on watched TF
  - volatility_spike — ATR(14) current > 2× recent mean

Control: reads state/forex_watcher.control every loop (run | pause | stop).
Status: writes state/forex_watcher_status.json every loop.
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Make forex/ importable
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "forex"))

from technicals import get_full_candles, calc_atr, smc_analyze  # noqa: E402

STATE = REPO / "state"
EVENTS_FILE = STATE / "forex_events.jsonl"
SIGNALS_FILE = STATE / "forex_watchlist_signals.json"
CONTROL_FILE = STATE / "forex_watcher.control"
STATUS_FILE = STATE / "forex_watcher_status.json"
RUNTIME_FILE = STATE / ".watcher_runtime.json"
LOG_FILE = REPO / "logs" / "forex_watcher.out.log"

# Fallback config if signals file absent
DEFAULT_INSTRUMENTS = ["EURUSD", "USDJPY", "GOLD", "OIL_CRUDE", "BTCUSD",
                       "AUDUSD", "USDCAD", "GBPUSD", "USDCHF"]
DEFAULT_CADENCE_ACTIVE = 30      # sec between tick polls during active market
DEFAULT_CADENCE_QUIET = 120      # sec between tick polls overnight/weekend
STRUCTURE_POLL_EVERY_N_TICKS = 20  # run structure scan every N tick polls

# ── Utilities ────────────────────────────────────────────────────────────────

def log(msg):
    """Line-buffered print to stdout (launchd captures to log file)."""
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
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_FILE.open("a") as f:
        f.write(json.dumps(event, default=str) + "\n")
    log(f"EVENT {event['type']} {event.get('instrument','')} "
        f"{event.get('alert_id', '')} price={event.get('payload',{}).get('price')}")


def read_control():
    """Return 'run' | 'pause' | 'stop'. Default 'run' if file absent."""
    if not CONTROL_FILE.exists():
        return "run"
    try:
        val = CONTROL_FILE.read_text().strip().lower()
        return val if val in ("run", "pause", "stop") else "run"
    except Exception:
        return "run"


def market_active():
    """Heuristic: forex is 24/5. Active = Mon-Fri 00:00-22:00 UTC approx
    (excludes Fri-Sun weekend gap). Returns True/False."""
    now = utc_now()
    # weekday() Mon=0 Sun=6. Forex close: Fri 21:00 UTC, open: Sun 21:00 UTC.
    if now.weekday() == 5:  # Saturday
        return False
    if now.weekday() == 6 and now.hour < 21:  # Sunday before open
        return False
    if now.weekday() == 4 and now.hour >= 21:  # Friday after close
        return False
    return True


# ── Runtime state (persisted across daemon restarts) ─────────────────────────

def load_runtime():
    return read_json(RUNTIME_FILE, {
        "last_seen_bar": {},        # {"AUDUSD|HOUR": "2026-04-20T13:00:00"}
        "level_state": {},          # {alert_id: "inside"|"outside"}
        "alert_cooldowns": {},      # {alert_id: "2026-04-20T12:00:00Z"}
        "last_structure": {},       # {"AUDUSD|HOUR": {"bos_dir":"bull","bos_bars_ago":58}}
        "ticks_since_structure": 0,
    })


def save_runtime(rt):
    write_json_atomic(RUNTIME_FILE, rt)


# ── Level alerts ─────────────────────────────────────────────────────────────

def check_level_alerts(signals, prices_by_epic, runtime):
    """Emit level_enter / level_exit / level_cross events."""
    alerts = signals.get("level_alerts", [])
    now_iso = utc_now().isoformat()

    for alert in alerts:
        aid = alert.get("id")
        instrument = alert.get("instrument")
        if not aid or instrument not in prices_by_epic:
            continue

        cooldown_sec = alert.get("cooldown_sec", 600)
        last_fired = runtime["alert_cooldowns"].get(aid)
        if last_fired:
            try:
                last_dt = datetime.fromisoformat(last_fired.replace("Z", "+00:00"))
                if (utc_now() - last_dt).total_seconds() < cooldown_sec:
                    continue
            except Exception:
                pass

        price = prices_by_epic[instrument]
        emit_on = alert.get("emit_on", "enter")

        # Two alert modes:
        #   zone mode: zone_low + zone_high → enter/exit
        #   level mode: level + emit_on in {"cross_up", "cross_down"} → cross
        if "zone_low" in alert and "zone_high" in alert:
            zlo, zhi = alert["zone_low"], alert["zone_high"]
            inside = zlo <= price <= zhi
            prev_state = runtime["level_state"].get(aid, "outside")

            # Propagate direction from the watchlist entry into the event itself.
            # The counterfactual tracker (daemon/forex_counterfactual_tracker.py)
            # needs this persisted because alerts get REMOVED from watchlist over
            # time, and if direction isn't in the event we lose it forever.
            trade_dir = (alert.get("direction") or "").lower() or None

            if inside and prev_state != "inside" and emit_on in ("enter", "touch"):
                append_event({
                    "type": "level_enter", "instrument": instrument, "alert_id": aid,
                    "direction": trade_dir,
                    "payload": {"price": price, "zone_low": zlo, "zone_high": zhi,
                                "direction": trade_dir,
                                "sl": alert.get("sl"), "tp": alert.get("tp"),
                                "note": alert.get("note", "")},
                })
                runtime["alert_cooldowns"][aid] = now_iso
            elif not inside and prev_state == "inside" and emit_on in ("exit", "touch"):
                append_event({
                    "type": "level_exit", "instrument": instrument, "alert_id": aid,
                    "direction": trade_dir,
                    "payload": {"price": price, "zone_low": zlo, "zone_high": zhi,
                                "direction": trade_dir},
                })
                runtime["alert_cooldowns"][aid] = now_iso

            runtime["level_state"][aid] = "inside" if inside else "outside"

        elif "level" in alert:
            lvl = alert["level"]
            prev_side = runtime["level_state"].get(aid)
            cur_side = "above" if price > lvl else "below"
            trade_dir = (alert.get("direction") or "").lower() or None
            if prev_side and prev_side != cur_side:
                direction = "cross_up" if cur_side == "above" else "cross_down"
                if emit_on in ("touch", direction):
                    append_event({
                        "type": "level_cross", "instrument": instrument, "alert_id": aid,
                        "direction": trade_dir,
                        "payload": {"price": price, "level": lvl,
                                    "cross_direction": direction,     # up/down price crossing
                                    "direction": trade_dir,            # buy/sell intent
                                    "sl": alert.get("sl"), "tp": alert.get("tp"),
                                    "note": alert.get("note", "")},
                    })
                    runtime["alert_cooldowns"][aid] = now_iso
            runtime["level_state"][aid] = cur_side


# ── Structure scan (runs every N ticks) ──────────────────────────────────────

def _detect_transition(prev, curr):
    """Compare previous SMC snapshot vs current; return list of transition events."""
    events = []
    if not curr:
        return events

    # Did a brand-new BOS fire?
    cur_bos = curr.get("last_bos") or {}
    prev_bos = (prev or {}).get("last_bos") or {}
    if cur_bos.get("direction"):
        same_bar = (prev_bos.get("bar_ts") == cur_bos.get("bar_ts")
                    and prev_bos.get("direction") == cur_bos.get("direction"))
        if not same_bar and cur_bos.get("bars_ago", 999) <= 1:
            events.append(("bos", cur_bos))

    cur_choch = curr.get("last_choch") or {}
    prev_choch = (prev or {}).get("last_choch") or {}
    if cur_choch.get("direction"):
        same_bar = (prev_choch.get("bar_ts") == cur_choch.get("bar_ts")
                    and prev_choch.get("direction") == cur_choch.get("direction"))
        if not same_bar and cur_choch.get("bars_ago", 999) <= 1:
            events.append(("choch", cur_choch))

    return events


def scan_structure(signals, runtime):
    """Fetch candles for each watched (instrument, tf), detect bar-close +
    SMC transitions, emit events."""
    watches = signals.get("structure_watch", [])
    for watch in watches:
        instrument = watch.get("instrument")
        tfs = watch.get("timeframes", ["HOUR"])
        for tf in tfs:
            try:
                candles = get_full_candles(instrument, tf, 200)
            except Exception as e:
                log(f"structure fetch fail {instrument}/{tf}: {e}")
                continue
            if len(candles) < 60:
                continue

            key = f"{instrument}|{tf}"
            latest_bar_ts = candles[-1].get("time")
            prev_bar_ts = runtime["last_seen_bar"].get(key)

            # Did a new bar close since last check?
            new_bar = prev_bar_ts and latest_bar_ts and latest_bar_ts != prev_bar_ts
            runtime["last_seen_bar"][key] = latest_bar_ts

            smc_snap = smc_analyze(candles)
            atr = calc_atr(candles)
            last_close = candles[-1]["close"]

            if new_bar:
                append_event({
                    "type": "bar_close", "instrument": instrument, "timeframe": tf,
                    "payload": {
                        "bar_ts": latest_bar_ts,
                        "close": last_close,
                        "atr_14": atr,
                        "smc": smc_snap,
                    },
                })

            # Structure transition detection (BOS / CHoCH flips)
            prev_snap = runtime["last_structure"].get(key)
            transitions = _detect_transition(prev_snap, smc_snap)
            for kind, payload in transitions:
                append_event({
                    "type": f"structure_{kind}",
                    "instrument": instrument, "timeframe": tf,
                    "payload": {
                        "direction": payload.get("direction"),
                        "level": payload.get("level"),
                        "bar_ts": payload.get("bar_ts"),
                        "last_close": last_close, "atr_14": atr,
                    },
                })

            runtime["last_structure"][key] = smc_snap


# ── Price fetch ──────────────────────────────────────────────────────────────

def fetch_prices(instruments):
    """Use api.py via subprocess for the simple live-price path. Returns
    {epic: mid_price}. Degrades to {} on failure."""
    import subprocess
    result = {}
    for epic in instruments:
        try:
            r = subprocess.run(
                [sys.executable, str(REPO / "forex" / "api.py"), "price", epic],
                capture_output=True, text=True, timeout=12,
            )
            if r.returncode != 0:
                continue
            data = json.loads(r.stdout)
            p = data.get("prices", [{}])[0]
            bid, offer = p.get("bid"), p.get("offer")
            if bid and offer:
                result[epic] = (bid + offer) / 2
        except Exception as e:
            log(f"fetch_prices {epic} fail: {e}")
    return result


# ── Main loop ────────────────────────────────────────────────────────────────

def write_status(status, polls, events_emitted, errors, last_error=None):
    write_json_atomic(STATUS_FILE, {
        "status": status,
        "pid": os.getpid(),
        "started_at": _STARTED_AT,
        "last_poll": utc_now().isoformat(),
        "polls_total": polls,
        "events_emitted": events_emitted,
        "errors": errors,
        "last_error": last_error,
        "market_active": market_active(),
    })


_STARTED_AT = utc_now().isoformat()


def main():
    log(f"forex_watcher starting, pid={os.getpid()}, repo={REPO}")
    STATE.mkdir(parents=True, exist_ok=True)
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    runtime = load_runtime()
    polls = 0
    events_emitted_initial = _count_events()
    errors = 0
    last_error = None

    while True:
        control = read_control()
        if control == "stop":
            log("control=stop → exiting cleanly")
            write_status("stopped", polls, _count_events() - events_emitted_initial, errors, last_error)
            save_runtime(runtime)
            return 0

        if control == "pause":
            write_status("paused", polls, _count_events() - events_emitted_initial, errors, last_error)
            time.sleep(30)
            continue

        signals = read_json(SIGNALS_FILE, {
            "instruments": DEFAULT_INSTRUMENTS,
            "level_alerts": [],
            "structure_watch": [],
            "poll_cadence_sec": {"active_market": DEFAULT_CADENCE_ACTIVE,
                                 "quiet": DEFAULT_CADENCE_QUIET},
        })
        instruments = signals.get("instruments", DEFAULT_INSTRUMENTS)

        try:
            prices = fetch_prices(instruments)
            if prices:
                check_level_alerts(signals, prices, runtime)

            runtime["ticks_since_structure"] += 1
            if runtime["ticks_since_structure"] >= STRUCTURE_POLL_EVERY_N_TICKS:
                if signals.get("structure_watch"):
                    scan_structure(signals, runtime)
                runtime["ticks_since_structure"] = 0

            save_runtime(runtime)
            last_error = None
        except Exception as e:
            errors += 1
            last_error = f"{type(e).__name__}: {e}"
            log(f"tick error: {last_error}\n{traceback.format_exc()[-800:]}")

        polls += 1
        write_status("running", polls, _count_events() - events_emitted_initial, errors, last_error)

        cadence = signals.get("poll_cadence_sec", {})
        active_sec = cadence.get("active_market", DEFAULT_CADENCE_ACTIVE)
        quiet_sec = cadence.get("quiet", DEFAULT_CADENCE_QUIET)
        sleep_for = active_sec if market_active() else quiet_sec
        time.sleep(sleep_for)


def _count_events():
    """Count lines in events.jsonl (rough event count)."""
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
