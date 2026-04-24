#!/usr/bin/env python3
"""
Database persister — tails the JSONL streams and mirrors them into SQLite.

Writes to state/pulse.db. Source files:
  state/forex_events.jsonl               → events table
  state/forex_scalp_ledger.jsonl         → trades table (scalp)
  state/forex_alert_counterfactuals.jsonl → counterfactuals table

Also polls:
  state/forex_state.json                 → regime_snapshots, trades (swing)
  broker positions                       → broker_positions table

Cursor files in state/.db_persister_cursor/ track the byte offset of each
tailed file so we only write new rows on each poll.

Periodically dumps a snapshot JSON to docs/data/forex/db_snapshot.json so
git can hold a durable backup even if the .db file is lost.

Safe to restart — all writes use UNIQUE constraints + INSERT OR IGNORE.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/rajneeshmishra/Downloads/stock-pulse")
os.chdir(REPO)

DB_PATH           = REPO / "state" / "pulse.db"
STATE_FILE        = REPO / "state" / "forex_state.json"
EVENTS_FILE       = REPO / "state" / "forex_events.jsonl"
CONSUMED_FILE     = REPO / "state" / "forex_events_consumed.txt"
CF_LEDGER         = REPO / "state" / "forex_alert_counterfactuals.jsonl"
SCALP_LEDGER      = REPO / "state" / "forex_scalp_ledger.jsonl"
SNAPSHOT_OUT      = REPO / "docs" / "data" / "forex" / "db_snapshot.json"
LOG_FILE          = REPO / "logs" / "db_persister.log"
CURSOR_DIR        = REPO / "state" / ".db_persister_cursor"
CTRL_FILE         = REPO / "state" / "db_persister.control"

CURSOR_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

POLL_SEC = 5
SNAPSHOT_EVERY_SEC = 300        # dump JSON snapshot every 5 min
BROKER_POLL_EVERY_SEC = 60      # poll broker positions every 60s


def log(msg):
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
    with LOG_FILE.open("a") as f:
        f.write(f"[{stamp}] {msg}\n")


def read_control():
    if not CTRL_FILE.exists(): return "run"
    try:
        v = CTRL_FILE.read_text().strip().lower()
        return v if v in ("run", "pause", "stop") else "run"
    except Exception:
        return "run"


# ── Cursor helpers ────────────────────────────────────────────────────────

def cursor_path(name): return CURSOR_DIR / name

def read_cursor(name):
    p = cursor_path(name)
    if not p.exists(): return 0
    try: return int(p.read_text().strip() or 0)
    except Exception: return 0

def write_cursor(name, pos):
    cursor_path(name).write_text(str(pos))


# ── DB connection ─────────────────────────────────────────────────────────

def db_connect():
    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


# ── Tailers ──────────────────────────────────────────────────────────────

def tail_events(con):
    if not EVENTS_FILE.exists(): return 0
    pos = read_cursor("events")
    size = EVENTS_FILE.stat().st_size
    if pos >= size: return 0
    consumed = set()
    if CONSUMED_FILE.exists():
        consumed = {l.strip() for l in CONSUMED_FILE.read_text().splitlines() if l.strip()}
    before = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    with EVENTS_FILE.open() as f:
        f.seek(pos)
        for line in f:
            try: d = json.loads(line)
            except Exception: continue
            eid = d.get("event_id")
            if not eid: continue
            payload = d.get("payload") or {}
            try:
                con.execute("""INSERT OR IGNORE INTO events
                    (event_id, type, ts_utc, instrument, alert_id, direction,
                     timeframe, consumed, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (eid, d.get("type"), d.get("ts_utc"), d.get("instrument"),
                     d.get("alert_id"), d.get("direction") or payload.get("direction"),
                     d.get("timeframe") or payload.get("timeframe"),
                     1 if eid in consumed else 0,
                     json.dumps(d, default=str)))
            except Exception as e:
                log(f"events insert {eid}: {e}")
        pos = f.tell()
    write_cursor("events", pos)
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return after - before


def tail_scalp_trades(con):
    """Pair open → close from the scalp ledger, insert into trades.
    Returns real row count added (not con.total_changes delta, which
    includes ignored INSERTs and misleads the log)."""
    if not SCALP_LEDGER.exists(): return 0
    rows = []
    for line in SCALP_LEDGER.open():
        try: rows.append(json.loads(line))
        except Exception: pass
    opens = {}
    before = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    for r in rows:
        k = r.get("kind"); epic = r.get("epic")
        if k == "opened" and epic:
            opens[epic] = r
        elif k == "closed" and epic and epic in opens:
            o = opens.pop(epic)
            try:
                ot = datetime.fromisoformat(o["ts_utc"].replace("Z","+00:00"))
                ct = datetime.fromisoformat(r["ts_utc"].replace("Z","+00:00"))
                held = (ct - ot).total_seconds() / 60
            except Exception:
                held = r.get("held_min")
            deal_id = None
            br = o.get("broker_ref")
            if isinstance(br, dict):
                deal_id = br.get("dealId")
            con.execute("""INSERT OR IGNORE INTO trades
                (source, epic, direction, size, entry_price, sl, tp,
                 exit_price, opened_at, closed_at, open_reason,
                 close_reason, thesis_type, realized_pnl_usd,
                 realized_pnl_pips, held_minutes, shadow,
                 broker_deal_id, raw)
                VALUES ('scalp',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (epic, o.get("direction"), o.get("size") or 1.0,
                 o.get("entry"), o.get("sl"), o.get("tp"), r.get("exit"),
                 o["ts_utc"], r["ts_utc"], o.get("setup"), r.get("how"),
                 "scalp_entry", r.get("pnl_usd"), None, held,
                 1 if o.get("shadow") else 0, deal_id,
                 json.dumps({"open": o, "close": r}, default=str)))
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    return after - before


def tail_swing_trades_from_state(con):
    """state_file.trade_history — re-read each poll, insert missing ones."""
    if not STATE_FILE.exists(): return 0
    try:
        s = json.loads(STATE_FILE.read_text())
    except Exception:
        return 0
    before = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    for t in (s.get("trade_history") or []):
        con.execute("""INSERT OR IGNORE INTO trades
            (source, epic, direction, size, entry_price, sl, tp,
             exit_price, opened_at, closed_at, open_reason,
             close_reason, thesis_type, realized_pnl_usd,
             realized_pnl_pips, held_minutes, shadow, broker_deal_id, raw)
            VALUES ('swing',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t.get("instrument"), t.get("direction"), t.get("size") or 1.0,
             t.get("entry_price"), t.get("sl"), t.get("tp"), t.get("exit_price"),
             t.get("opened_at"), t.get("closed_at"),
             t.get("open_reason"), t.get("result") or t.get("exit_reason"),
             None, t.get("pnl"), None, None, 0, None,
             json.dumps(t, default=str)))
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    return after - before


def tail_regime(con):
    """Append a regime snapshot if the note changed since last write."""
    if not STATE_FILE.exists(): return 0
    try:
        s = json.loads(STATE_FILE.read_text())
    except Exception:
        return 0
    note = s.get("regime_note") or ""
    if not note: return 0
    # Get last stored note; skip if unchanged
    cur = con.execute("SELECT note_text FROM regime_snapshots ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    if row and row["note_text"] == note:
        return 0
    be = s.get("binary_event") or {}
    con.execute("""INSERT INTO regime_snapshots
        (ts_utc, regime_name, note_text, binary_event_name,
         binary_event_active, binary_event_deadline)
        VALUES (?,?,?,?,?,?)""",
        (s.get("tick_ts_utc") or datetime.now(timezone.utc).isoformat(),
         s.get("regime"), note, be.get("name"),
         1 if be.get("active") else 0, be.get("deadline_utc")))
    con.commit()
    return 1


def tail_counterfactuals(con):
    if not CF_LEDGER.exists(): return 0
    pos = read_cursor("counterfactuals")
    size = CF_LEDGER.stat().st_size
    if pos >= size: return 0
    before = con.execute("SELECT COUNT(*) FROM counterfactuals").fetchone()[0]
    with CF_LEDGER.open() as f:
        f.seek(pos)
        for line in f:
            try: d = json.loads(line)
            except Exception: continue
            if d.get("kind") != "alert_fired": continue
            cps = d.get("checkpoints") or {}
            def _hit(h):
                cp = cps.get(h) or {}
                p = cp.get("pips_in_favor")
                if p is None: return None, None
                return (1 if p > 0 else 0), p
            h1, p1 = _hit("1h"); h4, p4 = _hit("4h"); h24, p24 = _hit("24h")
            con.execute("""INSERT OR IGNORE INTO counterfactuals
                (event_id, alert_id, event_type, instrument, direction,
                 trigger_price, sl, tp, fired_at,
                 hit_1h, hit_4h, hit_24h, pips_1h, pips_4h, pips_24h)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (d.get("event_id"), d.get("alert_id"), d.get("event_type"),
                 d.get("instrument"), d.get("direction"),
                 d.get("trigger_price"), d.get("sl"), d.get("tp"),
                 d.get("fired_at"), h1, h4, h24, p1, p4, p24))
        pos = f.tell()
    write_cursor("counterfactuals", pos)
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM counterfactuals").fetchone()[0]
    return after - before


def poll_broker_positions(con):
    """Snapshot live broker positions at every poll interval."""
    try:
        r = subprocess.run(
            [sys.executable, str(REPO / "forex" / "api.py"), "positions"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0: return 0
        d = json.loads(r.stdout)
    except Exception as e:
        log(f"broker poll: {e}")
        return 0
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    for p in d.get("positions", []):
        pos = p.get("position", {})
        market = p.get("market", {})
        con.execute("""INSERT INTO broker_positions
            (ts_utc, deal_id, epic, direction, size, level,
             stop_level, profit_level, upl, created_date_utc, raw)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (now, pos.get("dealId"),
             market.get("epic") or pos.get("epic"),
             pos.get("direction"), pos.get("size"), pos.get("level"),
             pos.get("stopLevel"), pos.get("profitLevel"),
             pos.get("upl"), pos.get("createdDateUTC"),
             json.dumps(p, default=str)))
        n += 1
    con.commit()
    return n


# ── Snapshot dump (git-backup of DB) ──────────────────────────────────────

def dump_snapshot(con):
    """Write a JSON snapshot of key table tails to docs/data/forex/db_snapshot.json.
    This file IS committed to git, giving us a durable backup of critical
    state even if the .db file is lost."""
    try:
        snap = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "counts": {},
            "trades_last_50": [],
            "broker_positions_last_20": [],
            "regime_snapshots_last_10": [],
            "ticks_last_50": [],
            "counterfactuals_last_50": [],
        }
        for t in ("trades","events","ticks","regime_snapshots",
                  "broker_positions","counterfactuals"):
            snap["counts"][t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

        def rows(sql):
            return [dict(r) for r in con.execute(sql).fetchall()]
        snap["trades_last_50"] = rows("""
            SELECT id, source, epic, direction, size, entry_price, sl, tp,
                   exit_price, opened_at, closed_at, open_reason, close_reason,
                   thesis_type, realized_pnl_usd, realized_pnl_pips,
                   held_minutes, shadow, broker_deal_id
            FROM trades ORDER BY id DESC LIMIT 50""")
        snap["broker_positions_last_20"] = rows("""
            SELECT ts_utc, deal_id, epic, direction, size, level, stop_level,
                   profit_level, upl, created_date_utc
            FROM broker_positions ORDER BY id DESC LIMIT 20""")
        snap["regime_snapshots_last_10"] = rows("""
            SELECT ts_utc, regime_name, note_text,
                   binary_event_name, binary_event_active, binary_event_deadline
            FROM regime_snapshots ORDER BY id DESC LIMIT 10""")
        snap["ticks_last_50"] = rows("""
            SELECT ts_utc, trigger_type, events_in, trades_opened, trades_closed,
                   summary, cost_usd, duration_sec
            FROM ticks ORDER BY id DESC LIMIT 50""")
        snap["counterfactuals_last_50"] = rows("""
            SELECT alert_id, instrument, direction, trigger_price,
                   fired_at, hit_1h, hit_4h, hit_24h, pips_1h, pips_4h, pips_24h
            FROM counterfactuals ORDER BY id DESC LIMIT 50""")

        tmp = SNAPSHOT_OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, indent=2, default=str))
        tmp.replace(SNAPSHOT_OUT)
    except Exception as e:
        log(f"dump_snapshot: {e}")


# ── Main loop ────────────────────────────────────────────────────────────

def main():
    log(f"db_persister starting, pid={os.getpid()}")
    con = db_connect()
    last_snapshot = 0
    last_broker = 0

    while True:
        ctrl = read_control()
        if ctrl == "stop":
            log("control=stop → exiting")
            con.close()
            return 0
        if ctrl == "pause":
            time.sleep(POLL_SEC * 4)
            continue

        try:
            e = tail_events(con)
            t_scalp = tail_scalp_trades(con)
            t_swing = tail_swing_trades_from_state(con)
            c = tail_counterfactuals(con)
            r = tail_regime(con)

            now = time.time()
            bp = 0
            if now - last_broker >= BROKER_POLL_EVERY_SEC:
                bp = poll_broker_positions(con)
                last_broker = now

            changes = e + t_scalp + t_swing + c + r + bp
            if changes:
                log(f"tail: events+{e} scalp+{t_scalp} swing+{t_swing} cf+{c} regime+{r} broker+{bp}")

            if now - last_snapshot >= SNAPSHOT_EVERY_SEC:
                dump_snapshot(con)
                last_snapshot = now

        except Exception as ex:
            log(f"loop error: {ex}")
            import traceback; log(traceback.format_exc()[-500:])

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
