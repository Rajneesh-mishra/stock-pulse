# Stock Pulse — Forex Command

Project-level instructions. Auto-loaded by Claude Code at every session
start (including headless `claude -p` ticks). Keep this focused and
forex-specific — anything here runs on every tick, so bloat costs money.

## What this system is

Claude-orchestrated forex/CFD trading on Capital.com demo. Working
capital: **$1,000**. Daemons sense the tape and emit events; Claude ticks
consume events and decide trades; risk_guard validates every order;
scalp engine handles mechanical scalps on FX majors.

**Canonical tick prompt:** `prompts/forex_tick.md` — read that, not this.
This file just has the rails the tick runs on.

## Repo layout (what matters)

```
state/
  forex_state.json                 positions, trade_history, binary_event, regime_note
  forex_watchlist_signals.json     YOU author this; daemon reads it
  forex_events.jsonl               append-only event stream
  forex_events_consumed.txt        IDs you've processed
  forex_counterfactual_summary.json alert hit rates (read-only, from daemon)
  forex_scalp_config.json          YOU author this; scalp engine reads it
  forex_scalp_status.json          scalp engine writes this (read-only for you)
  forex_scalp_ledger.jsonl         scalp engine writes this (read-only)
  daily/YYYY-MM-DD.json            tick log — append today's entry
daemon/
  forex_watcher.py                 emits level/structure/liquidity_sweep events
  forex_news_watcher.py            emits news_flash + alert_audit_request events
  forex_position_sync.py           broker reconciliation
  forex_counterfactual_tracker.py  fills hit-rate data (don't touch)
  forex_scalp_engine.py            mechanical scalp entries (shadow-mode by default)
  claude_event_waker.py            wakes YOU on actionable events
forex/
  api.py                           Capital.com CLI wrapper — use for trades + prices
  risk_guard.py                    HARD safety — every order must pass `check` first
  confluence.py                    multi-TF readiness scorer (strong/moderate/weak/none)
  technicals.py                    SMC + EMA + ATR helpers
prompts/
  forex_tick.md                    THE tick protocol — read this every tick
web/
  dashboard_server.py              http://localhost:8787 — live UI + /api/chat
docs/                              Vite React build — served by dashboard_server + GH Pages
  publish_forex.sh                 copies state/forex_*.json → docs/data/forex/
dashboard/                         React source (Vite + TS + Tailwind)
```

## Operational invariants

1. **Telegram**: `bash send_telegram.sh "<msg>"` — reads `.env` automatically. Send on: trade open/close/modify, regime-change headline, any tick-timeout or daemon-down alert from the waker.
2. **Every trade has SL + TP.** `risk_guard check` rejects naked orders.
3. **Risk ceilings** — 2% per swing trade, 0.5% per scalp, 6% total open, 4 positions max, 2 correlated, 5% daily loss stop. Enforced by risk_guard.
4. **No run_in_background agents.** Silently lose output. Foreground only.
5. **No background tools in ticks.** `claude -p` is one-shot; can't schedule wakeups.
6. **State writes happen LAST** in a tick, after all analysis.
7. **Publish data before commit.** Every forex tick Step 10 runs `bash docs/publish_forex.sh` before `git commit` so the dashboard reflects current state.
8. **Git push has retry** (`for i in 1 2 3; do ... && break; sleep 10; done`). Never silent-fail.
9. **Scalp engine is auto** — don't place scalp-style entries yourself. You tune `forex_scalp_config.json` (enable/disable per pair, bias, sessions) and review stats weekly. Live scalp orders go through risk_guard same as swing.

## Confluence readiness tiers (from forex/confluence.py)

- **strong** — |composite| ≥ 60 AND all TFs agree → full size (1.5%)
- **moderate** — |composite| ≥ 40 AND ≥ (n−1) TFs agree → half size (0.5%), anticipation LIMIT OK
- **weak** — |composite| ≥ 25 AND ≥ 2 TFs agree → watchlist only
- **none** — below the above → no setup

For counter-trend fades (alert note contains "intervention"/"red line"/"BoJ"/"fade"/"overextended"/"capitulation"/"exhaustion"/"parabolic"), confluence OPPOSING the alert is corroborating, not blocking — see prompt Step 4b.

## Event types (from daemons)

- `level_enter` / `level_cross` / `level_exit` — watchlist zone hits
- `liquidity_sweep` — price wicked beyond recent 20-bar extreme and closed back inside (rejection). Fresh bounce point the tape just created.
- `structure_bos` / `structure_choch` — SMC transitions on watched TFs
- `alert_audit_request` — news matched an active alert's keywords; re-score THAT alert
- `news_flash` — breaking headline matching a watched query
- `bar_close` — auto-consumed by waker, not actionable
- `trail_candidate` / `position_opened` / `position_closed` / `sl_hit` / `tp_hit`
- `daily_pnl_threshold` — 5% daily loss stop reached

## Instruments

9 pairs: EURUSD, GBPUSD, AUDUSD, USDCAD, USDCHF, USDJPY, GOLD, OIL_CRUDE, BTCUSD.

Scalp engine is active on EURUSD / GBPUSD / AUDUSD only (USD quote, shadow P/L = price_diff × size gives direct USD). USDJPY / USDCHF / USDCAD disabled pending per-pair pip-value conversion. GOLD / OIL / BTC disabled — spreads too wide for scalp (5p / 4p / 50p).

## Python environment

- Python 3.14 at `/opt/homebrew/bin/python3`
- Daemons run under launchd (com.stockpulse.*)
- Always use `python3 forex/api.py …` from repo root — not bare scripts
- `.env` file at repo root holds CAPITAL_API_KEY / CAPITAL_EMAIL / CAPITAL_PASSWORD / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS. **Never commit `.env`.**

## Dashboard

- **Local**: http://localhost:8787 (served by `web/dashboard_server.py`)
- **Public**: https://rajneesh-mishra.github.io/stock-pulse/
- Same HTML. GitHub Pages reads static JSON from `docs/data/forex/`; the local server also serves the same JSON plus live API endpoints.

## What NOT to do

- Don't touch `state/.capital_session.json` (shared session cache for api.py / risk_guard / technicals)
- Don't remove `state/.counterfactual_cursor` unless rebuilding the ledger
- Don't add a sentinel/deep-dive agent pattern — that was the old Indian-equities flow, no longer applies
- Don't read yesterday's daily log unless a tracked situation explicitly references prior-day context
- Don't bloat this file — it's auto-loaded on every tick. Move anything non-essential to `prompts/` or the archive.
