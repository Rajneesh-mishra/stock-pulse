#!/usr/bin/env python3
"""
One-time migration: read all existing JSONL / JSON state into state/pulse.db.
Idempotent: re-runnable; uses UNIQUE constraints to skip already-imported rows.

Run manually:
    python3 db/migrate.py

Safe to run any time as a "rebuild DB from files" recovery step.
"""

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB_PATH = REPO / "state" / "pulse.db"
SCHEMA = REPO / "db" / "schema.sql"

STATE_FILE        = REPO / "state" / "forex_state.json"
EVENTS_FILE       = REPO / "state" / "forex_events.jsonl"
CONSUMED_FILE     = REPO / "state" / "forex_events_consumed.txt"
CF_LEDGER         = REPO / "state" / "forex_alert_counterfactuals.jsonl"
SCALP_LEDGER      = REPO / "state" / "forex_scalp_ledger.jsonl"
DAILY_DIR         = REPO / "state" / "daily"


def db_connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def init_schema(con):
    sql = SCHEMA.read_text()
    con.executescript(sql)
    con.commit()


def migrate_trades(con):
    """Two sources: state.trade_history (historical real trades, n=2) and
    scalp_ledger (all shadow + live scalps)."""
    inserted = 0

    # 1) Historical real trades from state_file.trade_history
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text())
            for t in (s.get("trade_history") or []):
                row = {
                    "source": "swing",
                    "epic": t.get("instrument"),
                    "direction": t.get("direction"),
                    "size": t.get("size") or 1.0,
                    "entry_price": t.get("entry_price"),
                    "sl": t.get("sl"),
                    "tp": t.get("tp"),
                    "exit_price": t.get("exit_price"),
                    "opened_at": t.get("opened_at"),
                    "closed_at": t.get("closed_at"),
                    "open_reason": t.get("open_reason"),
                    "close_reason": t.get("result") or t.get("exit_reason"),
                    "thesis_type": None,
                    "realized_pnl_usd": t.get("pnl"),
                    "realized_pnl_pips": None,
                    "held_minutes": None,
                    "shadow": 0,
                    "broker_deal_id": None,
                    "raw": json.dumps(t, default=str),
                }
                try:
                    con.execute("""INSERT OR IGNORE INTO trades
                        (source, epic, direction, size, entry_price, sl, tp,
                         exit_price, opened_at, closed_at, open_reason,
                         close_reason, thesis_type, realized_pnl_usd,
                         realized_pnl_pips, held_minutes, shadow,
                         broker_deal_id, raw)
                        VALUES
                        (:source, :epic, :direction, :size, :entry_price,
                         :sl, :tp, :exit_price, :opened_at, :closed_at,
                         :open_reason, :close_reason, :thesis_type,
                         :realized_pnl_usd, :realized_pnl_pips,
                         :held_minutes, :shadow, :broker_deal_id, :raw)""",
                        row)
                    inserted += con.total_changes > 0 and 1 or 0
                except Exception as e:
                    print(f"  swing trade skip: {e}")
        except Exception as e:
            print(f"state.trade_history read failed: {e}")

    # 2) Scalp ledger — pair opens/closes into trades
    if SCALP_LEDGER.exists():
        rows = []
        for line in SCALP_LEDGER.open():
            try: rows.append(json.loads(line))
            except Exception: pass
        opens = {}
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
                row = {
                    "source": "scalp",
                    "epic": epic,
                    "direction": o.get("direction"),
                    "size": o.get("size") or 1.0,
                    "entry_price": o.get("entry"),
                    "sl": o.get("sl"),
                    "tp": o.get("tp"),
                    "exit_price": r.get("exit"),
                    "opened_at": o["ts_utc"],
                    "closed_at": r["ts_utc"],
                    "open_reason": o.get("setup"),
                    "close_reason": r.get("how"),
                    "thesis_type": "scalp_entry",
                    "realized_pnl_usd": r.get("pnl_usd"),
                    "realized_pnl_pips": None,
                    "held_minutes": held,
                    "shadow": 1 if o.get("shadow") else 0,
                    "broker_deal_id": (o.get("broker_ref") or {}).get("dealId") if isinstance(o.get("broker_ref"), dict) else None,
                    "raw": json.dumps({"open": o, "close": r}, default=str),
                }
                try:
                    before = con.total_changes
                    con.execute("""INSERT OR IGNORE INTO trades
                        (source, epic, direction, size, entry_price, sl, tp,
                         exit_price, opened_at, closed_at, open_reason,
                         close_reason, thesis_type, realized_pnl_usd,
                         realized_pnl_pips, held_minutes, shadow,
                         broker_deal_id, raw)
                        VALUES
                        (:source, :epic, :direction, :size, :entry_price,
                         :sl, :tp, :exit_price, :opened_at, :closed_at,
                         :open_reason, :close_reason, :thesis_type,
                         :realized_pnl_usd, :realized_pnl_pips,
                         :held_minutes, :shadow, :broker_deal_id, :raw)""",
                        row)
                    if con.total_changes > before: inserted += 1
                except Exception as e:
                    print(f"  scalp trade skip: {e}")
    con.commit()
    print(f"trades: {inserted} inserted")


def migrate_events(con):
    if not EVENTS_FILE.exists():
        return
    # Build consumed set
    consumed = set()
    if CONSUMED_FILE.exists():
        consumed = {l.strip() for l in CONSUMED_FILE.read_text().splitlines() if l.strip()}

    inserted = 0
    before_total = con.total_changes
    for line in EVENTS_FILE.open():
        try: d = json.loads(line)
        except Exception: continue
        eid = d.get("event_id")
        if not eid: continue
        payload = d.get("payload") or {}
        con.execute("""INSERT OR IGNORE INTO events
            (event_id, type, ts_utc, instrument, alert_id, direction,
             timeframe, consumed, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, d.get("type"), d.get("ts_utc"), d.get("instrument"),
             d.get("alert_id"), d.get("direction") or payload.get("direction"),
             d.get("timeframe") or payload.get("timeframe"),
             1 if eid in consumed else 0,
             json.dumps(d, default=str)))
    con.commit()
    inserted = con.total_changes - before_total
    print(f"events: {inserted} inserted")


def migrate_ticks(con):
    """Tick history lives in state.tick_history (last ~20) plus per-day daily logs."""
    inserted = 0
    before = con.total_changes
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text())
            for t in (s.get("tick_history") or []):
                con.execute("""INSERT INTO ticks
                    (ts_utc, trigger_type, events_in, trades_opened,
                     trades_closed, summary, cost_usd, duration_sec, raw)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (t.get("ts_utc"), t.get("trigger"), t.get("events_in") or t.get("events_processed"),
                     t.get("opened"), t.get("closed"),
                     t.get("note") or t.get("summary"),
                     None, None, json.dumps(t, default=str)))
        except Exception as e:
            print(f"state.tick_history: {e}")

    # Daily logs (one file per day)
    if DAILY_DIR.exists():
        for f in sorted(DAILY_DIR.glob("*.json")):
            try:
                raw = json.loads(f.read_text())
                ticks = raw.get("ticks", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                for t in ticks:
                    ts = t.get("t") or t.get("ts_utc")
                    if not ts: continue
                    con.execute("""INSERT INTO ticks
                        (ts_utc, trigger_type, events_in, trades_opened,
                         trades_closed, summary, cost_usd, duration_sec, raw)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (ts, t.get("trigger"),
                         t.get("events_processed"), t.get("opened") or 0, t.get("closed") or 0,
                         t.get("summary") or t.get("note"),
                         None, None, json.dumps(t, default=str)))
            except Exception as e:
                print(f"  {f.name}: {e}")
    con.commit()
    inserted = con.total_changes - before
    print(f"ticks: {inserted} inserted")


def migrate_counterfactuals(con):
    if not CF_LEDGER.exists():
        return
    before = con.total_changes
    for line in CF_LEDGER.open():
        try: d = json.loads(line)
        except Exception: continue
        if d.get("kind") != "alert_fired": continue
        cps = d.get("checkpoints") or {}
        def _hit(h):
            cp = cps.get(h) or {}
            p = cp.get("pips_in_favor")
            if p is None: return None, None
            return (1 if p > 0 else 0), p
        h1, p1 = _hit("1h")
        h4, p4 = _hit("4h")
        h24, p24 = _hit("24h")
        con.execute("""INSERT OR IGNORE INTO counterfactuals
            (event_id, alert_id, event_type, instrument, direction,
             trigger_price, sl, tp, fired_at,
             hit_1h, hit_4h, hit_24h, pips_1h, pips_4h, pips_24h)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d.get("event_id"), d.get("alert_id"), d.get("event_type"),
             d.get("instrument"), d.get("direction"),
             d.get("trigger_price"), d.get("sl"), d.get("tp"),
             d.get("fired_at"), h1, h4, h24, p1, p4, p24))
    con.commit()
    inserted = con.total_changes - before
    print(f"counterfactuals: {inserted} inserted")


def migrate_regime(con):
    if not STATE_FILE.exists(): return
    try:
        s = json.loads(STATE_FILE.read_text())
    except Exception:
        return
    note = s.get("regime_note")
    if not note: return
    be = s.get("binary_event") or {}
    con.execute("""INSERT INTO regime_snapshots
        (ts_utc, regime_name, note_text, binary_event_name,
         binary_event_active, binary_event_deadline)
        VALUES (?,?,?,?,?,?)""",
        (s.get("tick_ts_utc") or datetime.now(timezone.utc).isoformat(),
         s.get("regime"), note, be.get("name"),
         1 if be.get("active") else 0, be.get("deadline_utc")))
    con.commit()
    print("regime_snapshots: +1")


def main():
    print(f"migrating into {DB_PATH}")
    con = db_connect()
    init_schema(con)
    migrate_trades(con)
    migrate_events(con)
    migrate_ticks(con)
    migrate_counterfactuals(con)
    migrate_regime(con)
    cur = con.execute("SELECT name, COUNT(*) as n FROM sqlite_master "
                      "LEFT JOIN (SELECT 'trades' name UNION SELECT 'events' UNION "
                      "SELECT 'ticks' UNION SELECT 'counterfactuals' UNION "
                      "SELECT 'regime_snapshots' UNION SELECT 'broker_positions') "
                      "USING (name) WHERE type='table' GROUP BY name")
    print("\nTable row counts:")
    for t in ("trades","events","ticks","counterfactuals","regime_snapshots","broker_positions"):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:20s} {n}")
    con.close()


if __name__ == "__main__":
    main()
