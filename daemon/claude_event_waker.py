#!/usr/bin/env python3
"""
Event-triggered Claude waker (Python version).

Runs under launchd (com.stockpulse.claudewaker) with KeepAlive. Homebrew python
has Full Disk Access on this machine; /bin/bash does not, so the previous bash
implementation hit macOS TCC "Operation not permitted" on the Downloads folder.

Watches state/forex_events.jsonl via inotify-style poll. On every new line,
checks for unconsumed events (event_id not in forex_events_consumed.txt).
When found, invokes `claude -p` non-interactively to process them. A file
lock ensures at most one Claude tick runs at a time (shared with heartbeat).

Event filtering:
  - `bar_close` events are NOISE for wake decisions — the watcher emits 500+/day
    across 9 pairs × multiple timeframes. They're useful as persisted structure
    data but should not pay $1/tick each. Auto-consumed without invoking Claude.
  - During a binary event (forex_state.json.binary_event.active), only
    actionable event types wake Claude. Other types are auto-consumed.
  - Duplicate news_flash events (same keywords as recent) are auto-consumed.

Timeout: Claude invocations are hard-killed at 600s (10 min). A stuck tick
eats API budget silently; the old 900s limit allowed 89-minute runaways.
"""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/rajneeshmishra/Downloads/stock-pulse")
os.chdir(REPO)

CTRL = REPO / "state" / "forex_event_waker.control"   # run | pause | stop
LOCK = REPO / "state" / ".forex_claude.lock"
EVENTS = REPO / "state" / "forex_events.jsonl"
CONSUMED = REPO / "state" / "forex_events_consumed.txt"
STATE = REPO / "state" / "forex_state.json"
LOG = REPO / "logs" / "claude_waker.log"
PROMPT_FILE = REPO / "prompts" / "forex_tick.md"

DEBOUNCE_SEC = 15                # min gap between Claude invocations
POLL_SEC = 2                     # how often we check for new events
TICK_BUDGET_USD = 1.50           # max spend per Claude invocation
TICK_TIMEOUT_SEC = 600           # hard kill after 10 min (was 900 — 89min runaway caught)

# Event types that always wake Claude (actionable).
WAKE_EVENT_TYPES = {
    "level_enter",
    "level_exit",
    "level_cross",
    "liquidity_sweep",          # NEW — fresh bounce point created by the tape
    "alert_audit_request",      # NEW — news matched an active alert's keywords
    "structure_bos",
    "structure_choch",
    "trail_candidate",
    "position_opened",
    "position_closed",
    "sl_hit",
    "tp_hit",
    "daily_pnl_threshold",
    "volatility_spike",
    "news_flash",               # filtered further by dedup below
}

# Event types that are auto-consumed without waking Claude (pure noise for tick decisions).
# bar_close is still valuable as persisted structure data — watcher keeps emitting it,
# Claude can re-read it on the next actionable tick.
AUTO_CONSUME_TYPES = {
    "bar_close",
}

# Critical event types that wake Claude even during a binary-event suppression window.
BINARY_MODE_WAKE_TYPES = {
    "level_enter",
    "level_cross",
    "liquidity_sweep",          # sweep = real edge even during binary event mode
    "alert_audit_request",
    "trail_candidate",
    "position_opened",
    "position_closed",
    "sl_hit",
    "tp_hit",
    "daily_pnl_threshold",
}

# News keywords that force-wake even during binary mode or dedup windows.
CRITICAL_NEWS_KEYWORDS = (
    "breaking", "emergency", "strike", "attack", "ceasefire",
    "fed", "fomc", "rate cut", "rate hike", "hikes", "cuts",
    "intervention", "boj", "ecb", "rba", "snb",
    "halt", "crash", "circuit breaker", "force majeure",
)

LOG.parent.mkdir(parents=True, exist_ok=True)
CONSUMED.parent.mkdir(parents=True, exist_ok=True)
CONSUMED.touch(exist_ok=True)


def log(msg):
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
    with LOG.open("a") as f:
        f.write(f"[{stamp}] {msg}\n")


def read_control():
    if not CTRL.exists():
        return "run"
    try:
        val = CTRL.read_text().strip().lower()
        return val if val in ("run", "pause", "stop") else "run"
    except Exception:
        return "run"


def load_binary_event():
    """Return (active: bool, name: str) for the current binary event, if any.

    forex_state.json.binary_event = {
        "name": "iran_ceasefire_deadline",
        "deadline_utc": "2026-04-23T23:00:00Z",
        "active": true,
        "sources": ["cnn.com/...", "reuters.com/..."]
    }
    """
    if not STATE.exists():
        return False, None
    try:
        s = json.loads(STATE.read_text())
        b = s.get("binary_event") or {}
        if b.get("active"):
            return True, b.get("name", "binary_event")
    except Exception:
        pass
    return False, None


def append_consumed(event_ids):
    if not event_ids:
        return
    with CONSUMED.open("a") as f:
        for eid in event_ids:
            f.write(eid + "\n")


def classify_pending():
    """Scan unconsumed events and split them into:
       - wake: IDs that warrant a Claude invocation
       - auto_consume: IDs to mark consumed without waking
       Returns (wake_events, auto_consume_ids).
    """
    seen = set()
    if CONSUMED.exists():
        seen = {l.strip() for l in CONSUMED.read_text().splitlines() if l.strip()}

    binary_active, binary_name = load_binary_event()

    wake_events = []
    auto_consume = []
    recent_news_keys = []  # dedup news_flash within this batch

    if not EVENTS.exists():
        return wake_events, auto_consume

    with EVENTS.open() as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            eid = d.get("event_id")
            if not eid or eid in seen or d.get("consumed_by_claude"):
                continue

            etype = d.get("type", "")

            # 1) Hard noise — bar_close, volatility_spike-alike: auto-consume
            if etype in AUTO_CONSUME_TYPES:
                auto_consume.append(eid)
                continue

            # 2) Binary-event mode: suppress everything except the critical set
            if binary_active and etype not in BINARY_MODE_WAKE_TYPES:
                if etype == "news_flash":
                    text = (d.get("headline", "") + " " + d.get("body", "")).lower()
                    if any(kw in text for kw in CRITICAL_NEWS_KEYWORDS):
                        wake_events.append(d)
                    else:
                        auto_consume.append(eid)
                else:
                    auto_consume.append(eid)
                continue

            # 3) News dedup within batch: same query/keyword cluster → one wake,
            #    rest auto-consumed as duplicates
            if etype == "news_flash":
                key = (d.get("query_id") or "") + "|" + (d.get("headline", "")[:40].lower())
                if key in recent_news_keys:
                    auto_consume.append(eid)
                    continue
                recent_news_keys.append(key)
                wake_events.append(d)
                continue

            # 4) Default: actionable event
            if etype in WAKE_EVENT_TYPES:
                wake_events.append(d)
            else:
                # Unknown type — pass through to Claude, don't drop silently
                wake_events.append(d)

    return wake_events, auto_consume


def acquire_lock():
    """Returns the open file descriptor if locked, None if another holder."""
    fd = os.open(str(LOCK), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def release_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def send_telegram(msg):
    try:
        subprocess.run(["bash", "send_telegram.sh", msg],
                       cwd=str(REPO), timeout=10)
    except Exception as e:
        log(f"telegram alert failed: {e}")


def invoke_claude():
    """Run `claude -p` with the forex_tick prompt. Returns exit code.
    Hard-kills the process group on TICK_TIMEOUT_SEC to prevent runaway spend."""
    prompt = PROMPT_FILE.read_text()
    # Default model: Sonnet 4.6 — higher-quality reasoning for the tick's
    # multi-step protocol (confluence + sizing + audit + commit). Override via
    # TICK_MODEL env if needed.
    model = os.environ.get("TICK_MODEL", "claude-sonnet-4-6")
    cmd = [
        "claude", "-p", prompt,
        "--model", model,
        "--dangerously-skip-permissions",
        "--max-budget-usd", str(TICK_BUDGET_USD),
        "--output-format", "text",
        "--add-dir", str(REPO),
    ]
    log(f"invoking: claude -p <{PROMPT_FILE.name}> --max-budget-usd {TICK_BUDGET_USD} timeout={TICK_TIMEOUT_SEC}s")
    t0 = time.time()
    try:
        with LOG.open("a") as f:
            # start_new_session=True puts Claude in its own process group so
            # timeout kills the whole tree (including any child processes it spawned).
            p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                 cwd=str(REPO), start_new_session=True)
            try:
                rc = p.wait(timeout=TICK_TIMEOUT_SEC)
                log(f"tick completed in {int(time.time()-t0)}s with exit={rc}")
                return rc
            except subprocess.TimeoutExpired:
                elapsed = int(time.time() - t0)
                log(f"tick TIMEOUT after {elapsed}s — SIGKILL process group {p.pid}")
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except Exception as e:
                    log(f"killpg failed: {e}")
                    try: p.kill()
                    except Exception: pass
                try: p.wait(timeout=5)
                except Exception: pass
                send_telegram(
                    f"🚨 Forex tick TIMEOUT after {elapsed}s — killed. "
                    f"Budget ~${TICK_BUDGET_USD:.2f} burned. Check logs/claude_waker.log"
                )
                return -1
    except FileNotFoundError:
        log("claude CLI not found on PATH — waker cannot function")
        return -2


def process_pending(state):
    ctrl = read_control()
    if ctrl == "stop":
        log("control=stop → exiting")
        sys.exit(0)
    if ctrl == "pause":
        return

    now = time.time()
    if now - state["last_tick"] < DEBOUNCE_SEC:
        return

    wake_events, auto_consume = classify_pending()

    # Always auto-consume the noise pile up front so the queue doesn't grow.
    if auto_consume:
        append_consumed(auto_consume)
        log(f"auto-consumed {len(auto_consume)} noise events (bar_close/dupe/binary-mode)")

    if not wake_events:
        return

    fd = acquire_lock()
    if fd is None:
        log("lock held by another tick — skipping")
        return

    try:
        # Summarize what's waking us up
        counts = {}
        for e in wake_events:
            counts[e.get("type", "?")] = counts.get(e.get("type", "?"), 0) + 1
        summary = ", ".join(f"{k}×{v}" for k, v in counts.items())
        log(f"{len(wake_events)} wake events [{summary}] — invoking Claude")
        state["last_tick"] = now
        code = invoke_claude()
        if code == 0:
            log("tick complete")
        else:
            log(f"tick exited with {code}")
            if code > 1:
                send_telegram(f"🚨 Claude waker error: exit {code} — check logs/claude_waker.log")
    finally:
        release_lock(fd)


def tail_loop():
    """Poll events file size; when it grows, call process_pending."""
    state = {"last_tick": 0.0, "last_size": 0}
    if EVENTS.exists():
        state["last_size"] = EVENTS.stat().st_size

    log(f"claude_event_waker starting, pid={os.getpid()}, repo={REPO}")

    # Initial drain — handle anything already pending
    process_pending(state)

    while True:
        ctrl = read_control()
        if ctrl == "stop":
            log("control=stop → exiting")
            return 0

        try:
            size = EVENTS.stat().st_size if EVENTS.exists() else 0
            if size > state["last_size"]:
                state["last_size"] = size
                process_pending(state)
            elif size < state["last_size"]:
                # File was truncated or replaced
                state["last_size"] = size
        except Exception as e:
            log(f"poll error: {e}")

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        sys.exit(tail_loop())
    except KeyboardInterrupt:
        log("KeyboardInterrupt → exiting")
        sys.exit(0)
