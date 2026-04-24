-- Stock Pulse durable state. SQLite, single file at state/pulse.db.
-- Idempotent — safe to re-run on an existing DB.
--
-- Durability model: every daemon dual-writes to its own JSONL (the
-- primary append-only log) and to this DB (for queryability). If the DB
-- ever corrupts we can rebuild it from the JSONL files by running
-- db/migrate.py again.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Trades — every order placed (live or shadow) ──────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,           -- 'swing' / 'scalp' / 'manual'
    epic          TEXT NOT NULL,
    direction     TEXT NOT NULL,           -- 'BUY' / 'SELL'
    size          REAL NOT NULL,           -- broker size (contract-dependent)
    entry_price   REAL,
    sl            REAL,
    tp            REAL,
    exit_price    REAL,
    opened_at     TEXT NOT NULL,           -- ISO UTC
    closed_at     TEXT,                    -- ISO UTC, null = still open
    open_reason   TEXT,                    -- alert_id / setup / manual note
    close_reason  TEXT,                    -- 'tp_hit' / 'sl_hit' / 'time_exit' / 'manual'
    thesis_type   TEXT,                    -- narrative / trend / sweep_retest / counter_trend_fade / scalp_entry
    realized_pnl_usd REAL,
    realized_pnl_pips REAL,
    held_minutes  REAL,
    shadow        INTEGER NOT NULL DEFAULT 0,   -- 1 = paper trade
    broker_deal_id TEXT,                    -- unique on broker side if live
    raw           TEXT                     -- full JSON of source record
    -- Identity: source+epic+opened_at+direction. broker_deal_id is NOT
    -- part of the key because it's NULL for shadow trades and SQLite's
    -- UNIQUE treats NULL != NULL, which caused duplicate rows on re-run.
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_trades_identity
    ON trades (source, epic, opened_at, direction);
CREATE INDEX IF NOT EXISTS idx_trades_opened_at ON trades (opened_at);
CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades (closed_at);
CREATE INDEX IF NOT EXISTS idx_trades_epic      ON trades (epic);
CREATE INDEX IF NOT EXISTS idx_trades_source    ON trades (source);
CREATE INDEX IF NOT EXISTS idx_trades_shadow    ON trades (shadow);

-- ── Events — every daemon-emitted event ───────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT UNIQUE,
    type        TEXT NOT NULL,             -- level_enter / liquidity_sweep / news_flash / …
    ts_utc      TEXT NOT NULL,
    instrument  TEXT,
    alert_id    TEXT,
    direction   TEXT,
    timeframe   TEXT,
    consumed    INTEGER DEFAULT 0,
    payload     TEXT                        -- full JSON
);

CREATE INDEX IF NOT EXISTS idx_events_ts   ON events (ts_utc);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (type);
CREATE INDEX IF NOT EXISTS idx_events_inst ON events (instrument);

-- ── Ticks — every Claude tick invocation ─────────────────────────────────
CREATE TABLE IF NOT EXISTS ticks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT NOT NULL,
    trigger_type    TEXT,                    -- 'event' / 'heartbeat' / 'manual'
    events_in       INTEGER,
    trades_opened   INTEGER,
    trades_closed   INTEGER,
    summary         TEXT,
    cost_usd        REAL,
    duration_sec    REAL,
    git_sha         TEXT,
    raw             TEXT                    -- full tick_history row
);

CREATE INDEX IF NOT EXISTS idx_ticks_ts   ON ticks (ts_utc);

-- ── Regime snapshots — what the orchestrator was thinking ─────────────────
CREATE TABLE IF NOT EXISTS regime_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT NOT NULL,
    regime_name     TEXT,
    note_text       TEXT,
    binary_event_name       TEXT,
    binary_event_active     INTEGER,
    binary_event_deadline   TEXT
);

CREATE INDEX IF NOT EXISTS idx_regime_ts ON regime_snapshots (ts_utc);

-- ── Broker positions snapshot — point-in-time live positions ──────────────
CREATE TABLE IF NOT EXISTS broker_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT NOT NULL,
    deal_id         TEXT NOT NULL,
    epic            TEXT,
    direction       TEXT,
    size            REAL,
    level           REAL,
    stop_level      REAL,
    profit_level    REAL,
    upl             REAL,
    created_date_utc TEXT,
    raw             TEXT
);

CREATE INDEX IF NOT EXISTS idx_bp_ts ON broker_positions (ts_utc);
CREATE INDEX IF NOT EXISTS idx_bp_deal ON broker_positions (deal_id);

-- ── Counterfactual fires (denormalised from alert_counterfactuals.jsonl) ──
CREATE TABLE IF NOT EXISTS counterfactuals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT UNIQUE,
    alert_id        TEXT NOT NULL,
    event_type      TEXT,
    instrument      TEXT,
    direction       TEXT,
    trigger_price   REAL,
    sl              REAL,
    tp              REAL,
    fired_at        TEXT NOT NULL,
    hit_1h          INTEGER,                 -- 1=favorable, 0=adverse, null=pending
    hit_4h          INTEGER,
    hit_24h         INTEGER,
    pips_1h         REAL,
    pips_4h         REAL,
    pips_24h        REAL
);

CREATE INDEX IF NOT EXISTS idx_cf_alert ON counterfactuals (alert_id);
CREATE INDEX IF NOT EXISTS idx_cf_fired ON counterfactuals (fired_at);

-- ── Schema version for future migrations ──────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_version (
    version   INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_version (version, applied_at)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ','now'));
