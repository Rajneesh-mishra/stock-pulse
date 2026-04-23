#!/usr/bin/env python3
"""
Local forex dashboard server. Python stdlib only — no Flask, no npm.

Endpoints:
  GET /                       → dashboard.html
  GET /api/snapshot           → full JSON state (daemons + broker + positions + prices + alerts)
  GET /api/events?n=50        → last N events from forex_events.jsonl
  GET /api/events/stream      → Server-Sent Events, tails new events live
  GET /api/ticks              → recent git commits (Claude tick audit trail)
  GET /api/control?daemon=&action=  → pause/run a daemon (writes control file)

Broker calls (positions, account, prices) are cached 3 sec so aggressive
browser refresh doesn't saturate Capital.com's rate limits.

Listen: http://localhost:8787  (override with DASHBOARD_PORT env var)
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    from websockets.sync.client import connect as ws_connect
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "forex"))

from technicals import get_full_candles, calc_atr, smc_analyze  # noqa: E402
from confluence import scan as confluence_scan  # noqa: E402

PORT = int(os.environ.get("DASHBOARD_PORT", "8787"))
HTML_FILE = REPO / "docs" / "index.html"
EVENTS_FILE = REPO / "state" / "forex_events.jsonl"
CONSUMED_FILE = REPO / "state" / "forex_events_consumed.txt"
SIGNALS_FILE = REPO / "state" / "forex_watchlist_signals.json"
WATCHER_STATUS = REPO / "state" / "forex_watcher_status.json"
POSYNC_STATUS = REPO / "state" / "forex_position_sync_status.json"
STATE_FILE = REPO / "state" / "forex_state.json"

# Broker call cache — thread-safe, 3-sec TTL
_broker_cache = {"ts": 0, "positions": None, "account": None, "prices": {}}
_broker_lock = threading.Lock()

# Candle cache — {(epic, resolution): {ts, candles}}, 30-sec TTL
_candle_cache = {}
_candle_cache_lock = threading.Lock()
CANDLE_CACHE_TTL = 30

# Gates cache — {epic: {ts, result}}, 60-sec TTL
_gates_cache = {}
_gates_lock = threading.Lock()
GATES_CACHE_TTL = 60

# Theme map for correlation gate
THEME_MAP = {
    "GOLD": "geopolitical", "OIL_CRUDE": "geopolitical", "USDJPY": "geopolitical",
    "EURUSD": "dollar_macro", "USDCAD": "dollar_macro", "USDCHF": "dollar_macro",
    "GBPUSD": "dollar_macro", "AUDUSD": "risk_on_dollar", "BTCUSD": "crypto_macro",
}

# Live tick stream state
WS_URL = "wss://api-streaming-capital.backend-capital.com/connect"
WS_PING_SEC = 540  # 9 min (server expires WS session at 10 min)
_latest_ticks = {}          # {epic: {"bid": ..., "ofr": ..., "ts_ms": ..., "rcv_ts": ...}}
_ticks_lock = threading.Lock()
_tick_subs = []             # list[queue.Queue] — SSE subscribers
_subs_lock = threading.Lock()
_ws_stats = {
    "status": "init",
    "reconnects": 0,
    "ticks_received": 0,
    "last_tick_at": None,
    "subscribed_epics": [],
    "last_error": None,
}

DAEMON_META = [
    {"name": "watcher",   "label": "com.stockpulse.forexwatcher",
     "status_file": str(WATCHER_STATUS),      "ctrl": "state/forex_watcher.control"},
    {"name": "posync",    "label": "com.stockpulse.forexpositionsync",
     "status_file": str(POSYNC_STATUS),       "ctrl": "state/forex_position_sync.control"},
    {"name": "waker",     "label": "com.stockpulse.claudewaker",
     "status_file": None,                     "ctrl": "state/forex_event_waker.control"},
    {"name": "heartbeat", "label": "com.stockpulse.claudeheartbeat",
     "status_file": None,                     "ctrl": "state/forex_heartbeat.control"},
]


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


# ── Broker proxies (cached) ──────────────────────────────────────────────────

def _run_api(cmd_args):
    r = subprocess.run(
        [sys.executable, str(REPO / "forex" / "api.py")] + cmd_args,
        capture_output=True, text=True, cwd=str(REPO), timeout=15,
    )
    try:
        return json.loads(r.stdout) if r.stdout else {}
    except json.JSONDecodeError:
        return {"error": "parse_fail", "raw": r.stdout[:500]}


def broker_snapshot():
    with _broker_lock:
        age = time.time() - _broker_cache["ts"]
        if age < 3.0 and _broker_cache["positions"] is not None:
            return {
                "positions": _broker_cache["positions"],
                "account": _broker_cache["account"],
                "prices": _broker_cache["prices"],
                "cache_age_sec": round(age, 1),
            }
        try:
            account = _run_api(["account"])
            positions = _run_api(["positions"])
            # Prices — only for instruments in the watchlist
            prices = {}
            try:
                signals = json.loads(SIGNALS_FILE.read_text()) if SIGNALS_FILE.exists() else {}
                epics = sorted({a["instrument"] for a in signals.get("level_alerts", [])})
            except Exception:
                epics = ["EURUSD", "USDJPY", "AUDUSD", "GOLD", "OIL_CRUDE"]
            for e in epics:
                p = _run_api(["price", e])
                try:
                    snap = p["prices"][0]
                    prices[e] = {
                        "bid": snap.get("bid"),
                        "offer": snap.get("offer"),
                        "mid": (snap.get("bid", 0) + snap.get("offer", 0)) / 2,
                        "change_pct": snap.get("change_pct"),
                        "high": snap.get("high"),
                        "low": snap.get("low"),
                        "update_time": snap.get("update_time"),
                    }
                except Exception:
                    prices[e] = {"error": True}
            _broker_cache.update({
                "ts": time.time(),
                "positions": positions,
                "account": account,
                "prices": prices,
            })
            return {
                "positions": positions,
                "account": account,
                "prices": prices,
                "cache_age_sec": 0,
            }
        except Exception as e:
            return {"error": str(e)}


# ── Daemon status ────────────────────────────────────────────────────────────

def daemon_snapshot():
    """Fetch launchd list once; parse per daemon."""
    try:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        ll_lines = r.stdout.splitlines()
    except Exception as e:
        ll_lines = []
    by_label = {}
    for line in ll_lines:
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2].startswith("com.stockpulse"):
            by_label[parts[2]] = {"pid": parts[0], "status": parts[1]}

    result = []
    for meta in DAEMON_META:
        entry = {"name": meta["name"], "loaded": meta["label"] in by_label}
        if entry["loaded"]:
            entry.update(by_label[meta["label"]])
        ctrl_path = REPO / meta["ctrl"]
        entry["control"] = ctrl_path.read_text().strip() if ctrl_path.exists() else "run"
        if meta["status_file"]:
            try:
                entry["status_detail"] = json.loads(Path(meta["status_file"]).read_text())
                last = entry["status_detail"].get("last_poll")
                if last:
                    t = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    entry["last_poll_age_sec"] = int((datetime.now(timezone.utc) - t).total_seconds())
            except Exception:
                entry["status_detail"] = None
        result.append(entry)
    return result


# ── Events ───────────────────────────────────────────────────────────────────

def load_consumed_ids():
    if not CONSUMED_FILE.exists():
        return set()
    return {l.strip() for l in CONSUMED_FILE.read_text().splitlines() if l.strip()}


def recent_events(n=50):
    if not EVENTS_FILE.exists():
        return []
    lines = EVENTS_FILE.read_text().splitlines()
    out = []
    consumed = load_consumed_ids()
    for line in lines[-n:]:
        try:
            d = json.loads(line)
            if d.get("event_id") in consumed:
                d["consumed_by_claude"] = True
            out.append(d)
        except Exception:
            pass
    return out


# ── Watchlist + alert distance ───────────────────────────────────────────────

def watchlist_snapshot(broker):
    try:
        signals = json.loads(SIGNALS_FILE.read_text())
    except Exception:
        return {"error": "signals file unreadable"}

    prices = broker.get("prices", {})
    entries = []
    for alert in signals.get("level_alerts", []):
        ep = alert["instrument"]
        mid = prices.get(ep, {}).get("mid")
        entry = {
            "id": alert["id"], "instrument": ep, "emit_on": alert.get("emit_on"),
            "note": alert.get("note", ""), "current_price": mid,
        }
        if "zone_low" in alert:
            entry["kind"] = "zone"
            entry["zone_low"] = alert["zone_low"]
            entry["zone_high"] = alert["zone_high"]
            if mid is not None:
                if alert["zone_low"] <= mid <= alert["zone_high"]:
                    entry["inside"] = True; entry["distance"] = 0
                else:
                    entry["inside"] = False
                    entry["distance"] = min(abs(mid - alert["zone_low"]),
                                             abs(mid - alert["zone_high"]))
        else:
            entry["kind"] = "level"
            entry["level"] = alert["level"]
            if mid is not None:
                entry["distance"] = abs(mid - alert["level"])
                entry["side"] = "above" if mid > alert["level"] else "below"
        if entry.get("distance") is not None and mid is not None:
            scale = 100 if ep == "USDJPY" else 1 if ep in ("OIL_CRUDE", "GOLD", "BTCUSD") else 10000
            entry["distance_pips"] = round(entry["distance"] * scale, 1)
        entries.append(entry)
    return {"alerts": entries, "structure_watch": signals.get("structure_watch", [])}


# ── Recent ticks (git audit trail) ───────────────────────────────────────────

def recent_ticks(n=15):
    try:
        r = subprocess.run(
            ["git", "log", f"-{n}", "--pretty=format:%h|%at|%s"],
            capture_output=True, text=True, cwd=str(REPO), timeout=5,
        )
        out = []
        for line in r.stdout.splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                out.append({
                    "sha": parts[0],
                    "ts": datetime.fromtimestamp(int(parts[1]), timezone.utc).isoformat(),
                    "subject": parts[2],
                })
        return out
    except Exception:
        return []


# ── Candles (cached) ─────────────────────────────────────────────────────────

def fetch_candles(epic, resolution, count=200):
    """Return OHLC bars for charting. Cached 30s per (epic, resolution)."""
    key = (epic, resolution)
    now = time.time()
    with _candle_cache_lock:
        cached = _candle_cache.get(key)
        if cached and now - cached["ts"] < CANDLE_CACHE_TTL:
            return cached["candles"]
    try:
        candles = get_full_candles(epic, resolution, count)
    except Exception as e:
        log(f"fetch_candles({epic},{resolution}) failed: {e}")
        candles = []
    with _candle_cache_lock:
        _candle_cache[key] = {"ts": now, "candles": candles}
    return candles


def chart_context(epic):
    """Aggregate all overlay data for one instrument: alerts, positions, tick."""
    alerts = []
    try:
        signals = json.loads(SIGNALS_FILE.read_text())
        for a in signals.get("level_alerts", []):
            if a.get("instrument") == epic:
                alerts.append(a)
    except Exception:
        pass

    positions = []
    broker = broker_snapshot()
    pos_data = (broker.get("positions") or {}).get("positions") or []
    for p in pos_data:
        if p.get("epic") == epic:
            positions.append(p)

    with _ticks_lock:
        tick = _latest_ticks.get(epic)

    return {
        "epic": epic,
        "alerts": alerts,
        "positions": positions,
        "tick": tick,
    }


# ── 7-gate setup evaluation ──────────────────────────────────────────────────

def _bullish_rejection(candle):
    """Hammer / bullish pin bar — body in upper half, lower wick dominant."""
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    body = abs(c - o)
    full = h - l
    if full == 0:
        return False
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    return (c >= o) and (lower_wick > 1.5 * body) and (lower_wick > upper_wick * 1.5)


def _bearish_rejection(candle):
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    body = abs(c - o)
    full = h - l
    if full == 0:
        return False
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    return (c <= o) and (upper_wick > 1.5 * body) and (upper_wick > lower_wick * 1.5)


def evaluate_gates(epic):
    """Compute the 7-gate status for one instrument. Cached 60s."""
    now = time.time()
    with _gates_lock:
        cached = _gates_cache.get(epic)
        if cached and now - cached["ts"] < GATES_CACHE_TTL:
            return cached["result"]

    gates = []
    alerts = []
    mid = None
    atr_m15 = None
    try:
        signals = json.loads(SIGNALS_FILE.read_text())
        alerts = [a for a in signals.get("level_alerts", []) if a.get("instrument") == epic]
    except Exception:
        pass

    with _ticks_lock:
        tick = _latest_ticks.get(epic)
    if tick and tick.get("bid") and tick.get("ofr"):
        mid = (tick["bid"] + tick["ofr"]) / 2

    # ── Gate 1: HTF bias aligned ──
    try:
        conf = confluence_scan(epic)
        comp = conf.get("composite_score", 0)
        aligned = conf.get("aligned", False)
        call = conf.get("directional_call", "neutral")
        if aligned:
            g1 = {"status": "PASS", "value": comp, "detail": f"aligned {call} ({comp:+.1f})"}
        elif abs(comp) >= 40 and conf.get("all_tfs_agree"):
            g1 = {"status": "PARTIAL", "value": comp, "detail": f"{call} {comp:+.1f} — TFs agree but weak"}
        else:
            g1 = {"status": "FAIL", "value": comp, "detail": f"{call} {comp:+.1f} not aligned"}
        gates.append({"id": 1, "name": "HTF bias", **g1})
        direction_bias = 1 if comp > 10 else -1 if comp < -10 else 0
    except Exception as e:
        gates.append({"id": 1, "name": "HTF bias", "status": "ERR", "detail": str(e)[:60]})
        direction_bias = 0

    # ── Gate 2: At real structure ──
    at_structure = False
    struct_detail = []
    for a in alerts:
        if "zone_low" in a and mid is not None:
            if a["zone_low"] <= mid <= a["zone_high"]:
                at_structure = True
                struct_detail.append(f"in {a['id']}")
        elif "level" in a and mid is not None:
            scale = 100 if epic == "USDJPY" else 1 if epic in ("OIL_CRUDE", "GOLD", "BTCUSD") else 10000
            if abs(mid - a["level"]) * scale < 10:
                at_structure = True
                struct_detail.append(f"near {a['id']} ({abs(mid-a['level'])*scale:.1f}p)")
    # Also check unmitigated OB/FVG on H1
    if not at_structure:
        try:
            h1 = get_full_candles(epic, "HOUR", 120)
            if h1 and mid is not None:
                smc = smc_analyze(h1)
                if smc:
                    for ob in [smc.get("nearest_bull_ob"), smc.get("nearest_bear_ob")]:
                        if ob and ob["bottom"] <= mid <= ob["top"]:
                            at_structure = True
                            struct_detail.append("in unmit H1 OB")
                    fvg = smc.get("last_fvg")
                    if fvg and not fvg.get("mitigated") and fvg["bottom"] <= mid <= fvg["top"]:
                        at_structure = True
                        struct_detail.append(f"in H1 FVG ({fvg['direction']})")
        except Exception:
            pass
    gates.append({
        "id": 2, "name": "At structure",
        "status": "PASS" if at_structure else "FAIL",
        "detail": "; ".join(struct_detail) if struct_detail else "not at any zone/level/OB/FVG",
    })

    # ── Gate 3: Confirmation on M15 (rejection wick / bullish or bearish) ──
    try:
        m15 = get_full_candles(epic, "MINUTE_15", 60)
        if m15 and len(m15) >= 3:
            last_closed = m15[-2]  # second-last is last fully closed bar
            atr_m15 = calc_atr(m15)
            bull_rej = _bullish_rejection(last_closed)
            bear_rej = _bearish_rejection(last_closed)
            if direction_bias > 0 and bull_rej:
                g3 = {"status": "PASS", "detail": f"bullish rejection last bar @ {last_closed['close']}"}
            elif direction_bias < 0 and bear_rej:
                g3 = {"status": "PASS", "detail": f"bearish rejection last bar @ {last_closed['close']}"}
            elif bull_rej or bear_rej:
                # Rejection but wrong direction vs bias
                d = "bullish" if bull_rej else "bearish"
                g3 = {"status": "PARTIAL", "detail": f"{d} rejection but bias says {call}"}
            else:
                g3 = {"status": "FAIL", "detail": "no rejection wick on last closed M15"}
        else:
            g3 = {"status": "ERR", "detail": "insufficient M15 data"}
    except Exception as e:
        g3 = {"status": "ERR", "detail": str(e)[:60]}
    gates.append({"id": 3, "name": "Confirmation", **g3})

    # ── Gate 4: R:R ≥ 2:1 (requires structured SL/TP in alert — most don't have them yet) ──
    rr_found = False
    for a in alerts:
        if all(k in a for k in ("sl", "tp", "level")) or all(k in a for k in ("sl", "tp", "zone_low")):
            entry = a.get("level") or (a["zone_low"] + a["zone_high"]) / 2
            sl, tp = a["sl"], a["tp"]
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            rr = reward / risk if risk > 0 else 0
            if rr >= 2:
                gates.append({"id": 4, "name": "R:R ≥ 2:1", "status": "PASS",
                              "detail": f"{rr:.1f}:1 from {a['id']}"})
            elif rr >= 1.5:
                gates.append({"id": 4, "name": "R:R ≥ 2:1", "status": "PARTIAL",
                              "detail": f"{rr:.1f}:1 from {a['id']}"})
            else:
                gates.append({"id": 4, "name": "R:R ≥ 2:1", "status": "FAIL",
                              "detail": f"{rr:.1f}:1 from {a['id']}"})
            rr_found = True
            break
    if not rr_found:
        gates.append({"id": 4, "name": "R:R ≥ 2:1", "status": "N/A",
                      "detail": "SL/TP not structured — computed at trade time"})

    # ── Gate 5: Position count (room for new trade) ──
    try:
        broker = broker_snapshot()
        pos_list = (broker.get("positions") or {}).get("positions") or []
        n_open = len(pos_list)
        gates.append({
            "id": 5, "name": "Capacity",
            "status": "PASS" if n_open < 4 else "FAIL",
            "detail": f"{n_open}/4 positions open",
        })
    except Exception as e:
        gates.append({"id": 5, "name": "Capacity", "status": "ERR", "detail": str(e)[:60]})
        n_open = 0
        pos_list = []

    # ── Gate 6: Correlation (≤ 2 in same theme) ──
    my_theme = THEME_MAP.get(epic, "other")
    same_theme = sum(1 for p in pos_list if THEME_MAP.get(p.get("epic"), "other") == my_theme)
    gates.append({
        "id": 6, "name": "Correlation",
        "status": "PASS" if same_theme < 2 else "FAIL",
        "detail": f"{same_theme}/2 {my_theme}",
    })

    # ── Gate 7: Session / timing ──
    now_utc = datetime.now(timezone.utc)
    h = now_utc.hour
    # London 07-15 UTC, NY 12-20 UTC, overlap 12-15 = best
    if 12 <= h < 16:
        session = "London+NY overlap (best)"
        session_status = "PASS"
    elif 7 <= h < 12 or 16 <= h < 20:
        session = "London or NY (good)"
        session_status = "PASS"
    elif 20 <= h < 22:
        session = "NY late (thin)"
        session_status = "PARTIAL"
    else:
        session = "Asia / overnight (thin)"
        session_status = "PARTIAL"
    gates.append({"id": 7, "name": "Session", "status": session_status, "detail": session})

    # ── Overall verdict ──
    statuses = [g["status"] for g in gates]
    n_pass = sum(1 for s in statuses if s == "PASS")
    n_fail = sum(1 for s in statuses if s == "FAIL")
    must_pass = [g["status"] for g in gates if g["id"] in (1, 2, 3)]
    if all(s == "PASS" for s in must_pass) and n_fail == 0:
        verdict = "READY"
    elif gates[1]["status"] == "PASS" and n_fail <= 2:
        verdict = "WAIT"  # at structure, near ready
    else:
        verdict = "SKIP"

    result = {
        "epic": epic,
        "ts": datetime.now(timezone.utc).isoformat(),
        "mid": mid,
        "atr_m15": atr_m15,
        "verdict": verdict,
        "pass_count": n_pass,
        "gates": gates,
    }
    with _gates_lock:
        _gates_cache[epic] = {"ts": now, "result": result}
    return result


def gates_all():
    """Evaluate gates for every instrument in the watchlist."""
    try:
        signals = json.loads(SIGNALS_FILE.read_text())
        epics = sorted({a["instrument"] for a in signals.get("level_alerts", [])})
    except Exception:
        return []
    return [evaluate_gates(e) for e in epics]


# ── WebSocket tick streamer ──────────────────────────────────────────────────

def _ws_subscribed_epics():
    """All epics we care about — union of watchlist alerts + structure watch."""
    try:
        signals = json.loads(SIGNALS_FILE.read_text())
        epics = set()
        for a in signals.get("level_alerts", []):
            epics.add(a["instrument"])
        for s in signals.get("structure_watch", []):
            epics.add(s["instrument"])
        for i in signals.get("instruments", []):
            epics.add(i)
        return sorted(epics)[:40]  # WS limit
    except Exception:
        return ["EURUSD", "USDJPY", "GOLD", "OIL_CRUDE", "BTCUSD",
                "AUDUSD", "USDCAD", "GBPUSD", "USDCHF"]


def _ws_broadcast(tick):
    """Non-blocking fan-out to SSE subscribers. Drops slow consumers."""
    dead = []
    with _subs_lock:
        for q in _tick_subs:
            try:
                q.put_nowait(tick)
            except queue.Full:
                dead.append(q)
        for d in dead:
            try:
                _tick_subs.remove(d)
            except ValueError:
                pass


def _ws_connection_loop():
    """Background thread: connect to Capital WS, subscribe, relay ticks.
    Reconnects on failure with backoff. Refreshes session on 401-style drops."""
    from technicals import _ensure_session  # noqa: E402

    corr = [0]
    def next_corr():
        corr[0] += 1
        return str(corr[0])

    while True:
        try:
            s = _ensure_session()
            _ws_stats["status"] = "connecting"
            with ws_connect(WS_URL, open_timeout=15, close_timeout=5,
                            max_size=2**20) as ws:
                epics = _ws_subscribed_epics()
                ws.send(json.dumps({
                    "destination": "marketData.subscribe",
                    "correlationId": next_corr(),
                    "cst": s["cst"], "securityToken": s["tok"],
                    "payload": {"epics": epics},
                }))
                _ws_stats["status"] = "connected"
                _ws_stats["subscribed_epics"] = epics
                _ws_stats["last_error"] = None
                log(f"WS connected, subscribed to {len(epics)} epics: {epics}")

                last_ping = time.time()

                while True:
                    # Periodic ping (don't wait for new messages to trigger)
                    if time.time() - last_ping > WS_PING_SEC:
                        try:
                            ws.send(json.dumps({
                                "destination": "ping",
                                "correlationId": f"ping-{next_corr()}",
                                "cst": s["cst"], "securityToken": s["tok"],
                            }))
                            last_ping = time.time()
                        except Exception as e:
                            raise RuntimeError(f"ping failed: {e}")

                    try:
                        msg = ws.recv(timeout=30)
                    except TimeoutError:
                        continue  # loop back to ping check

                    try:
                        d = json.loads(msg)
                    except Exception:
                        continue

                    dest = d.get("destination")
                    if dest == "quote":
                        p = d.get("payload", {}) or {}
                        epic = p.get("epic")
                        if not epic:
                            continue
                        tick = {
                            "epic": epic,
                            "bid": p.get("bid"),
                            "ofr": p.get("ofr"),
                            "ts_ms": p.get("timestamp"),
                            "rcv_ts": int(time.time() * 1000),
                        }
                        with _ticks_lock:
                            _latest_ticks[epic] = tick
                        _ws_stats["ticks_received"] += 1
                        _ws_stats["last_tick_at"] = datetime.now(timezone.utc).isoformat()
                        _ws_broadcast(tick)
                    elif dest == "ping":
                        pass  # ack
                    elif dest == "marketData.subscribe":
                        pass  # subscribe ack
                    # Other destinations ignored
        except Exception as e:
            _ws_stats["status"] = "reconnecting"
            _ws_stats["reconnects"] += 1
            _ws_stats["last_error"] = str(e)[:200]
            log(f"WS loop error: {e} — reconnecting in 10s")
            time.sleep(10)


def start_ws_streamer():
    if not WS_AVAILABLE:
        log("websockets library unavailable — live ticks disabled")
        _ws_stats["status"] = "unavailable"
        _ws_stats["last_error"] = "websockets library not installed"
        return
    t = threading.Thread(target=_ws_connection_loop, daemon=True,
                         name="capital-ws-streamer")
    t.start()
    log("WS streamer thread started")


# ── Full snapshot ────────────────────────────────────────────────────────────

def _build_chat_briefing():
    """Compact system briefing — state the trader has on screen right now.
    Kept small so the prompt stays cheap. Never includes secrets or API keys."""
    parts = []
    # State
    state = {}
    try:
        if STATE_FILE.exists():
            s = json.loads(STATE_FILE.read_text())
            state = s
    except Exception:
        state = {}

    bal = state.get("broker_balance", 1000)
    daily = state.get("daily_pnl", 0)
    total = state.get("total_pnl", 0)
    total_trades = state.get("total_trades", 0)
    open_pos = state.get("open_positions", []) or []
    regime_note = (state.get("regime_note") or "").replace("\n", " ").strip()
    if len(regime_note) > 600:
        regime_note = regime_note[:600] + "…"

    parts.append(f"CAPITAL  balance=${bal:.2f}  daily_pnl=${daily:.2f}  total_pnl=${total:.2f}  lifetime_trades={total_trades}  open_positions={len(open_pos)}/4")

    be = state.get("binary_event") or {}
    if be.get("active"):
        parts.append(f"BINARY_EVENT  {be.get('name','?')}  active=true  deadline={be.get('deadline_utc') or 'tbd'}  verified={be.get('verified')}  sources={len(be.get('sources') or [])}")
    else:
        parts.append(f"BINARY_EVENT  active=false  last_name={be.get('name','none')}")

    # Open positions detail
    for p in open_pos[:6]:
        pos = p.get("position", p) if isinstance(p, dict) else {}
        parts.append(f"OPEN  {pos.get('instrument','?')}  {pos.get('direction','?')}  entry={pos.get('level')}  sl={pos.get('stopLevel')}  tp={pos.get('profitLevel')}")

    # Trade history (brief)
    for t in (state.get("trade_history") or [])[-3:]:
        parts.append(f"TRADE  {t.get('instrument')}  {t.get('direction')}  {t.get('entry_price')}→{t.get('exit_price')}  pnl=${t.get('pnl')}  {t.get('result')}")

    # Watchlist
    try:
        if SIGNALS_FILE.exists():
            w = json.loads(SIGNALS_FILE.read_text())
            for a in (w.get("level_alerts") or []):
                note = (a.get("note") or "").replace("\n", " ").strip()
                if len(note) > 260: note = note[:260] + "…"
                parts.append(
                    f"ALERT  id={a.get('id')}  inst={a.get('instrument')}  {a.get('direction')}  level={a.get('level')}  "
                    f"live={a.get('current_price_ref')}  prox={a.get('proximity','')[:80]}  note: {note}"
                )
    except Exception:
        pass

    # Live prices
    try:
        with _ticks_lock:
            ticks = list(_latest_ticks.values())
        if ticks:
            tline = " ".join(f"{t['epic']}={(t['bid']+t['ofr'])/2:.5f}" for t in ticks)
            parts.append(f"PRICES  {tline}")
    except Exception:
        pass

    # Last 8 events
    try:
        evs = recent_events(8)
        for e in evs:
            ts = (e.get("ts_utc") or "")[11:16]
            parts.append(f"EVENT  {ts}  {e.get('type','?')}  {e.get('instrument','')}  {e.get('alert_id') or e.get('timeframe') or ''}")
    except Exception:
        pass

    if regime_note:
        parts.append(f"REGIME  {regime_note}")

    return "\n".join(parts)


_CHAT_SYSTEM = """You are an analyst embedded in a live forex trading dashboard.
The user is the trader. You're looking at the same state they see — a compact
briefing is prepended below.

## The system you're reasoning about

- $1,000 working capital on Capital.com demo. Swing risk 2%/trade, scalp 0.5%.
- 9 pairs watched: EURUSD, GBPUSD, AUDUSD, USDCAD, USDCHF, USDJPY, GOLD, OIL_CRUDE, BTCUSD.
- Confluence is TIERED: strong (|score|≥60 all-agree) / moderate (≥40, ≥n-1 agree) / weak (≥25, ≥2 agree) / none. Readiness drives sizing.
- Counter-trend fades (notes containing "intervention", "red line", "BoJ", "fade", "overextended") use OPPOSING confluence as corroborating, not blocking.
- Scalp engine runs mechanically on EURUSD/GBPUSD/AUDUSD (shadow mode). Three setups: range_extreme, session_open_break, ema_pullback.
- Liquidity-sweep events fire when price wicks beyond a 20-bar extreme and closes back inside — fresh bounce points the tape just created.
- News-reactive audits: a breaking headline matching an alert's keywords emits `alert_audit_request` for a targeted re-audit.
- Orders route through risk_guard for hard safety checks.

## Your job

Answer the user's question directly, in plain English, grounded in the briefing.
Cite specific alert IDs, levels, pip distances, readiness tiers when they matter.
Prefer the system's vocabulary (readiness/moderate, counter-trend fade, sweep,
liquidity_sweep, H1 ATR proximity, scalp shadow mode).

DO NOT invoke tools. DO NOT place or modify trades. If asked to act, explain
what you'd do and tell the user to execute via the main tick flow. Treat the
briefing as read-only — you're commenting on it, not changing it.

## Format

Short paragraphs or tight bullet lists. Inline code for prices/IDs. Avoid
boilerplate. Don't repeat the briefing back to the user — they already have it
on screen. If the answer depends on info not in the briefing (e.g. a chart
pattern you can't see), say so explicitly."""


def _compose_chat_prompt(briefing, messages):
    hist_lines = []
    for m in messages:
        role = m.get("role")
        content = str(m.get("content") or "").strip()
        if not content: continue
        if role == "user":
            hist_lines.append(f"USER: {content}")
        elif role == "assistant":
            hist_lines.append(f"ASSISTANT: {content}")
    history = "\n\n".join(hist_lines)

    return (
        _CHAT_SYSTEM
        + "\n\n---\n## LIVE BRIEFING (auto-generated from state)\n\n"
        + briefing
        + "\n\n---\n## CONVERSATION\n\n"
        + history
        + "\n\nASSISTANT:"
    )


def _stream_claude_chat(handler, prompt):
    """Pipe `claude -p --output-format stream-json` output to the browser as SSE.

    stream-json emits one JSON object per line. We only forward:
      • text_delta events  → {"delta": "..."}
      • final result event → {"done": true, "cost_usd": ...}
      • any error          → {"error": "...", "detail": "..."}

    Thinking / metadata / tool events are silently dropped. The prompt enters
    via stdin so nothing shell-quoted. 120s hard wall-clock timeout.
    """
    claude_bin = os.environ.get("CLAUDE_BIN", "/Users/rajneeshmishra/.local/bin/claude")
    # Default: Sonnet 4.6. Was Haiku 4.5 for cost, but user requested consistent
    # Sonnet across all Claude invocations (ticks + chat). Budget bumped from
    # 0.12 to 0.30 because Sonnet costs ~5-8× Haiku on equivalent output.
    model = os.environ.get("CHAT_MODEL", "claude-sonnet-4-6")
    cmd = [
        claude_bin, "-p",
        "--model", model,
        "--output-format", "stream-json",
        "--include-partial-messages",   # token-level deltas, required for real streaming
        "--verbose",                    # required alongside stream-json
        "--dangerously-skip-permissions",
        "--max-budget-usd", "0.30",
    ]

    # Send SSE headers first so the browser starts consuming immediately
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache, no-transform")
        handler.send_header("X-Accel-Buffering", "no")
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
    except Exception:
        return

    def write_event(payload):
        try:
            handler.wfile.write(f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8"))
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(REPO),
            bufsize=1,  # line-buffered
            text=True,
        )
    except FileNotFoundError:
        try: write_event({"error": "cli_missing", "detail": f"claude binary not at {claude_bin}"})
        except Exception: pass
        return
    except Exception as e:
        try: write_event({"error": "spawn_failed", "detail": str(e)})
        except Exception: pass
        return

    # Pipe prompt in, then close stdin so claude can start generating
    try:
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()
    except Exception as e:
        try: write_event({"error": "write_failed", "detail": str(e)})
        except Exception: pass
        try: proc.kill()
        except Exception: pass
        return

    start = time.time()
    TIMEOUT = 120
    cost_usd = None
    received_any_text = False

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if time.time() - start > TIMEOUT:
                try: write_event({"error": "timeout", "detail": f"no completion within {TIMEOUT}s"})
                except Exception: pass
                proc.kill()
                return

            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            t = obj.get("type")
            if t == "stream_event":
                ev = obj.get("event") or {}
                if ev.get("type") == "content_block_delta":
                    delta = ev.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text")
                        if text:
                            received_any_text = True
                            try:
                                write_event({"delta": text})
                            except (BrokenPipeError, ConnectionResetError):
                                proc.kill()
                                return
            elif t == "result":
                cost_usd = obj.get("total_cost_usd")
                break

        # Drain and close
        try: proc.wait(timeout=5)
        except Exception:
            try: proc.kill()
            except Exception: pass

        if proc.returncode and proc.returncode != 0:
            err_detail = ""
            try:
                err_detail = (proc.stderr.read() or "").strip()[:500] if proc.stderr else ""
            except Exception:
                pass
            if not received_any_text:
                try: write_event({"error": "nonzero_exit", "detail": err_detail or f"exit {proc.returncode}"})
                except Exception: pass
                return

        try: write_event({"done": True, "cost_usd": cost_usd})
        except Exception: pass
    except (BrokenPipeError, ConnectionResetError):
        try: proc.kill()
        except Exception: pass
    except Exception as e:
        try: write_event({"error": "stream_failed", "detail": str(e)[:500]})
        except Exception: pass
        try: proc.kill()
        except Exception: pass


def full_snapshot():
    broker = broker_snapshot()
    with _ticks_lock:
        live_ticks = dict(_latest_ticks)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "daemons": daemon_snapshot(),
        "broker": broker,
        "watchlist": watchlist_snapshot(broker),
        "events_total": _count_events(),
        "events_unconsumed": _count_unconsumed(),
        "state_file": _read_state_minimal(),
        "live_ticks": live_ticks,
        "ws_stats": _ws_stats,
    }


def _count_events():
    if not EVENTS_FILE.exists():
        return 0
    try:
        return sum(1 for _ in EVENTS_FILE.open())
    except Exception:
        return 0


def _count_unconsumed():
    if not EVENTS_FILE.exists():
        return 0
    consumed = load_consumed_ids()
    n = 0
    with EVENTS_FILE.open() as f:
        for line in f:
            try:
                d = json.loads(line)
                if d.get("event_id") and d["event_id"] not in consumed and not d.get("consumed_by_claude"):
                    n += 1
            except Exception:
                pass
    return n


def _read_state_minimal():
    if not STATE_FILE.exists():
        return {}
    try:
        s = json.loads(STATE_FILE.read_text())
        return {
            "regime": s.get("regime"),
            "regime_note": s.get("regime_note"),
            "last_tick": s.get("last_tick"),
            "last_tick_utc": s.get("last_tick_utc") or s.get("last_tick"),
            "daily_pnl": s.get("daily_pnl"),
            "total_pnl": s.get("total_pnl"),
            "total_trades": s.get("total_trades"),
            "consecutive_losses": s.get("consecutive_losses"),
            "binary_event": s.get("binary_event"),
            "trade_history": s.get("trade_history", []),
            "open_positions": s.get("open_positions", []),
            "broker_balance": s.get("broker_balance"),
        }
    except Exception:
        return {}


# ── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Quiet the default-noisy stderr logger
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            u = urlparse(self.path)
            if u.path == "/api/chat":
                return self._handle_chat()
            self._json({"error": "not found", "path": u.path}, status=404)
        except Exception as e:
            try:
                self._json({"error": str(e)}, status=500)
            except Exception:
                pass

    def _handle_chat(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 50_000:
            return self._json({"error": "payload too large"}, status=413)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._json({"error": "invalid JSON"}, status=400)

        messages = data.get("messages") or []
        if not isinstance(messages, list) or not messages:
            return self._json({"error": "messages[] required"}, status=400)
        messages = messages[-16:]
        for m in messages:
            if not isinstance(m, dict): continue
            if isinstance(m.get("content"), str) and len(m["content"]) > 4000:
                m["content"] = m["content"][:4000]

        briefing = _build_chat_briefing()
        prompt = _compose_chat_prompt(briefing, messages)
        _stream_claude_chat(self, prompt)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        try:
            if u.path == "/" or u.path == "/index.html":
                self._html(HTML_FILE.read_bytes())
            elif u.path == "/api/snapshot":
                self._json(full_snapshot())
            elif u.path == "/api/live_ticks":
                # Fast path — in-memory dict, no broker calls. Used by the
                # scalp engine which polls every 1s and needs deterministic
                # latency. Full snapshot can take >3s on broker cache miss.
                with _ticks_lock:
                    ticks_copy = dict(_latest_ticks)
                self._json({"live_ticks": ticks_copy, "ts_ms": int(time.time() * 1000)})
            elif u.path == "/api/events":
                n = int(q.get("n", ["50"])[0])
                self._json({"events": recent_events(n)})
            elif u.path == "/api/events/stream":
                self._stream_events()
            elif u.path == "/api/ticks/stream":
                self._stream_ticks()
            elif u.path == "/api/candles":
                epic = q.get("epic", [""])[0]
                resolution = q.get("resolution", ["HOUR"])[0]
                count = int(q.get("count", ["200"])[0])
                if not epic:
                    self._json({"error": "epic required"}, status=400)
                else:
                    self._json({
                        "epic": epic, "resolution": resolution,
                        "candles": fetch_candles(epic, resolution, count),
                    })
            elif u.path == "/api/chart_context":
                epic = q.get("epic", [""])[0]
                if not epic:
                    self._json({"error": "epic required"}, status=400)
                else:
                    self._json(chart_context(epic))
            elif u.path == "/api/gates":
                epic = q.get("epic", [""])[0]
                if not epic:
                    self._json({"error": "epic required"}, status=400)
                else:
                    self._json(evaluate_gates(epic))
            elif u.path == "/api/gates/all":
                self._json({"instruments": gates_all()})
            elif u.path == "/api/ticks":
                self._json({"ticks": recent_ticks(15)})
            elif u.path == "/api/control":
                daemon = q.get("daemon", [""])[0]
                action = q.get("action", [""])[0]
                self._json(self._set_control(daemon, action))
            elif u.path.startswith("/data/") or u.path.startswith("/assets/"):
                # Serve static published files from docs/ (same tree served by
                # GitHub Pages). Scoped strictly to docs/ so we can't escape.
                rel = u.path.lstrip("/")
                docs_root = (REPO / "docs").resolve()
                target = (REPO / "docs" / rel).resolve()
                if not str(target).startswith(str(docs_root)):
                    self._json({"error": "forbidden"}, status=403)
                elif not target.exists() or not target.is_file():
                    self._json({"error": "not found", "path": u.path}, status=404)
                else:
                    ctype_map = {
                        ".json": "application/json",
                        ".js":   "application/javascript; charset=utf-8",
                        ".css":  "text/css; charset=utf-8",
                        ".svg":  "image/svg+xml",
                        ".png":  "image/png",
                        ".jpg":  "image/jpeg",
                        ".woff2": "font/woff2",
                        ".woff":  "font/woff",
                        ".html": "text/html; charset=utf-8",
                    }
                    ctype = ctype_map.get(target.suffix, "application/octet-stream")
                    body = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    # Hashed assets can be cached long-term; JSON should not
                    if u.path.startswith("/assets/"):
                        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                    else:
                        self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
            else:
                self._json({"error": "not found", "path": u.path}, status=404)
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._json({"error": str(e)}, status=500)
            except Exception:
                pass

    def _stream_events(self):
        """SSE — tail the events file and push each new line as `data: ...\\n\\n`."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        if not EVENTS_FILE.exists():
            EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            EVENTS_FILE.touch()

        last_size = EVENTS_FILE.stat().st_size
        try:
            # Initial heartbeat
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()

            while True:
                time.sleep(1)
                try:
                    size = EVENTS_FILE.stat().st_size
                except FileNotFoundError:
                    continue
                if size > last_size:
                    with EVENTS_FILE.open() as f:
                        f.seek(last_size)
                        new = f.read()
                    last_size = size
                    for line in new.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        self.wfile.write(f"data: {line}\n\n".encode())
                        self.wfile.flush()
                elif size < last_size:
                    last_size = size
                else:
                    # Comment heartbeat every ~15s to keep connection alive
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _stream_ticks(self):
        """SSE — push every new Capital.com tick to browser.
        On connect, send current snapshot so the UI has data immediately."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q = queue.Queue(maxsize=500)
        with _subs_lock:
            _tick_subs.append(q)

        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()

            # Initial snapshot of what we already know
            with _ticks_lock:
                snap = list(_latest_ticks.values())
            for tick in snap:
                self.wfile.write(f"data: {json.dumps(tick, default=str)}\n\n".encode())
            self.wfile.flush()

            while True:
                try:
                    tick = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(tick, default=str)}\n\n".encode())
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _subs_lock:
                try:
                    _tick_subs.remove(q)
                except ValueError:
                    pass

    def _set_control(self, daemon_name, action):
        meta = next((d for d in DAEMON_META if d["name"] == daemon_name), None)
        if not meta:
            return {"ok": False, "error": f"unknown daemon: {daemon_name}"}
        if action not in ("run", "pause", "stop"):
            return {"ok": False, "error": f"bad action: {action}"}
        path = REPO / meta["ctrl"]
        path.write_text(action)
        return {"ok": True, "daemon": daemon_name, "action": action}


def main():
    if not HTML_FILE.exists():
        log(f"WARN: {HTML_FILE} missing")
    start_ws_streamer()
    log(f"dashboard listening on http://localhost:{PORT}  (repo={REPO})")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
