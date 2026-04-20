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

from technicals import get_full_candles  # noqa: E402

PORT = int(os.environ.get("DASHBOARD_PORT", "8787"))
HTML_FILE = REPO / "web" / "dashboard.html"
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
            "daily_pnl": s.get("daily_pnl"),
            "total_pnl": s.get("total_pnl"),
            "total_trades": s.get("total_trades"),
            "consecutive_losses": s.get("consecutive_losses"),
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

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        try:
            if u.path == "/" or u.path == "/index.html":
                self._html(HTML_FILE.read_bytes())
            elif u.path == "/api/snapshot":
                self._json(full_snapshot())
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
            elif u.path == "/api/ticks":
                self._json({"ticks": recent_ticks(15)})
            elif u.path == "/api/control":
                daemon = q.get("daemon", [""])[0]
                action = q.get("action", [""])[0]
                self._json(self._set_control(daemon, action))
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
