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
"""

import fcntl
import json
import os
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
LOG = REPO / "logs" / "claude_waker.log"
PROMPT_FILE = REPO / "prompts" / "forex_tick.md"

DEBOUNCE_SEC = 15                # min gap between Claude invocations
POLL_SEC = 2                     # how often we check for new events
TICK_BUDGET_USD = 1.50           # max spend per Claude invocation

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


def count_unconsumed():
    seen = set()
    if CONSUMED.exists():
        seen = {l.strip() for l in CONSUMED.read_text().splitlines() if l.strip()}
    n = 0
    if EVENTS.exists():
        with EVENTS.open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if (d.get("event_id")
                            and d["event_id"] not in seen
                            and not d.get("consumed_by_claude")):
                        n += 1
                except Exception:
                    pass
    return n


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


def invoke_claude():
    """Run `claude -p` with the forex_tick prompt. Returns exit code."""
    prompt = PROMPT_FILE.read_text()
    cmd = [
        "claude", "-p", prompt,
        "--dangerously-skip-permissions",
        "--max-budget-usd", str(TICK_BUDGET_USD),
        "--output-format", "text",
        "--add-dir", str(REPO),
    ]
    log(f"invoking: claude -p <{PROMPT_FILE.name}> --max-budget-usd {TICK_BUDGET_USD}")
    try:
        with LOG.open("a") as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT,
                               cwd=str(REPO), timeout=900)
        return r.returncode
    except subprocess.TimeoutExpired:
        log("claude timed out after 900s")
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

    n = count_unconsumed()
    if n == 0:
        return

    fd = acquire_lock()
    if fd is None:
        log("lock held by another tick — skipping")
        return

    try:
        log(f"{n} unconsumed events — invoking Claude")
        state["last_tick"] = now
        code = invoke_claude()
        if code == 0:
            log("tick complete")
        else:
            log(f"tick exited with {code}")
            if code > 1:
                # Real error — alert
                try:
                    subprocess.run(["bash", "send_telegram.sh",
                                    f"🚨 Claude waker error: exit {code} — check logs/claude_waker.log"],
                                   cwd=str(REPO), timeout=10)
                except Exception as e:
                    log(f"telegram alert failed: {e}")
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
