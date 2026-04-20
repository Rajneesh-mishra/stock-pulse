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

## Step 7 — Keep your eyes current (MANDATORY EVERY TICK)

Before persisting, audit **every** item the daemons are watching. Do this as a mechanical walk — one verdict per item. You MUST produce output for this step; skipping it is not allowed.

### 7a. Watchlist audit — `state/forex_watchlist_signals.json`

For each entry in `level_alerts` and each pair in `structure_watch`, assign one verdict:

- **KEEP** — the trigger still expresses a real edge given what you know right now (price action, news, positions, broader context). The level is reachable on a plausible catalyst path. The direction is still the right call. The note still describes reality.
- **MODIFY** — the idea survives but the numbers are stale. Update zone/level/SL/TP/note/direction. Produce the new JSON fields.
- **REMOVE** — the setup is invalidated (price has moved through it and kept going, the underlying narrative broke, a better opportunity replaces it). Remove the entry.
- **ADD** — a setup you now see but aren't watching. Add the new entry.

Treat any ONE of these as grounds to not-KEEP:
- Current price has moved so far from the trigger that a hit requires a new catalyst that doesn't exist
- The direction contradicts what the last bar_close / structure events / news events tell you
- The `note` references a situation (deal, catalyst, zone identity) that isn't true anymore
- A structurally sharper level has formed (order block, swept liquidity, new FVG) that wasn't in the alert when it was written
- You opened or closed a position on this instrument — management alerts (break-even trigger, TP1 partial, trailing levels) may need to be added or retired

### One-in, one-out discipline (HARD RULE)

The watchlist is a scarce resource, not a logbook. Cap: **≤ 8 total `level_alerts`**, **≤ 2 entry alerts per instrument per direction** (a second alert on the same instrument+direction must serve a genuinely different purpose — e.g. management/exit, not just a second entry level).

Before every `ADD`:
1. Identify which existing alert this replaces or retires. If none — justify why the watchlist genuinely needs to grow.
2. If your audit produces a net alert count higher than before, the tick summary must include a one-sentence reason for the growth.
3. "Signal-only, no entry here" alerts count against the cap. If something is signal-only and far from price, `REMOVE` it — the news daemon + web search already catches far-field moves.
4. Entries noted as "REMOTE" or "low probability" get `REMOVE`, not `KEEP`. Watchlist alerts exist to fire actionably; if they're not expected to fire, they're noise.
5. Two alerts on the same instrument+direction at different levels (e.g. "pullback zone" + "breakout trigger") are allowed ONLY if one is for pullback-entry and the other is for breakout-continuation — pick the cleaner one otherwise.

Before every `MODIFY` or `REMOVE`, also re-check whether a replacement `ADD` is needed in the opposite direction (a breakdown zone replacing a breakout zone, for example).

### 7b. News-query audit — `state/news_queries.json`

For each entry in `queries`:

- **KEEP** — query + keywords still cover a theme you need to be awake for
- **TUNE** — keywords missing terms that showed up in real headlines this tick (or earlier); add/remove keywords
- **REMOVE** — the theme has played out or stopped mattering; drop it
- **ADD** — a theme is active that no current query covers (if you did news searches during this tick and found relevant stories, at least one of those themes probably deserves a query)

### 7c. Write the updates

If any item in 7a or 7b is not KEEP, rewrite the file(s) atomically. Preserve all other entries. Claim the changes in your tick summary with one short sentence per change — e.g. `"removed AUDUSD pullback zone: price broke through without catalyst"`, `"added query iran_strikes: headlines showed up that iran_ceasefire didn't catch"`.

**Output a compact audit table to stdout** (not the full JSON — just verdict per id):

```
audit watchlist:  audusd_pullback_buy_zone=KEEP  usdjpy_intervention_zone=KEEP  oil_crude_sub_85=MODIFY(level 85→83)  eurusd_breakout_level=REMOVE
audit queries:    iran_ceasefire=KEEP  hormuz_strait=TUNE(+"cargo","ship")  fed_fomc=KEEP  rba_hike=KEEP
```

If every item is KEEP, still print: `audit: all items KEEP (N watchlist, M queries)`.

## Step 8 — Mark events consumed

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

**If you do not mark an event consumed, it will wake you up again.** Every event you read in Step 1 must be in this list.

## Step 9 — Persist

```bash
cd /Users/rajneeshmishra/Downloads/stock-pulse
git add state/ docs/ && git commit -m "tick: $(date +%H:%M) forex auto — <one-line summary>" && git push origin main
```

Update `state/forex_state.json` with any new positions, trade outcomes, alerts sent, and the current read of conditions. Also update daily log `state/daily/$(date +%Y-%m-%d).json`.

## Step 10 — Done

Print a single-line summary of the tick outcome to stdout:
`tick done: processed N events, opened M, closed K, skipped S`

Exit. Do not loop.

---

## INVARIANTS

1. Every order has SL + TP.
2. Every trade passes `risk_guard` first.
3. 7 gates all green, or skip.
4. Mark every event consumed.
5. Run the Step 7 audit every tick. Not optional. Every item gets a verdict.
6. Commit + push at end (even if no action — state changes matter).
7. No background tools. No asking for confirmation. No scheduling wakeups.
