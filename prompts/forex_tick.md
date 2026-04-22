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

## Step 0 — Posture check (new)

Read `state/forex_state.json`. Specifically check:

1. **`binary_event`** — shape:
   ```json
   { "name": "iran_ceasefire_deadline",
     "deadline_utc": "2026-04-23T23:00:00Z",
     "active": true,
     "sources": ["https://cnn.com/...", "https://reuters.com/..."] }
   ```
   - If `active=true` AND deadline is within **30 minutes**: blackout mode — no NEW entries. Manage existing positions only.
   - If `active=true` AND deadline is 30min–24h away: **elevated caution, not blanket skip.** Take conviction 4–5 setups with tighter stops. Reduce size to 0.5× normal if you can't cleanly defend both scenarios.
   - If `active=true` AND deadline is passed: clear the field. Trade normally.
   - Any anchor to a deadline REQUIRES ≥2 independent sources agreeing on the exact time. See Step 5.

2. **`state/forex_counterfactual_summary.json`** — rolling P&L tracker for every watchlist alert fire. Use this to:
   - Validate your audit decisions in Step 7a (if an alert has hit_rate < 30% at 1h AND 4h AND 24h across ≥3 fires, it's noise — REMOVE).
   - Bias toward acting on alerts with hit_rate > 55% (historical edge, not just gut feel).

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
for e in pending[-20:]:
    print(f"  {e.get('ts_utc','')[:19]}  {e['type']:20s}  {e.get('instrument','-')}  {e.get('alert_id') or e.get('timeframe','')}")
PY
```

Note: the waker already auto-consumes `bar_close` events (pure noise) and duplicate `news_flash` events. If you see them pending it means the waker hasn't processed them yet — that's fine, process normally.

If **zero pending** → heartbeat tick. Do Step 6 (position/news/audit) only, then persist.

## Step 2 — Read state

- `state/forex_state.json` — open positions, regime, trade history, binary_event, counterfactual digest
- `state/forex_watchlist_signals.json` — current watchlist (you author this)
- `state/forex_counterfactual_summary.json` — hit rates per alert
- If a TRACKED situation explicitly references prior-day context, read yesterday's `state/daily/YYYY-MM-DD.json`. Otherwise skip.

## Step 3 — Verify broker state

```bash
python3 forex/api.py positions
python3 forex/api.py account
```

Reconcile any divergence between broker positions and `forex_state.json.open_positions`.

## Step 4 — For each unconsumed event, reason + decide (smarter, not more conservative)

This replaces the old "Default to SKIP" logic. Conservativeness is not an edge; discipline is. Use this framework:

### 4a. Assign conviction (1–5)

| Conv | Criteria |
|---|---|
| 5 | HTF structural alignment + key level hit + fresh confirmation + news tailwind + no conflicting positions |
| 4 | HTF alignment + key level + confirmation OR structural confluence score ≥ 70 aligned |
| 3 | HTF alignment + level approach, confirmation weak (wicky but suggestive) |
| 2 | Counter-trend or mixed signals |
| 1 | No clear thesis |

### 4b. Entry size by conviction

- **Conviction 5 + R:R ≥ 2:1** → full size (1.5% risk)
- **Conviction 4 + R:R ≥ 2:1** → full size (1.0% risk)
- **Conviction 4 + R:R 1.5–2:1** → half size (0.5% risk) — high conviction rescues the R:R relaxation
- **Conviction 3 + asymmetric** → half size (0.5% risk) + LIMIT order at key level (anticipation entry)
- **Conviction 3, single scenario** → skip, put alert in watchlist instead
- **Conviction ≤ 2** → skip

**Asymmetric** = the same direction wins in 2+ plausible scenarios. Example: long gold wins on escalation (obvious) AND wins on disorderly-de-escalation (capital flight from USD). Single-scenario trades need higher conviction.

### 4c. Anticipation entries (new — use more often)

Instead of waiting for a confirmation candle close (which often misses 30–60 pips of the move), place a LIMIT order at the key level when:
- Conviction ≥ 4
- Level is structurally clean (fresh OB, unmitigated FVG, swept liquidity + return)
- SL goes beyond the invalidation point (not arbitrary)
- You've pre-committed mentally to NOT cancel when price approaches

Use `api.py open` with current price ≈ limit level — this executes as market close to the level, or set a pending order.

### 4d. Event-specific triage

| Event | Question | Default action |
|---|---|---|
| `level_enter` | Conviction + R:R. At level with confluence? | 7-gate → enter per 4b sizing table |
| `level_cross` | Break-and-go or sweep-and-reverse? | 7-gate → enter on retest (anticipation OK) |
| `level_exit` | Setup invalidated? | MODIFY/REMOVE the alert, consider inverse ADD |
| `structure_bos` | Trend confirmed? | Tighten watchlist toward bias; breakout entry if conv 4+ |
| `structure_choch` | Trend flip signal | Update regime_note, flip alert directions |
| `news_flash` | Is this regime-changing? | If yes: re-score all alerts, update binary_event if applicable |
| `trail_candidate` | Move SL? | Decide per trailing_stop_rules in strategy.json |
| `position_closed` | Log trade | Update trade_history, consecutive_losses, daily_pnl |
| `daily_pnl_threshold` | Stop tier? | If stop: close all, set control file, telegram |

### 4e. Actual skip criteria (tightened — not "any doubt = skip")

Skip this specific event only if:
- Conviction ≤ 2
- R:R < 1.5:1 even with stop widening
- `risk_guard.py` would reject (daily loss, correlation, exposure)
- Binary event T-30min blackout
- You just lost on this same instrument in the last 24h and the thesis hasn't changed

**"News ambiguity" alone is NOT a skip reason.** All markets have news ambiguity; entering against structure is. A setup that requires a specific news outcome to pay off is not a 7-gate setup — it's a bet. Recognize the difference: trade the setup, not the news prediction.

## Step 5 — Deadline / thesis verification (new)

If you're about to anchor ≥2 ticks to a deadline (ceasefire expiry, FOMC time, CPI release):

1. **Require ≥2 independent sources** (different outlets, not the same wire service republished) agreeing on the exact UTC time.
2. Log sources as URLs in `forex_state.json.binary_event.sources`.
3. If only 1 source is confident: mark `binary_event.verified: false` and annotate `regime_note` with "deadline unverified — single source".
4. **Do not reuse an unverified deadline across more than 3 ticks.** Re-verify with fresh search.

Yesterday's tick log documented: a deadline assumed to be 0000 GMT Wed was actually "Wed evening ET" — a 25h miscalibration that invalidated ~30 ticks of "T-~Xh" rhetoric. The 2-source rule exists because of that.

## Step 6 — Execute approved trades

```bash
python3 forex/risk_guard.py check <EPIC> <BUY|SELL> <size> <stop_level> <profit_level>
```

Only proceed if `"approved": true`. Then:

```bash
python3 forex/api.py open <EPIC> <BUY|SELL> <size> <stop_level> <profit_level>
```

Verify via positions call. On every trade action (open/close/modify), telegram:

```bash
bash send_telegram.sh "*TRADE EXECUTED* ..."
```

## Step 7 — Position / news check (always)

- 2–3 targeted web searches (rotate: gold, crude, USDJPY+DXY, USD macro, specific active situations).
- Regime-changing headline → update `forex_state.json.regime_note` and telegram if material (position-affecting or setup-invalidating).

## Step 8 — Keep your eyes current (MANDATORY EVERY TICK)

### 8a. Watchlist audit — `state/forex_watchlist_signals.json`

Every pair in `instruments` should have a matching `structure_watch` entry. Default TFs for passive coverage: `["HOUR", "MINUTE_15"]`. Pairs with active setups may merit `HOUR_4`. BTC: `["HOUR_4", "HOUR"]`.

For each entry in `level_alerts` and each `structure_watch` pair, assign one verdict:

- **KEEP** — trigger still expresses a real edge; level is reachable on plausible catalyst; direction still right; note describes reality.
- **MODIFY** — idea survives, numbers are stale. Update zone/level/SL/TP/note/direction.
- **REMOVE** — invalidated (price gone through, narrative broke, better alternative).
- **ADD** — setup you see but aren't watching.

**Use counterfactual data.** For each alert check `state/forex_counterfactual_summary.json`:
- `fires ≥ 3 AND hit_rate < 0.30 at 1h AND 4h` → the alert is noise. REMOVE.
- `fires ≥ 3 AND hit_rate > 0.55 at 4h or 24h` → genuine edge. Prefer MODIFY over REMOVE even if stale; keep the thesis alive.
- `fires < 3` → insufficient data, decide on merits.

Not-KEEP triggers (any one):
- Price moved beyond trigger and kept going without reversal
- Direction contradicts last bar_close / structure / news
- Note references a situation no longer true
- Sharper level now exists (fresh OB, swept liquidity, new FVG)
- Position opened/closed on this instrument — management alerts may need adjustment

### Every alert must pass: "If this fires in the next hour, am I prepared to act on it right now with real money?"

If not — REMOVE regardless of how recently written.

Stale patterns to cull:
1. "Signal-only" / "no entry here" — journaling, not trading
2. "REMOTE" / "low probability" — noise
3. Duplicates of the same idea at different numbers — collapse to one
4. Note references outdated situation
5. Direction contradicts current read — flip or remove

Before ADD: is this genuinely new or a restatement? If restatement, MODIFY.
Before MODIFY/REMOVE: consider inverse ADD (breakdown zone replacing breakout).

### 8b. News-query audit — `state/news_queries.json`

Per query: KEEP / TUNE (add/remove keywords) / REMOVE (theme played out) / ADD (active theme uncovered).

### 8c. Write the updates + audit table

Rewrite files atomically if any item is not KEEP. Output compact audit:

```
audit watchlist:  audusd_breakdown_trigger=KEEP  usdjpy_intervention_fade_zone=KEEP(hit1h=100%) oil_crude_retest_90_buy=MODIFY(lvl 90.5→91)
audit queries:    iran_ceasefire=KEEP  hormuz_strait=TUNE(+"tanker")  fed_fomc=KEEP
binary_event:     iran_ceasefire_deadline active=true T-25h sources=2 verified=true
```

If every item KEEP: `audit: all items KEEP (N watchlist, M queries); binary_event: <state>`.

## Step 9 — Mark events consumed

```bash
python3 - <<'PY'
processed_ids = [...]  # every event you read, acted or not
with open("state/forex_events_consumed.txt", "a") as f:
    for eid in processed_ids:
        f.write(eid + "\n")
PY
```

Every event you read in Step 1 must appear here.

## Step 10 — Persist + commit

Update `state/forex_state.json` with:
- new positions, trade outcomes, alert changes
- current `binary_event` state (incl. sources URLs)
- latest `regime_note`
- `last_tick_utc`, `last_tick_summary`

Append to `state/daily/$(date +%Y-%m-%d).json`.

Then publish the forex data to the static dashboard:

```bash
cd /Users/rajneeshmishra/Downloads/stock-pulse
bash docs/publish_forex.sh
git add state/ docs/ && git commit -m "tick: $(date +%H:%M) forex auto — <one-line>" && git push origin main
```

## Step 11 — Done

Print single-line summary:
`tick done: N events, M opened, K closed, S skipped; binary=<name|none>; conv=<high/med/none>`

Exit. Do not loop.

---

## INVARIANTS

1. Every order has SL + TP.
2. Every trade passes `risk_guard` first.
3. Conviction + sizing framework (Step 4b) replaces "all 7 gates green or skip" — gate failures at conv 4–5 trigger half-size, not automatic skip. Gate 5 (risk_guard) is ALWAYS mandatory.
4. Mark every event consumed.
5. Run Step 8 audit every tick.
6. Commit + push at end.
7. No background tools. No asking for confirmation. No scheduling wakeups.
8. Anchored deadlines require 2 sources (Step 5).
9. Binary event T-30min = no new entries. Binary event T-30min to T-24h = elevated caution, NOT blanket skip.
10. "News ambiguity" alone is not a skip reason.
