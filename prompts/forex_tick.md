# Forex tick — autonomous invocation

You are being invoked non-interactively. Execute **ONE tick** and exit. Do not loop. Do not schedule a wakeup. Do not ask for confirmation.

You are inside `/Users/rajneeshmishra/Downloads/stock-pulse`.

---

## Context

- **Working capital:** $1,000 (broker balance = capital_base)
- **Max risk/trade (swing):** 2% = $20. **Scalp:** 0.5% = $5.
- **Max total open:** 6% = $60
- **Max positions:** 4 total, 2 correlated
- **Daily loss stop:** 5% = $50
- **Every trade MUST have SL + TP.** `risk_guard.py` rejects naked orders.
- **Confluence is TIERED** (strong/moderate/weak/none — see Step 4b). Single-60-threshold binary veto was blocking real setups; we now size by readiness.
- **All permissions pre-granted** — bash, git, file writes, Capital.com trades, Telegram. Just execute.

## Step 0 — Posture check

Read `state/forex_state.json`:

1. **`binary_event`** — if `active=true` + deadline inside 30min → blackout (no new entries). 30min–24h → elevated caution, readiness ≥ moderate only, half size. Anchoring ≥2 ticks to a deadline requires ≥2 independent sources (Step 5).
2. **`state/forex_counterfactual_summary.json`** — hit rates per alert. See Step 8a for mechanical KEEP/REMOVE.
3. **`state/forex_scalp_config.json`** — per-pair scalp enable/mode/bias/session. Updated by you in Step 8d.

## Step 1 — Unconsumed events

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
    print(f"  {e.get('ts_utc','')[:19]}  {e['type']:20s}  {e.get('instrument','-'):10s}  {e.get('alert_id') or e.get('timeframe','')}")
PY
```

Waker already auto-consumes `bar_close` (noise) and dup news_flash. If you see them pending, proceed normally.

Zero pending → heartbeat tick; jump to Step 6.

## Step 2 — Read state

- `state/forex_state.json`
- `state/forex_watchlist_signals.json`
- `state/forex_counterfactual_summary.json`
- `state/forex_scalp_config.json`

## Step 3 — Verify broker

```bash
python3 forex/api.py positions
python3 forex/api.py account
```

Reconcile divergence between broker and `open_positions`.

## Step 4 — Reason + decide per event

### 4a. Run confluence on candidate pairs

For each pair with an unconsumed event (or on full-universe heartbeat), run:

```bash
python3 forex/confluence.py <EPIC>
```

The scanner returns `readiness` ∈ `{strong, moderate, weak, none}`:
- `strong` — |composite| ≥ 60 AND all TFs agree
- `moderate` — |composite| ≥ 40 AND ≥ (n-1) TFs agree (e.g. 3-of-4)
- `weak` — |composite| ≥ 25 AND ≥ 2 TFs agree
- `none` — below those

### 4b. Sizing map (replaces conviction table)

For TREND-ALIGNED alerts (normal case — the alert's direction matches the expected move):

| readiness | direction at key level? | sizing | entry mode |
|---|---|---|---|
| strong | yes | **full** (1.5% risk) | market on 15M BOS + rejection close |
| strong | no | watchlist | add to level_alerts |
| moderate | yes | **half** (0.5% risk) | anticipation LIMIT at level, SL beyond invalidation |
| moderate | no | watchlist | add to level_alerts |
| weak | yes | watchlist only | add as proximity alert |
| weak | no | watchlist only | add as proximity alert |
| none | any | skip | no alert unless thesis-new |

For COUNTER-TREND FADES (alert explicitly marks an extreme to fade — e.g.
"intervention red line", "BoJ defends", "overextended", "capitulation"):

Confluence OPPOSING the alert direction is **corroborating**, not blocking.
Price extended in the "wrong" direction (i.e. toward the fade level) is
exactly what creates the rejection edge. Use the confluence magnitude as
an extension gauge:

| |composite| | sizing for counter-trend fade |
|---|---|
| ≥ 40 against alert | **half** (0.5% risk) — extension is real, fade is live |
| ≥ 25 against alert | watchlist only, arm on rejection wick |
| < 25 | no setup |

A counter-trend alert is one whose note contains any of:
`intervention`, `red line`, `red-line`, `defends`, `BoJ`, `fade`,
`overextended`, `capitulation`, `exhaustion`, `parabolic`.

Key level proximity threshold (for LIMIT arming):
    near = dist <= max(1 × H1_ATR, 0.5 × H4_ATR, 30 pips)
Why the floor of 30 pips: in low-vol regimes H1 ATR can collapse to
<20 pips, which would make every alert unarmable. The H4 fallback and
absolute floor keep the criterion useful across volatility regimes.

R:R floor: **1.5:1**. Below 1.5:1 is skip.

### 4c. Anticipation LIMIT entries — MANDATORY when conditions met

Old framework waited for candle-close confirmation and missed 30–60p of the move repeatedly. You MUST use LIMIT entry (not market-on-confirmation) when ALL THREE hold:

- Readiness is **moderate or better** per Step 4b (for trend-aligned alerts: `readiness ≥ moderate` AND direction matches; for counter-trend fades: `|composite| ≥ 40` against the alert direction)
- Price is within `max(1 × H1 ATR, 0.5 × H4 ATR, 30 pips)` of a clean structural level (alert level, fresh OB, unmitigated FVG, swept extreme)
- R:R from the level to the next opposing structure is ≥ 1.5:1

Execution:
- Place LIMIT at the level (not current price)
- SL beyond the invalidation (the wick extreme for sweeps, beyond the OB for OBs)
- Half size if readiness=moderate, full if strong
- Do NOT cancel as price approaches. The pre-commitment is the edge.

If you choose market-on-confirmation instead of LIMIT, your tick summary MUST explain why — otherwise assume LIMIT is the required action.

### 4d. Skip criteria — tighter

Skip only if:
- `readiness = none` AND no fresh catalyst
- R:R < 1.5:1 even with stop widening
- `risk_guard` would reject (correlation, daily loss, exposure)
- Binary event T-30min blackout
- Same-instrument loss in last 24h AND thesis unchanged
- Counterfactual says this alert_id had `hit_rate < 30%` across ≥3 fires

"News ambiguity" is NOT a skip reason. All markets have ambiguity; entering against structure is the sin.

### 4e. Event-specific triage

| Event | Question | Default |
|---|---|---|
| `level_enter` | Confluence readiness? | Per 4b sizing map |
| `level_cross` | Break+go or sweep+reverse? | Enter on retest (anticipation OK) |
| `level_exit` | Invalidated? | MODIFY/REMOVE, consider inverse ADD |
| **`liquidity_sweep` (NEW)** | **Fresh bounce point created by the tape** | **Treat the swept extreme as a new structural level. If confluence readiness ≥ moderate AGAINST the sweep wick (bearish sweep → sell bias; bullish sweep → buy bias), arm anticipation LIMIT entry per 4c. Payload includes `suggested_entry`, `suggested_sl` — use them or explain why not. Even if no existing watchlist alert covers this instrument, ADD one anchored to the swept level.** |
| `structure_bos` | Trend confirmed? | Tighten alerts toward bias |
| `structure_choch` | Trend flip signal | Update regime_note |
| `alert_audit_request` | News matched this alert's keywords | Re-score THIS alert. If readiness moderate+, arm anticipation LIMIT |
| `news_flash` | Regime-changing? | Re-score affected alerts only |
| `trail_candidate` | Move SL? | At +1R: SL → breakeven. At +2R: SL → +0.5R (lock half profit). At +3R: SL → +1.5R. Modify via `python3 forex/api.py modify <dealId> <sl> <tp>` |
| _time check_ | Trade is age ≥ 4h and setup is narrative/validated? | **Close at market.** Data shows narrative alerts (escalation, sweep-retest) pop 1-4h then revert at 24h. Don't hold overnight. |
| `position_closed` | Log trade | Update trade_history, daily_pnl |
| `daily_pnl_threshold` | Stop tier? | If `stop`: close all, set control, telegram |

**Why `liquidity_sweep` matters**: the old system waited for price to reach pre-authored watchlist levels that often became stale. The tape creates fresh bounce points constantly — wicks beyond recent extremes that snap back. These sweeps are where the real money enters. The watcher now emits these events automatically; your job is to act on them, not wait for a level you set two days ago.

### 4f. Alert audit requests (NEW — news-reactive)

The news watcher now emits `alert_audit_request` events when a breaking headline matches an active alert's keywords. Process these immediately:
- Re-run confluence on that alert's instrument
- If readiness improved to moderate/strong, arm anticipation LIMIT at the level
- If news *invalidates* the thesis (e.g. ceasefire extension kills escalation-based buy), MODIFY or REMOVE the alert
- Do NOT bump size purely on news. Bump ENTRY PROBABILITY by considering the setup; the tape still has to confirm.

## Step 5 — Deadline / thesis verification

If anchoring ≥2 ticks to a deadline (ceasefire, FOMC, CPI):
1. Require ≥2 independent sources agreeing on the UTC time
2. Log URLs in `forex_state.json.binary_event.sources`
3. Only 1 source → `binary_event.verified: false`, annotate "deadline unverified"
4. Do not reuse unverified deadlines across >3 ticks

## Step 6 — Manage existing positions FIRST, then execute new trades

### 6a. Position-age check (NEW — data-driven time exit)

For each open position, compute age = `now - opened_at`. Check `state/forex_state.json.open_positions[i]` for the `thesis_type` tag you set at entry (`narrative`, `trend`, `validated_sweep_retest`, `counter_trend_fade`, `scalp_entry`).

Apply the time cap by type:

| thesis_type | max hold | why |
|---|---|---|
| `narrative` / `validated_sweep_retest` | **4h** | Counterfactual data: setups pop 1-4h, revert at 24h |
| `counter_trend_fade` | 8h | Fades at extremes reset on a session, not a narrative |
| `trend` | 24h | Structural-trend trades deserve a day |
| `scalp_entry` | 1h | Scalp range → exit even if neither SL/TP hit |

If age ≥ max_hold, close at market:
```bash
python3 forex/api.py close <dealId>
bash send_telegram.sh "*TIME EXIT* <epic> <dir> <entry> → <mid> pnl=<x>"
```

### 6b. Execute new approved trades

When entering a trade, ALWAYS tag `thesis_type` in `state/forex_state.json.open_positions[i].thesis_type` so 6a can time-exit correctly.

```bash
python3 forex/risk_guard.py check <EPIC> <BUY|SELL> <size> <stop_level> <profit_level>
```

Only if `"approved": true`:

```bash
python3 forex/api.py open <EPIC> <BUY|SELL> <size> <stop_level> <profit_level>
```

Telegram on every open/close/modify:
```bash
bash send_telegram.sh "*TRADE EXECUTED* ..."
```

## Step 7 — Position / news check (budget-aware)

- **Skip web search if**: any of (last search <20 min ago) OR (news_watcher emitted news_flash events this tick that you've already read) OR (0 open positions AND regime stable for 4+ hours).
- Otherwise: 2 targeted searches, rotating through gold, crude, USDJPY+DXY, specific active situations. Don't do the same rotation every tick — vary.
- Material headline → update `regime_note` + telegram if position-affecting.
- Web search costs real API budget; the news_watcher daemon already covers the routine news surface. Your job here is to fill the gaps, not duplicate.

## Step 8 — MANDATORY AUDIT (every tick, no skipping)

### 8a. Watchlist audit — with TWO mechanical rules

Every pair in `instruments` needs matching `structure_watch`. Default TFs: `["HOUR", "MINUTE_15"]`. Active-setup pairs may add `HOUR_4`. BTC: `["HOUR_4", "HOUR"]`.

**Mechanical rule 1 — ZONE STALENESS AUTO-REMOVE** (NEW):
An alert is AUTO-REMOVE (no discretion) if ALL THREE are true:
1. `|price - level| > 3 × ATR(HOUR_4)` for that instrument
2. No catalyst in the last 24h delivered an approach move
3. Last 20 M15 closes are not trending toward the level

Show the ATR math in your audit line. Example: `audusd_breakdown_trigger REMOVE (dist=89p, 3×H4ATR=60p, no catalyst 24h)`.

Replace removed alerts with a new zone anchored to the current week's swept liquidity / fresh OB / FVG.

**Mechanical rule 2 — COUNTERFACTUAL-DRIVEN PRUNE**:
- `fires >= 3 AND hit_rate < 0.40 at 4h` → REMOVE (noise — 4h is the decisive horizon; don't use 24h)
- `fires >= 3 AND hit_rate > 0.60 at 4h` → KEEP even if stale AND promote (see Rule 3 below)
- `fires < 3` → decide on merits

**Mechanical rule 3 — VALIDATED-ALERT PROMOTION** (NEW):
An alert that has fired ≥ 2 times with **hit_rate ≥ 0.60 at 4h** is "validated". Validated alerts:
- Earn the right to fire **naked LIMIT** at moderate+ readiness — note-overrides like "require rejection confirmation" are RELAXED for them. The counterfactual is the proof.
- Use a **4-hour HARD TIME EXIT** on any trade entered via a validated narrative/escalation alert. Per-fire data shows these setups pop 1-4h then revert at 24h — take the move, exit on time, don't hold overnight.

Examples from current ledger (as of last run — update this table when state changes):
- `gold_escalation_buy_zone` — 10 fires, 90%/80% 1h/4h → validated, 4h time-stop
- `oil_sweep_9323_buy_retest` — 4 fires, 75%/100% 1h/4h → validated, 4h time-stop
- `gold_sweep_4721_sell_retest` — 5 fires, 60%/100% 1h/4h → validated, 4h time-stop

Note: "validated" applies ONLY for promotion / sizing relaxation. It does NOT override hard risk checks (SL/TP mandatory, risk_guard approval, exposure caps).

For each level_alert + structure_watch pair, assign: KEEP | MODIFY | REMOVE | ADD.

Not-KEEP triggers:
- Price moved beyond trigger and kept going
- Direction contradicts last bar_close / structure / news
- Note references situation no longer true
- Sharper level now exists
- Position opened/closed → management alerts may need adjustment

Stale patterns to cull: signal-only / REMOTE / low-prob / duplicates / outdated-note / contradicted-direction.

### 8b. News-query audit — `state/news_queries.json`

Per query: KEEP / TUNE / REMOVE / ADD. Prune queries that only produce stale archive republishes.

### 8c. ATTENTION MATRIX — mandatory 9-row output (NEW)

Every tick MUST emit exactly this table, one row per pair:

```
attention matrix:
  EURUSD    swing=<NONE|WATCH|ARMED>  scalp=<ACTIVE|OFF|N/A>  readiness=<strong|moderate|weak|none>  reason: <1-line>
  GBPUSD    …
  AUDUSD    …
  USDJPY    …
  USDCAD    …
  USDCHF    …
  GOLD      …  scalp=N/A (spread 5p)
  OIL_CRUDE …  scalp=N/A (spread 3-4p)
  BTCUSD    …  scalp=N/A (spread 50p)
```

- `swing=NONE` → no active alert and readiness < moderate
- `swing=WATCH` → alert exists but price not at trigger OR readiness=weak
- `swing=ARMED` → alert + readiness ≥ moderate + price within `max(1×H1_ATR, 0.5×H4_ATR, 30p)` (the Step 4c proximity threshold)
- `scalp=ACTIVE` → scalp_config.enabled=true for this pair AND engine not halted
- `scalp=HALTED` → 3-consecutive-loss halt in progress; recovers automatically
- `scalp=OFF` → scalp_config.enabled=false with a reason (pip-value conversion pending for JPY/CHF/CAD, etc.)
- `scalp=N/A` → spread economics forbid (GOLD 5p, OIL 4p, BTC 50p)

If a pair is neither ARMED nor ACTIVE, the reason must state WHY you aren't acting on it. This forces coverage — no pair can be silently ignored because the loud pair is getting focus.

### 8d. Scalp config audit (NEW)

Read `state/forex_scalp_config.json` AND `state/forex_scalp_status.json`.

Currently scalpable: **EURUSD, GBPUSD, AUDUSD** only (USD-quote pairs — shadow P/L math is direct). USDJPY / USDCHF / USDCAD are hard-disabled in the engine pending per-pair pip-value conversion; don't flip their `enabled` flag (engine ignores it anyway).

For the 3 live pairs:
- Engine halt → read from status file's `halted` map. Respect; don't override.
- If win rate (from ledger) <40% on ≥20 trades → set `enabled: false, reason: "wr_below_40pct"`
- If regime shifted (surprise central bank, risk-off flash) → reset `bias` to neutral
- If session transition matters for this pair's mode, note in `reason`

Output: `scalp audit: EURUSD=KEEP(wr 52%) GBPUSD=KEEP(no trades yet) AUDUSD=HALT(3L, 2h remaining) USDJPY/CHF/CAD=pip-conv-pending GOLD/OIL/BTC=spread-blocked`

### 8e. Write updates atomically

Rewrite files only if items are not KEEP. Output compact audit + attention matrix to stdout.

## Step 9 — Mark events consumed

```bash
python3 - <<'PY'
processed_ids = [...]   # every event_id you read
with open("state/forex_events_consumed.txt", "a") as f:
    for eid in processed_ids:
        f.write(eid + "\n")
PY
```

Every event read in Step 1 must appear here.

## Step 10 — Persist + commit

Update `state/forex_state.json`:
- new positions, trade outcomes, alert changes
- `binary_event`, `regime_note`
- **canonical timestamps only** — use `tick_ts_utc` + `state_ts_utc` (the other 7 legacy fields are being phased out; don't write them)

Append to `state/daily/$(date +%Y-%m-%d).json`.

Publish dashboard data + commit:

```bash
cd /Users/rajneeshmishra/Downloads/stock-pulse
bash docs/publish_forex.sh

# Git push with retry — no silent-fail
for i in 1 2 3; do
  git add state/ docs/ && git commit -m "tick: $(date +%H:%M) forex auto — <one-line>" && \
    git push origin main && break
  sleep 10
done
```

## Step 11 — Done

```
tick done: N events, M opened, K closed, S skipped; binary=<name|none>; scalp=<status>; attention_covered=9/9
```

Exit.

---

## INVARIANTS

1. Every order has SL + TP.
2. Every trade passes `risk_guard`.
3. Sizing is from **readiness tier × level proximity** (Step 4b). Not a binary gate.
4. Every event in Step 1 is marked consumed.
5. Step 8 audit runs every tick. 9-row attention matrix is mandatory.
6. Git push has 3× retry; don't let it fail silently.
7. No background tools. No wakeups. No asking.
8. News reacts via `alert_audit_request` (targeted re-audit), NEVER via blind conviction bump.
9. Deadlines require 2 sources.
10. Scalp engine handles its own entries mechanically; you only tune its config.
