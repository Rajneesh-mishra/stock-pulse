#!/usr/bin/env python3
"""
Forex scalp engine — mechanical scalping for the 5 FX majors where spread
economics support it (EURUSD, GBPUSD, AUDUSD, USDJPY, USDCHF). Other pairs
(GOLD, OIL, BTC, USDCAD) have spreads too wide for scalp; they stay on
the swing framework.

Design rule: no LLM in the hot path. Entry decisions are purely mechanical.
Claude (via the tick flow) sets per-pair enable/mode/bias/session; this
daemon executes within those constraints.

SHADOW MODE (default): logs every would-be entry/exit, tracks paper P/L,
never places an order. Set state/forex_scalp_config.json global.shadow_mode
= false to enable live orders.

Three setups:
  1. range_extreme    — M5 touches N-period high/low, closes back inside
                        with ≥60% wick retrace. SL beyond wick, TP = prior
                        swing mid. Capped at 20 M5 candles lookback.
  2. session_open_break — London/NY first 15min defines range. Breakout
                        + retest of range edge. SL beyond range mid.
  3. ema_pullback     — M5 trend (EMA21>EMA50 or reverse) + pullback to
                        EMA21±0.5×ATR + bounce. SL just beyond EMA21.

All trades route through forex/risk_guard.py for hard safety checks.

State:
  state/forex_scalp_config.json      — written by Claude, read by engine
  state/forex_scalp_status.json      — engine heartbeat for the dashboard
  state/forex_scalp_ledger.jsonl     — every would-be or real trade
"""

import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path("/Users/rajneeshmishra/Downloads/stock-pulse")
os.chdir(REPO)

CONFIG_FILE = REPO / "state" / "forex_scalp_config.json"
STATUS_FILE = REPO / "state" / "forex_scalp_status.json"
LEDGER_FILE = REPO / "state" / "forex_scalp_ledger.jsonl"
CTRL_FILE   = REPO / "state" / "forex_scalp.control"
STATE_FILE  = REPO / "state" / "forex_state.json"
LOG_FILE    = REPO / "logs" / "scalp_engine.log"

PIP_SIZE = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
    "USDCAD": 0.0001, "USDCHF": 0.0001,
    "USDJPY": 0.01,   "GOLD": 0.1, "OIL_CRUDE": 0.01, "BTCUSD": 1.0,
}

SCALPABLE = {"EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "USDCHF"}

SESSION_HOURS = {
    "asia":       (0, 7),     # UTC
    "london":     (7, 12),
    "ny_overlap": (12, 16),
    "ny":         (16, 21),
}

POLL_SEC = 1.0
M1_BUCKET = 60
M5_BUCKET = 300
BOOK_SIZE = 240          # ~4h of M1, ~20h of M5 — enough warmup
ATR_PERIOD = 14
EMA21 = 21
EMA50 = 50

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
LEDGER_FILE.touch(exist_ok=True)

_log_lock = threading.Lock()

def log(msg):
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
    with _log_lock, LOG_FILE.open("a") as f:
        f.write(f"[{stamp}] {msg}\n")


# ── Config + status IO ───────────────────────────────────────────────────

def read_config():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception as e:
        log(f"config read failed: {e}")
        return {"global": {"enabled": False}, "pairs": {}}


def write_status(d):
    try:
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, default=str, indent=2))
        tmp.replace(STATUS_FILE)
    except Exception as e:
        log(f"status write failed: {e}")


def read_control():
    if not CTRL_FILE.exists(): return "run"
    try:
        v = CTRL_FILE.read_text().strip().lower()
        return v if v in ("run", "pause", "stop") else "run"
    except Exception:
        return "run"


def append_ledger(entry):
    entry["ts_utc"] = datetime.now(timezone.utc).isoformat()
    with LEDGER_FILE.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Price book (from WS ticks snapshot) ──────────────────────────────────

class PriceBook:
    """Per-epic rolling candle aggregator, fed from the dashboard's live_ticks
    snapshot. Keeps M1 and M5 OHLC rings."""
    def __init__(self, epic):
        self.epic = epic
        self.m1 = deque(maxlen=BOOK_SIZE)   # list of (ts_bucket, o, h, l, c)
        self.m5 = deque(maxlen=BOOK_SIZE)
        self._cur_m1 = None   # {ts, o, h, l, c}
        self._cur_m5 = None
        self.last_mid = None

    def ingest(self, bid, ofr, ts_s):
        mid = (float(bid) + float(ofr)) / 2
        self.last_mid = mid
        self._bucket(mid, ts_s, M1_BUCKET, "_cur_m1", self.m1)
        self._bucket(mid, ts_s, M5_BUCKET, "_cur_m5", self.m5)

    def _bucket(self, price, ts, bucket_sec, cur_attr, ring):
        b = int(ts) // bucket_sec * bucket_sec
        cur = getattr(self, cur_attr)
        if cur is None or cur["ts"] != b:
            if cur is not None:
                ring.append((cur["ts"], cur["o"], cur["h"], cur["l"], cur["c"]))
            setattr(self, cur_attr, {"ts": b, "o": price, "h": price, "l": price, "c": price})
            return
        cur["h"] = max(cur["h"], price)
        cur["l"] = min(cur["l"], price)
        cur["c"] = price

    def m5_closed(self):
        """Return closed M5 candles (excludes in-progress)."""
        return list(self.m5)

    def m1_closed(self):
        return list(self.m1)


# ── Lightweight TA ──────────────────────────────────────────────────────

def ema(values, period):
    if len(values) < period: return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def atr(candles, period=ATR_PERIOD):
    if len(candles) < period + 1: return None
    trs = []
    for i in range(1, len(candles)):
        _, _, h, l, c = candles[i]
        _, _, _, _, prev_c = candles[i-1]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    if len(trs) < period: return None
    return sum(trs[-period:]) / period


# ── Session gate ────────────────────────────────────────────────────────

def in_session(session_names):
    if not session_names: return True
    hour = datetime.now(timezone.utc).hour
    for name in session_names:
        lo, hi = SESSION_HOURS.get(name, (0, 24))
        if lo <= hour < hi:
            return True
    return False


# ── Setups — return (direction, entry, sl, tp) or None ──────────────────

def setup_range_extreme(book, pair_cfg):
    """Price touched 20-M5-bar high or low, last closed M5 retraced ≥60% of the wick."""
    cs = book.m5_closed()
    if len(cs) < 25: return None
    window = cs[-21:-1]                   # 20 bars, excluding the last one
    hi = max(c[2] for c in window)        # highest high in window
    lo = min(c[3] for c in window)        # lowest low in window
    last = cs[-1]
    _, o, h, l, c = last
    body = abs(c - o) or 1e-9
    wick_up = h - max(o, c)
    wick_dn = min(o, c) - l
    pip = PIP_SIZE[book.epic]
    # Bullish rejection at range low
    if l <= lo + 0.2 * pip and wick_dn > 0 and c > (l + 0.6 * (h - l)):
        entry = book.last_mid
        sl = l - 0.5 * pip
        tp = (hi + lo) / 2   # target = range mid
        if (tp - entry) / (entry - sl) >= pair_cfg.get("min_rr", 1.5):
            return ("BUY", entry, sl, tp)
    # Bearish rejection at range high
    if h >= hi - 0.2 * pip and wick_up > 0 and c < (l + 0.4 * (h - l)):
        entry = book.last_mid
        sl = h + 0.5 * pip
        tp = (hi + lo) / 2
        if (entry - tp) / (sl - entry) >= pair_cfg.get("min_rr", 1.5):
            return ("SELL", entry, sl, tp)
    return None


def setup_session_open_break(book, pair_cfg):
    """First 15min of the session defines a range; break + retest of range edge."""
    hour = datetime.now(timezone.utc).hour
    minute = datetime.now(timezone.utc).minute
    # Only fires 15-45 minutes past a session-open hour
    open_hours = {"london": 7, "ny": 12, "ny_overlap": 12, "asia": 0}
    relevant = [open_hours[s] for s in pair_cfg.get("sessions", []) if s in open_hours]
    if not relevant: return None
    this_hour_session_start = hour in relevant and 15 <= minute <= 45
    if not this_hour_session_start: return None

    cs = book.m5_closed()
    if len(cs) < 6: return None
    first15 = cs[-5:-2]   # 3 × M5 bars = 15min from session open, excluding last 2
    rng_hi = max(c[2] for c in first15)
    rng_lo = min(c[3] for c in first15)
    last = cs[-1]
    _, o, h, l, c = last
    pip = PIP_SIZE[book.epic]

    # Broke above and pulled back to retest
    if h > rng_hi + 0.3 * pip and abs(c - rng_hi) < 0.5 * pip:
        entry = book.last_mid
        sl = (rng_hi + rng_lo) / 2
        tp = entry + 2 * (entry - sl)
        return ("BUY", entry, sl, tp)
    if l < rng_lo - 0.3 * pip and abs(c - rng_lo) < 0.5 * pip:
        entry = book.last_mid
        sl = (rng_hi + rng_lo) / 2
        tp = entry - 2 * (sl - entry)
        return ("SELL", entry, sl, tp)
    return None


def setup_ema_pullback(book, pair_cfg):
    """M5 trend: EMA21 vs EMA50 side, pullback to EMA21, bounce confirmation."""
    cs = book.m5_closed()
    if len(cs) < EMA50 + 5: return None
    closes = [c[4] for c in cs]
    e21 = ema(closes, EMA21)
    e50 = ema(closes, EMA50)
    if e21 is None or e50 is None: return None
    a = atr(cs) or 0
    if a == 0: return None
    last = cs[-1]
    _, o, h, l, c = last
    pip = PIP_SIZE[book.epic]
    bias = pair_cfg.get("bias", "neutral")

    # Bullish trend: EMA21 > EMA50, pullback wick into EMA21, close back above
    if e21 > e50 and bias in ("neutral", "bull"):
        if l <= e21 + 0.5 * a and c > e21 + 0.1 * a:
            entry = book.last_mid
            sl = e21 - 0.5 * a
            tp = entry + 2 * (entry - sl)
            return ("BUY", entry, sl, tp)
    if e21 < e50 and bias in ("neutral", "bear"):
        if h >= e21 - 0.5 * a and c < e21 - 0.1 * a:
            entry = book.last_mid
            sl = e21 + 0.5 * a
            tp = entry - 2 * (sl - entry)
            return ("SELL", entry, sl, tp)
    return None


SETUPS = {
    "range_extreme":       setup_range_extreme,
    "session_open_break":  setup_session_open_break,
    "ema_pullback":        setup_ema_pullback,
}


# ── Halt tracker (in-memory) ─────────────────────────────────────────────

class HaltTracker:
    def __init__(self):
        self.lost_streak = {}            # epic -> int
        self.halted_until = {}           # epic -> datetime
        self.daily_pnl_usd = 0.0
        self.daily_reset_at = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    def is_halted(self, epic):
        if datetime.now(timezone.utc) < self.daily_reset_at:
            pass
        u = self.halted_until.get(epic)
        return u is not None and datetime.now(timezone.utc) < u

    def on_close(self, epic, pnl_usd, halt_cfg):
        # Daily reset
        now = datetime.now(timezone.utc)
        next_day = self.daily_reset_at + timedelta(days=1)
        if now >= next_day:
            self.daily_pnl_usd = 0.0
            self.daily_reset_at = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.daily_pnl_usd += pnl_usd
        if pnl_usd < 0:
            self.lost_streak[epic] = self.lost_streak.get(epic, 0) + 1
        else:
            self.lost_streak[epic] = 0
        streak_cap = halt_cfg.get("consecutive_losses_halt", 3)
        halt_hrs = halt_cfg.get("halt_duration_hours", 4)
        if self.lost_streak.get(epic, 0) >= streak_cap:
            self.halted_until[epic] = datetime.now(timezone.utc) + timedelta(hours=halt_hrs)
            self.lost_streak[epic] = 0
            log(f"HALT {epic} for {halt_hrs}h — {streak_cap} consecutive losses")

    def global_day_halt_hit(self, cap_usd):
        return self.daily_pnl_usd <= -abs(cap_usd)


# ── Risk guard bridge ────────────────────────────────────────────────────

def pass_risk_guard(epic, direction, size, sl, tp):
    """Returns (approved:bool, detail:str). Calls forex/risk_guard.py check."""
    try:
        r = subprocess.run(
            ["python3", "forex/risk_guard.py", "check", epic, direction,
             f"{size:.4f}", f"{sl:.5f}", f"{tp:.5f}"],
            capture_output=True, text=True, timeout=20, cwd=str(REPO),
        )
        if r.returncode == 0:
            return True, "ok"
        # Parse output for rejection reasons
        try:
            out = json.loads(r.stdout)
            rej = out.get("rejections") or []
            return False, "; ".join(rej)[:200]
        except Exception:
            return False, (r.stdout + r.stderr)[:200]
    except Exception as e:
        return False, f"risk_guard_spawn_failed: {e}"


def execute_trade(epic, direction, size, sl, tp, shadow):
    """In shadow mode: log the hypothetical order, return fake deal id.
    In live mode: call forex/api.py open."""
    if shadow:
        return True, {"shadow": True, "would_open": {
            "epic": epic, "direction": direction, "size": size, "sl": sl, "tp": tp,
        }}
    try:
        r = subprocess.run(
            ["python3", "forex/api.py", "open", epic, direction,
             f"{size:.4f}", f"{sl:.5f}", f"{tp:.5f}"],
            capture_output=True, text=True, timeout=30, cwd=str(REPO),
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout)[:500]
        return True, json.loads(r.stdout)
    except Exception as e:
        return False, f"api_spawn_failed: {e}"


# ── Tick ingest from dashboard /api/snapshot ─────────────────────────────

def read_live_ticks_snapshot():
    """Read live_ticks from the dashboard's full_snapshot() via local HTTP.
    Falls back to None if dashboard is down."""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8787/api/snapshot", timeout=3) as r:
            data = json.loads(r.read())
        return data.get("live_ticks") or {}
    except Exception as e:
        return None


# ── Main loop ────────────────────────────────────────────────────────────

class Engine:
    def __init__(self):
        self.books = {e: PriceBook(e) for e in SCALPABLE}
        self.halt = HaltTracker()
        self.open_positions = {}   # epic -> {direction, entry, sl, tp, opened_at, shadow}
        self.last_attempt = {}     # epic -> ts — throttle per-pair cadence

    def step(self):
        cfg = read_config()
        g = cfg.get("global", {})
        pairs = cfg.get("pairs", {})

        if not g.get("enabled"):
            return {"reason": "global_disabled"}

        shadow = bool(g.get("shadow_mode", True))
        if self.halt.global_day_halt_hit(g.get("daily_loss_cap_usd", 15)):
            return {"reason": "daily_loss_cap_hit", "daily_pnl": self.halt.daily_pnl_usd}

        ticks = read_live_ticks_snapshot()
        if not ticks:
            return {"reason": "no_live_ticks"}

        # Ingest ticks into books
        now_ts = time.time()
        for epic, t in ticks.items():
            if epic not in self.books: continue
            try:
                self.books[epic].ingest(t["bid"], t["ofr"], now_ts)
            except Exception:
                pass

        # Evaluate each enabled + in-session pair
        actions = []
        for epic, pc in pairs.items():
            if epic not in SCALPABLE: continue
            if not pc.get("enabled"): continue
            if self.halt.is_halted(epic): continue
            if epic in self.open_positions: continue
            if not in_session(pc.get("sessions") or []): continue

            # Throttle: at most one attempt per pair per 60s
            if now_ts - self.last_attempt.get(epic, 0) < 60: continue
            self.last_attempt[epic] = now_ts

            setup = SETUPS.get(pc.get("mode"))
            if not setup: continue
            book = self.books[epic]
            sig = setup(book, pc)
            if not sig: continue

            direction, entry, sl, tp = sig
            # Position sizing: risk_pct / (SL distance * pip_value)
            # Approximation: pip_value per 1.0 size = 1 quote unit
            risk_usd = g.get("risk_pct_per_scalp", 0.005) * 1000   # $1000 capital
            sl_dist = abs(entry - sl)
            if sl_dist <= 0: continue
            size = max(0.01, round(risk_usd / sl_dist * PIP_SIZE[epic], 4))

            approved, detail = pass_risk_guard(epic, direction, size, sl, tp)
            if not approved:
                append_ledger({
                    "kind": "rejected", "epic": epic, "setup": pc.get("mode"),
                    "direction": direction, "entry": entry, "sl": sl, "tp": tp,
                    "size": size, "reason": detail, "shadow": shadow,
                })
                actions.append({"epic": epic, "status": "rejected", "detail": detail})
                continue

            ok, res = execute_trade(epic, direction, size, sl, tp, shadow)
            if ok:
                self.open_positions[epic] = {
                    "direction": direction, "entry": entry, "sl": sl, "tp": tp,
                    "size": size, "opened_at": now_ts, "shadow": shadow,
                }
                append_ledger({
                    "kind": "opened", "epic": epic, "setup": pc.get("mode"),
                    "direction": direction, "entry": entry, "sl": sl, "tp": tp,
                    "size": size, "shadow": shadow, "broker_ref": res,
                })
                actions.append({"epic": epic, "status": "opened", "shadow": shadow})
            else:
                append_ledger({
                    "kind": "open_failed", "epic": epic, "detail": res, "shadow": shadow,
                })
                actions.append({"epic": epic, "status": "open_failed", "detail": res})

        # Check open (shadow) positions for SL/TP hit
        for epic in list(self.open_positions.keys()):
            if epic not in self.books: continue
            pos = self.open_positions[epic]
            if not pos.get("shadow"): continue     # live positions tracked by broker
            mid = self.books[epic].last_mid
            if mid is None: continue
            dir = pos["direction"]
            pnl = 0
            closed = None
            if dir == "BUY":
                if mid >= pos["tp"]:
                    pnl = (pos["tp"] - pos["entry"]) * pos["size"] / PIP_SIZE[epic]
                    closed = "tp_hit"
                elif mid <= pos["sl"]:
                    pnl = (pos["sl"] - pos["entry"]) * pos["size"] / PIP_SIZE[epic]
                    closed = "sl_hit"
            else:
                if mid <= pos["tp"]:
                    pnl = (pos["entry"] - pos["tp"]) * pos["size"] / PIP_SIZE[epic]
                    closed = "tp_hit"
                elif mid >= pos["sl"]:
                    pnl = (pos["entry"] - pos["sl"]) * pos["size"] / PIP_SIZE[epic]
                    closed = "sl_hit"
            if closed:
                self.halt.on_close(epic, pnl, g)
                append_ledger({
                    "kind": "closed", "epic": epic, "how": closed, "pnl_usd": round(pnl, 2),
                    "entry": pos["entry"], "exit": mid, "shadow": True,
                })
                del self.open_positions[epic]
                actions.append({"epic": epic, "status": "closed", "how": closed, "pnl": pnl})

        return {
            "reason": "ok",
            "shadow": shadow,
            "daily_pnl_usd": round(self.halt.daily_pnl_usd, 2),
            "open": {e: {k: v for k, v in p.items() if k != "opened_at"} for e, p in self.open_positions.items()},
            "halted": {e: u.isoformat() for e, u in self.halt.halted_until.items() if u > datetime.now(timezone.utc)},
            "actions": actions,
        }


def main_loop():
    eng = Engine()
    log(f"scalp_engine starting pid={os.getpid()}")
    heartbeat = 0
    while True:
        ctrl = read_control()
        if ctrl == "stop":
            log("control=stop → exiting")
            return 0
        if ctrl == "pause":
            time.sleep(POLL_SEC * 5)
            continue
        try:
            result = eng.step()
            heartbeat += 1
            if heartbeat % 30 == 0 or result.get("actions"):
                write_status({
                    "pid": os.getpid(),
                    "last_step_utc": datetime.now(timezone.utc).isoformat(),
                    "heartbeat": heartbeat,
                    **result,
                })
        except Exception as e:
            log(f"step error: {e}")
            import traceback; log(traceback.format_exc()[:500])
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        sys.exit(main_loop())
    except KeyboardInterrupt:
        log("interrupted")
        sys.exit(0)
