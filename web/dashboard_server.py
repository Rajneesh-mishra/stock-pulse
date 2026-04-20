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
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "forex"))

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


# ── Full snapshot ────────────────────────────────────────────────────────────

def full_snapshot():
    broker = broker_snapshot()
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "daemons": daemon_snapshot(),
        "broker": broker,
        "watchlist": watchlist_snapshot(broker),
        "events_total": _count_events(),
        "events_unconsumed": _count_unconsumed(),
        "state_file": _read_state_minimal(),
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
    log(f"dashboard listening on http://localhost:{PORT}  (repo={REPO})")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
