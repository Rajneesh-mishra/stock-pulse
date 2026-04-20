# Forex tick — autonomous invocation

You are being invoked non-interactively. Execute **ONE tick** and exit. Do not loop. Do not schedule a wakeup. Do not ask for confirmation.

You are already inside `/Users/rajneeshmishra/Downloads/stock-pulse`.

---

## Context

- **Working capital:** $1,000 (broker balance = capital_base)
- **Max risk/trade:** 2% = $20
- **Max total open:** 6% = $60
- **Max positions:** 4, max correlated 2
- **Daily loss stop:** 5% = $50
- **Every trade MUST have SL + TP.** `risk_guard.py` rejects naked orders.
- **All permissions pre-granted** — bash, git, file writes, Capital.com trades, Telegram. Just execute.

## Step 1 — Identify unconsumed events

```bash
python3 - <<'PY'
import json, os
seen = set()
if os.path.exists("state/forex_events_consumed.txt"):
    seen = set(l.strip() for l in open("state/forex_events_consumed.txt") if l.strip())
pending = []
if os.path.exists("state/forex_events.jsonl"):
    for line in open("state/forex_events.jsonl"):
        try:
            d = json.loads(line)
            if d.get("event_id") and d["event_id"] not in seen and not d.get("consumed_by_claude"):
                pending.append(d)
        except Exception:
            pass
print(f"{len(pending)} unconsumed events")
for e in pending[-20:]:  # Most recent 20 only
    print(f"  {e.get('ts_utc','')[:19]}  {e['type']:20s}  {e.get('instrument','-')}  {e.get('alert_id') or e.get('timeframe','')}")
PY
```

If **zero pending** → this is a heartbeat-only tick. Jump to Step 6 (position/news review only).

## Step 2 — Read state

- `state/forex_state.json` — open positions, regime, trade history
- `state/forex_watchlist_signals.json` — current watchlist (what you told the daemon to watch)
- If a TRACKED situation (e.g., Iran ceasefire) explicitly references prior-day context, read yesterday's `state/daily/YYYY-MM-DD.json`. Otherwise skip — don't waste context.

## Step 3 — Verify broker state

```bash
python3 forex/api.py positions   # confirm open positions match state
python3 forex/api.py account     # current balance / available / open P&L
```

If divergence between broker and `forex_state.json.open_positions`, reconcile.

## Step 4 — For each unconsumed event, reason + decide

Per event type, ask yourself:

| Event | Question | If yes |
|---|---|---|
| `level_enter` | Does the thesis still hold? Confluence aligned? Confirmation candle? | Run 7-gate → enter if all green |
| `level_exit` | Setup invalidated? Update watchlist zones. | Rewrite `forex_watchlist_signals.json` |
| `level_cross` | Break-through or retest? Is this continuation? | 7-gate → maybe enter on retest |
| `bar_close` | Did structure change on any TF? | Re-score with `confluence.py`, update thesis if shifted |
| `structure_bos` | Trend confirmed in bias direction? | Tighten watchlist; maybe breakout entry |
| `structure_choch` | Potential trend flip — reassess bias fully | Update `forex_state.json.regime_note` |
| `position_opened` | New position — log thesis, set management alerts | Add trail trigger levels to watchlist |
| `position_closed` | Log trade result, update consecutive_losses + daily_pnl | Consider if thesis held or failed |
| `trail_candidate` | Move SL to breakeven / last swing? | Decide + `api.py modify` if yes |
| `daily_pnl_threshold` | Warn or stop tier hit | If `stop`: close all, set control file, telegram |

**Default to SKIP.** A bad entry is worse than a missed one. If any of:
- 7-gate fails
- Confluence score < 60 AND aligned=False
- News ambiguity (e.g., Iran deal status unclear)
- You just lost on this instrument (consecutive_losses > 0 same theme)

→ skip. Log why in state. Move on.

## Step 5 — Execute approved trades

For each trade you're committing to:

```bash
python3 forex/risk_guard.py check <EPIC> <BUY|SELL> <size> <stop_level> <profit_level>
```

Read the JSON. **Only proceed if `"approved": true`.** If rejected, log the rejection reasons and skip.

```bash
python3 forex/api.py open <EPIC> <BUY|SELL> <size> <stop_level> <profit_level>
```

Verify via positions call. On every trade action (open/close/modify):

```bash
bash send_telegram.sh "*TRADE EXECUTED* ..."
```

## Step 6 — Position / news check (always, even if no events)

- Scan 2-3 web searches relevant to open positions / watchlist (gold, crude, USD/JPY, DXY, Iran, BoJ — rotate)
- If a regime-changing headline is found, update `forex_state.json.regime_note` and telegram if material

## Step 7 — Mark events consumed

For **every** event you processed in Step 4 (acted on OR skipped), append its `event_id` to `state/forex_events_consumed.txt`:

```bash
python3 - <<'PY'
import json
processed_ids = [<PASTE EVENT IDS YOU HANDLED>]  # fill in
with open("state/forex_events_consumed.txt", "a") as f:
    for eid in processed_ids:
        f.write(eid + "\n")
PY
```

**If you do not mark an event consumed, it will wake you up again.** Every event you read in Step 1 must be in Step 7's list.

## Step 8 — Persist

```bash
cd /Users/rajneeshmishra/Downloads/stock-pulse
git add state/ docs/ && git commit -m "tick: $(date +%H:%M) forex auto — <one-line summary>" && git push origin main
```

Update `state/forex_state.json` with any regime changes, new positions, trade outcomes, alerts sent. Also update daily log `state/daily/$(date +%Y-%m-%d).json`.

## Step 9 — Done

Print a single-line summary of the tick outcome to stdout:
`tick done: processed N events, opened M, closed K, skipped S`

Exit. Do not loop.

---

## INVARIANTS

1. Every order has SL + TP.
2. Every trade passes `risk_guard` first.
3. 7 gates all green, or skip.
4. Mark every event consumed.
5. Commit + push at end (even if no action — state changes matter).
6. No background tools. No asking for confirmation. No scheduling wakeups.
