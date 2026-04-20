#!/usr/bin/env python3
"""
Heartbeat Claude invocation. Fires hourly via launchd StartInterval.

One-shot — fires, invokes claude, exits. Same lock as event waker (if waker
is mid-tick, heartbeat skips). Purpose: catch things events miss — position
review, news drift, end-of-day housekeeping.
"""

import fcntl
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/rajneeshmishra/Downloads/stock-pulse")
os.chdir(REPO)

CTRL = REPO / "state" / "forex_heartbeat.control"   # run | pause | stop
LOCK = REPO / "state" / ".forex_claude.lock"
LOG = REPO / "logs" / "claude_heartbeat.log"
PROMPT_FILE = REPO / "prompts" / "forex_tick.md"

TICK_BUDGET_USD = 1.50
TICK_TIMEOUT_SEC = 600

LOG.parent.mkdir(parents=True, exist_ok=True)


def log(msg):
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
    with LOG.open("a") as f:
        f.write(f"[{stamp}] {msg}\n")


def main():
    if CTRL.exists():
        ctrl = CTRL.read_text().strip().lower()
        if ctrl in ("stop", "pause"):
            log(f"control={ctrl} — skipping")
            return 0

    # Non-blocking lock
    fd = os.open(str(LOCK), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("lock held by waker — skipping heartbeat")
        os.close(fd)
        return 0

    try:
        log(f"heartbeat tick starting, pid={os.getpid()}")
        prompt = PROMPT_FILE.read_text()
        cmd = [
            "claude", "-p", prompt,
            "--dangerously-skip-permissions",
            "--max-budget-usd", str(TICK_BUDGET_USD),
            "--output-format", "text",
            "--add-dir", str(REPO),
        ]
        try:
            with LOG.open("a") as f:
                r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT,
                                   cwd=str(REPO), timeout=TICK_TIMEOUT_SEC)
            log(f"heartbeat exit {r.returncode}")
            return r.returncode
        except subprocess.TimeoutExpired:
            log(f"heartbeat timed out after {TICK_TIMEOUT_SEC}s")
            return -1
        except FileNotFoundError:
            log("claude CLI not found on PATH")
            return -2
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
