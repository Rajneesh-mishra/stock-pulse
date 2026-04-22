#!/usr/bin/env python3
"""
Counterfactual P&L tracker for watchlist alerts.

Purpose: every `level_enter` / `level_cross` / `level_exit` event records a
hypothetical entry. The tracker records what the price did at +1h / +4h / +24h
after the alert fired, and computes pips-in-favor. Over time, this tells us:

  "Of the N alerts the watchlist fired last week, how many would have paid
   off? At what horizon? Did SKIPping them cost us money?"

Without this feedback loop, every 'zero-edge SKIP' is unaudited.

Outputs:
  state/forex_alert_counterfactuals.jsonl — append-only ledger, one line
    per fired alert plus a `_checkpoint` marker line per filled timestamp.

  state/forex_counterfactual_summary.json — rolling roll-up per alert_id.

Runs under launchd (com.stockpulse.counterfactual), polls every 60s.
"""

import json
import os
import sys
import time
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path("/Users/rajneeshmishra/Downloads/stock-pulse")
os.chdir(REPO)

EVENTS = REPO / "state" / "forex_events.jsonl"
CF_LOG = REPO / "state" / "forex_alert_counterfactuals.jsonl"
CF_SUMMARY = REPO / "state" / "forex_counterfactual_summary.json"
CF_CURSOR = REPO / "state" / ".counterfactual_cursor"  # last events.jsonl offset processed
LOG = REPO / "logs" / "counterfactual.log"

POLL_SEC = 60
HORIZONS = [("1h", 3600), ("4h", 14400), ("24h", 86400)]
TRIGGER_TYPES = {"level_enter", "level_cross"}

PIP_SIZE = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
    "USDCAD": 0.0001, "USDCHF": 0.0001,
    "USDJPY": 0.01,
    "GOLD": 0.1,
    "OIL_CRUDE": 0.01,
    "BTCUSD": 1.0,
}

LOG.parent.mkdir(parents=True, exist_ok=True)
CF_LOG.parent.mkdir(parents=True, exist_ok=True)
CF_LOG.touch(exist_ok=True)


def log(msg):
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
    with LOG.open("a") as f:
        f.write(f"[{stamp}] {msg}\n")


def read_cursor():
    if not CF_CURSOR.exists():
        return 0
    try:
        return int(CF_CURSOR.read_text().strip() or 0)
    except Exception:
        return 0


def write_cursor(pos):
    CF_CURSOR.write_text(str(pos))


def fetch_price(epic):
    """Ask forex/api.py for current bid/offer. Returns mid or None.
    api.py emits {"prices":[{bid,offer,...}]} (multi-line pretty-printed)."""
    try:
        r = subprocess.run(
            ["python3", "forex/api.py", "price", epic],
            capture_output=True, text=True, timeout=15, cwd=str(REPO),
        )
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        prices = data.get("prices") or []
        if not prices:
            return None
        p = prices[0]
        bid = p.get("bid")
        offer = p.get("offer")
        if bid is not None and offer is not None:
            return (float(bid) + float(offer)) / 2
        return None
    except Exception as e:
        log(f"fetch_price({epic}) failed: {e}")
        return None


_HISTORY_CACHE = {}  # epic -> (fetched_at_ts, candles[])


def fetch_historical_price(epic, target_utc):
    """Fetch historical price near target_utc. Uses forex/api.py history (hourly
    candles) and picks the candle whose close-time is closest. Caches per-epic
    for 5 min to avoid hammering the broker on bulk backfills."""
    try:
        now_ts = time.time()
        cached = _HISTORY_CACHE.get(epic)
        if cached and now_ts - cached[0] < 300:
            candles = cached[1]
        else:
            r = subprocess.run(
                ["python3", "forex/api.py", "history", epic, "200", "HOUR"],
                capture_output=True, text=True, timeout=25, cwd=str(REPO),
            )
            if r.returncode != 0:
                log(f"history fetch failed for {epic}: rc={r.returncode} {r.stderr[:200]}")
                return None
            data = json.loads(r.stdout)
            candles = data.get("candles") or []
            _HISTORY_CACHE[epic] = (now_ts, candles)
        if not candles:
            return None
        best = None
        best_dt = None
        for c in candles:
            t = parse_iso(c.get("time"))
            if t is None:
                continue
            diff = abs((t - target_utc).total_seconds())
            if best_dt is None or diff < best_dt:
                best_dt = diff
                best = c
        if best is None:
            return None
        # If the closest candle is more than 2h off, we're outside the 200-candle
        # window. Return None rather than pretending.
        if best_dt and best_dt > 7200:
            return None
        return best.get("close")
    except Exception as e:
        log(f"fetch_historical_price({epic}, {target_utc}) failed: {e}")
        return None


WATCHLIST = REPO / "state" / "forex_watchlist_signals.json"


def load_watchlist_lookup():
    """Build {alert_id: alert_dict} from the current watchlist. Watcher-emitted
    events don't carry direction/sl/tp; we enrich from the watchlist at fire time.
    Not perfect historically (if the alert was MODIFIED between fire and ingest
    we see the newer numbers) but directionally correct for 99% of alerts."""
    if not WATCHLIST.exists():
        return {}
    try:
        w = json.loads(WATCHLIST.read_text())
    except Exception:
        return {}
    out = {}
    for a in w.get("level_alerts", []) or []:
        aid = a.get("id")
        if aid:
            out[aid] = a
    return out


def ingest_new_alerts():
    """Scan events.jsonl from the last cursor, record any trigger-type events as
    new counterfactual rows."""
    if not EVENTS.exists():
        return 0
    pos = read_cursor()
    size = EVENTS.stat().st_size
    if pos >= size:
        return 0
    wl = load_watchlist_lookup()
    new = 0
    with EVENTS.open() as f:
        f.seek(pos)
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") not in TRIGGER_TYPES:
                continue
            epic = d.get("instrument")
            if not epic:
                continue
            payload = d.get("payload") or {}
            aid = d.get("alert_id") or f"unnamed_{d.get('event_id','?')}"
            wl_row = wl.get(aid, {}) if aid else {}
            trigger_price = (
                payload.get("price")
                or d.get("price")
                or payload.get("level")
                or d.get("level")
                or wl_row.get("level")
            )
            # Direction resolution order:
            #   1. event.direction (new — emitted by watcher post-fix)
            #   2. event.payload.direction
            #   3. current watchlist row (only works if alert still exists)
            #   4. infer from payload.cross_direction: cross_up on resistance=SELL,
            #      cross_down on support=BUY. Rough but right 80%+ of the time.
            #   5. aliased from alert_id semantics as last resort
            inferred_dir = None
            cd = payload.get("cross_direction") or payload.get("direction")
            if cd == "cross_up":
                inferred_dir = "sell"
            elif cd == "cross_down":
                inferred_dir = "buy"
            aid_lower = (aid or "").lower()
            if "_buy" in aid_lower or aid_lower.endswith("buy") or "_long" in aid_lower:
                aid_dir = "buy"
            elif "_sell" in aid_lower or aid_lower.endswith("sell") or "_short" in aid_lower:
                aid_dir = "sell"
            else:
                aid_dir = None
            # Legacy events stored cross direction under payload.direction; reject
            # those values and use only real buy/sell semantics.
            def _clean(v):
                if v in ("cross_up", "cross_down", None):
                    return None
                if isinstance(v, str):
                    v = v.lower().strip()
                    return v if v in ("buy", "sell") else None
                return None
            direction = (
                _clean(d.get("direction"))
                or _clean(payload.get("direction"))
                or _clean(wl_row.get("direction"))
                or inferred_dir
                or aid_dir
            )
            row = {
                "kind": "alert_fired",
                "alert_id": aid,
                "event_id": d.get("event_id"),
                "event_type": d.get("type"),
                "instrument": epic,
                "direction": direction,
                "trigger_price": trigger_price,
                "sl": payload.get("sl") or d.get("sl") or wl_row.get("sl"),
                "tp": payload.get("tp") or d.get("tp") or wl_row.get("tp"),
                "fired_at": d.get("ts_utc") or datetime.now(timezone.utc).isoformat(),
                "checkpoints": {h: None for h, _ in HORIZONS},
            }
            with CF_LOG.open("a") as cf:
                cf.write(json.dumps(row) + "\n")
            new += 1
        pos = f.tell()
    write_cursor(pos)
    if new:
        log(f"ingested {new} new alert fires")
    return new


def load_ledger():
    """Load the full ledger back as a list of dicts (keep order)."""
    rows = []
    if not CF_LOG.exists():
        return rows
    with CF_LOG.open() as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def rewrite_ledger(rows):
    """Atomic rewrite of the ledger (used when filling in checkpoint values)."""
    tmp = CF_LOG.with_suffix(".tmp")
    with tmp.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    tmp.replace(CF_LOG)


def parse_iso(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fill_pending_checkpoints():
    """For each ledger row, fill any checkpoint whose horizon has elapsed."""
    rows = load_ledger()
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    changed = 0
    price_cache = {}

    for row in rows:
        if row.get("kind") != "alert_fired":
            continue
        fired_at = parse_iso(row.get("fired_at"))
        if fired_at is None:
            continue
        epic = row.get("instrument")
        trigger = row.get("trigger_price")
        direction = row.get("direction")
        pip = PIP_SIZE.get(epic, 0.0001)
        cps = row.get("checkpoints") or {}

        for horizon_name, horizon_sec in HORIZONS:
            if cps.get(horizon_name) is not None:
                continue
            due_at = fired_at + timedelta(seconds=horizon_sec)
            if now < due_at:
                continue
            # Fresh (due within last 10 min): use live price, cached per epic.
            # Stale (older): fetch historical candle close at due_at.
            fresh = (now - due_at).total_seconds() <= 600
            if fresh:
                if epic not in price_cache:
                    price_cache[epic] = fetch_price(epic)
                px = price_cache[epic]
            else:
                px = fetch_historical_price(epic, due_at)
            if px is None:
                continue
            try:
                trigger_f = float(trigger)
            except Exception:
                cps[horizon_name] = {"price": px, "error": "no_trigger_price"}
                changed += 1
                continue
            # Direction-adjusted move: positive = alert was right.
            if direction and direction.upper() == "BUY":
                pips = (px - trigger_f) / pip
            elif direction and direction.upper() == "SELL":
                pips = (trigger_f - px) / pip
            else:
                pips = None
            r_multiple = None
            try:
                if row.get("sl") is not None and trigger is not None:
                    sl_dist = abs(float(row["sl"]) - trigger_f) / pip
                    if sl_dist > 0 and pips is not None:
                        r_multiple = round(pips / sl_dist, 2)
            except Exception:
                pass
            cps[horizon_name] = {
                "at": now.isoformat(),
                "price": px,
                "pips_in_favor": round(pips, 1) if pips is not None else None,
                "r_multiple": r_multiple,
            }
            changed += 1
        row["checkpoints"] = cps

    if changed:
        rewrite_ledger(rows)
        log(f"filled {changed} checkpoints")
    return changed


def write_summary():
    """Roll up per alert_id: count fires, hit rate at each horizon."""
    rows = load_ledger()
    per_alert = {}
    for row in rows:
        if row.get("kind") != "alert_fired":
            continue
        aid = row.get("alert_id", "?")
        bucket = per_alert.setdefault(aid, {
            "alert_id": aid,
            "instrument": row.get("instrument"),
            "direction": row.get("direction"),
            "fires": 0,
            "by_horizon": {h: {"filled": 0, "favorable": 0, "avg_pips": None, "sum_pips": 0.0}
                           for h, _ in HORIZONS},
        })
        bucket["fires"] += 1
        for h, _ in HORIZONS:
            cp = (row.get("checkpoints") or {}).get(h)
            if not cp or cp.get("pips_in_favor") is None:
                continue
            b = bucket["by_horizon"][h]
            b["filled"] += 1
            b["sum_pips"] += cp["pips_in_favor"]
            if cp["pips_in_favor"] > 0:
                b["favorable"] += 1
    # finalize averages
    for bucket in per_alert.values():
        for h, b in bucket["by_horizon"].items():
            if b["filled"] > 0:
                b["avg_pips"] = round(b["sum_pips"] / b["filled"], 1)
                b["hit_rate"] = round(b["favorable"] / b["filled"], 2)
            else:
                b["hit_rate"] = None
            b.pop("sum_pips", None)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "alerts": list(per_alert.values()),
    }
    tmp = CF_SUMMARY.with_suffix(".tmp")
    tmp.write_text(json.dumps(summary, indent=2))
    tmp.replace(CF_SUMMARY)


def main_loop():
    log(f"counterfactual tracker starting, pid={os.getpid()}")
    while True:
        try:
            ingest_new_alerts()
            fill_pending_checkpoints()
            write_summary()
        except Exception as e:
            log(f"loop error: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        ingest_new_alerts()
        fill_pending_checkpoints()
        write_summary()
        print(f"ledger rows: {sum(1 for _ in CF_LOG.open())}")
        print(f"summary at: {CF_SUMMARY}")
    else:
        try:
            sys.exit(main_loop())
        except KeyboardInterrupt:
            log("KeyboardInterrupt → exiting")
            sys.exit(0)
