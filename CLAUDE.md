# Stock Pulse — Orchestrator Reference

This file contains invariants that MUST survive context compaction. The `/loop` orchestrator reads this on every tick.

## Telegram Config

- **Credentials**: stored in `.env` file at `/Users/rajneeshmishra/Downloads/stock-pulse/.env` (NEVER commit this file)
- **Send script**: `bash /Users/rajneeshmishra/Downloads/stock-pulse/send_telegram.sh "<message>"`
- **Parse mode**: Markdown
- The send script reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS from `.env` automatically

## File Paths

- **State file**: `/Users/rajneeshmishra/Downloads/stock-pulse/state/state.json`
- **Daily log**: `/Users/rajneeshmishra/Downloads/stock-pulse/state/daily/YYYY-MM-DD.json`
- **Dashboard current**: `/Users/rajneeshmishra/Downloads/stock-pulse/docs/data/current.json`
- **Dashboard timeline**: `/Users/rajneeshmishra/Downloads/stock-pulse/docs/data/timeline.json`
- **Dashboard history**: `/Users/rajneeshmishra/Downloads/stock-pulse/docs/data/history/YYYY-MM-DD.json`
- **Repo root**: `/Users/rajneeshmishra/Downloads/stock-pulse`

## Operational Rules

1. **ALL agents MUST be foreground** (never `run_in_background: true`) — background agents silently lose output
2. **Max 2-3 parallel agents** at any stage — reliability drops with more
3. **Max 3 deep-dive agents** per tick — hard cap, no exceptions
4. **Validate every agent result** — if empty/garbage, note the gap and proceed. One retry max for critical agents.
5. **State writes happen LAST** — after all analysis is done, before Telegram/dashboard
6. **No Telegram message on quiet ticks** — only send when there's something worth saying
7. **Dashboard push**: on events + every 30 min heartbeat. Skip on quiet ticks.
8. **Dedup**: read `last_scan.last_telegram_summary` before composing. Don't repeat the same alert. Check `alert_cooldowns` for per-category throttling.
9. **Web search for Indian data**: FII/DII only returns net figures (accept this). For sector data, use WebFetch on `https://www.icicidirect.com/research/equity/nse-bse-sector` specifically.
10. **Daily log**: append to today's file only. Read yesterday's file ONLY when a tracked situation or prediction explicitly references it.
11. **ALWAYS update `docs/data/current.json` and `docs/data/timeline.json`** every tick that modifies state — these are what the dashboard reads. Failing to update them leaves the dashboard showing stale data. current.json must reflect latest scenarios, tracker, market data, **and signals/avoid** (stock suggestions). timeline.json must have a new tick entry appended.
11a. **Update signals + avoid on every triggered tick** — if new data changes the thesis (e.g. crude spike, earnings result, tracker status change), update `signals` (action, stock, conviction 1-5, rationale, risk) and `avoid` in current.json accordingly. Don't leave stale conviction scores or outdated rationale.
12. **ALWAYS git push after every tick** — `git add docs/ state/ && git commit -m "tick: $(date +%H:%M) {trigger_type}" && git push origin main`. No confirmation needed. All permissions are pre-granted.
13. **Publish forex data to docs/ before commit** — run `bash docs/publish_forex.sh` at the end of every forex tick so docs/forex.html (the public dashboard at rajneesh-mishra.github.io/stock-pulse/forex.html) always reflects current state, watchlist, counterfactual summary, and feed.

## Agent Prompt Templates

### Sentinel Agent

```
You are a market sentinel scanning the Indian stock market.

CURRENT STATE (from state.json):
{paste state.json contents here}

TODAY'S LOG SO FAR:
{paste today's daily log summary or "No activity yet"}

YOUR JOB: Quick triage. Do a web search for "Indian stock market today" and "Nifty 50 today" to get a pulse.

Then answer:
1. Did anything significant happen since the last scan at {last_scan.timestamp}? (Nifty moved >0.5% from {last_scan.nifty_level}, major news broke, sector crash/rally)
2. Is any situation in the tracker escalating or de-escalating?
3. Is any calendar event within 3 days that hasn't been analyzed yet?
4. Is it time for a forward scan? (last_forward_scan was {last_scan.last_forward_scan} — do one if >3 hours ago during market hours, >6 hours otherwise)

OUTPUT exactly this JSON:
{
  "trigger": "none" | "reactive" | "predictive" | "calendar",
  "reason": "one line explaining why",
  "details": "relevant data points",
  "nifty_current": <number or null>,
  "forward_scan_needed": true | false,
  "market_status": "pre_market" | "open" | "post_market" | "closed" | "weekend"
}

Be conservative. Only trigger reactive/predictive if there's a REAL signal, not just noise.
```

### Sentinel Extended (Forward Scan)

```
You are doing a FORWARD SCAN of upcoming events and geopolitical situations relevant to Indian markets.

CURRENT CALENDAR: {paste calendar array}
CURRENT TRACKER: {paste tracker array}

Do multiple web searches:
1. "India stock market upcoming events this week"
2. "RBI policy date 2026" / "FOMC meeting date 2026" 
3. "Indian market earnings season dates"
4. "F&O expiry date India"
5. "geopolitical news affecting India markets today"

From your searches, update:
- CALENDAR: Add any new events with date, description, impact_level (1-5), sectors_affected. Remove events that have passed.
- TRACKER: Add any new geopolitical/macro situations. Update status of existing ones (watching/escalating/de-escalating/resolved). Remove resolved or stale ones.

NO HARDCODED items. Only add what you actually found in your searches.

OUTPUT as JSON:
{
  "calendar": [...updated array...],
  "tracker": [...updated array...]
}
```

### Market Pulse Agent (R1)

```
You are a market data collector for Indian equities. Get the NUMBERS, no opinions.

Web search and/or WebFetch to find:
- Nifty 50, Sensex, Bank Nifty: level, % change, day high/low
- India VIX: level and % change
- FII/DII: net buy/sell figures (in crores)
- Global cues: US futures (S&P 500, Nasdaq), crude oil (Brent), USD/INR, gold
- SGX Nifty or GIFT Nifty if available

For sector data, WebFetch this URL: https://www.icicidirect.com/research/equity/nse-bse-sector

OUTPUT: structured data, no commentary. Format:
{
  "indices": { "nifty": {...}, "sensex": {...}, "banknifty": {...} },
  "vix": {...},
  "fii_dii": { "fii_net": "...", "dii_net": "..." },
  "global": { "sp500_futures": "...", "crude": "...", "usdinr": "...", "gold": "..." },
  "sectors": { "top_gainers": [...], "top_losers": [...] },
  "data_timestamp": "..."
}
```

### News + Sector Scanner Agent (R1)

```
You are a news scanner for Indian stock markets.

Web search for:
1. "Indian stock market news today"
2. "NSE BSE breaking news"
3. "India earnings results today"
4. "{specific sector/theme from trigger}" if applicable

Find the 5-10 most relevant headlines. For each:
- Headline
- Source
- One-line context: WHY this matters for markets
- Affected sectors/stocks

Filter aggressively. Skip generic "market ends flat" headlines. Focus on news that could MOVE prices.

OUTPUT as JSON array of headlines with context.
```

### Deep Dive Agent (R2)

```
You are researching ONE specific stock or theme: {STOCK_OR_THEME}

Context from the broad scan: {CONTEXT_FROM_R1}

Do thorough web searches:
1. "{stock} share price today analysis"
2. "{stock} latest earnings results"
3. "{stock} analyst target price"
4. "{stock} promoter holding changes"
5. "{stock} technical analysis support resistance"

Compile:
- BULL CASE: Why this could go up. Catalysts, technicals, fundamentals.
- BEAR CASE: Why this could go down. Risks, red flags, headwinds.
- KEY LEVELS: Support, resistance, 52-week high/low
- CONVICTION: 1-5 (1 = weak signal, 5 = high conviction)

OUTPUT as structured JSON with bull_case, bear_case, key_levels, conviction, and summary.
```

### Scenario Mapper Agent (P1)

```
You are analyzing an approaching event/situation: {SITUATION}

Context: {DETAILS_FROM_SENTINEL}

Research this thoroughly:
1. What are the possible outcomes? (2-3 scenarios)
2. For each scenario: probability estimate, impact on Indian markets, specific sectors/stocks affected
3. Historical analogs: what happened last time something similar occurred?
4. Asymmetric positions: what benefits in multiple scenarios?

OUTPUT as JSON:
{
  "situation": "...",
  "timeframe": "...",
  "scenarios": [
    { "name": "...", "probability": "...", "impact": "...", "sectors": [...], "stocks": [...] }
  ],
  "positioning": { "asymmetric_bets": [...], "hedges": [...] },
  "watch_triggers": [...]
}
```

### Synthesis Agent (R3 / P2)

```
You are the final analyst synthesizing all research into a Telegram message.

INPUTS:
{ALL_PREVIOUS_AGENT_OUTPUTS}

LAST MESSAGE SENT: {last_scan.last_telegram_summary}
ALERT COOLDOWNS: {alert_cooldowns}

RULES:
- If this is substantially the same as the last message, output "SKIP" — don't send.
- Present BOTH bull and bear perspective. Be balanced.
- Be punchy and scannable — this is read on a phone.
- Include conviction score (1-5).
- Include specific levels/numbers, not vague commentary.
- End with what to WATCH for next.
- Max 200 words for alerts, 250 for predictive briefs.
- Use Markdown formatting compatible with Telegram.

OUTPUT: The exact message to send, or "SKIP" if nothing new.
MESSAGE TYPE: "alert" | "predictive" | "daily_wrap"
```

## Timing Reference

| Context | Sentinel Freq | Forward Scan | Dashboard Push |
|---------|--------------|--------------|----------------|
| Market hours (9:15-3:30 IST), quiet | 10 min | Every 3 hours | 30 min heartbeat |
| Market hours, volatile | 5 min | Every 2 hours | On events |
| Pre-market (8:30-9:15) | 10 min | Once at start | Once |
| Post-market (3:30-5 PM) | 15 min | Once | After daily wrap |
| Evening/night | 30 min | Every 4 hours | Skip |
| Weekend/holiday | 2 hours | Every 6 hours | Skip |

## Dashboard

- **Repo**: `https://github.com/Rajneesh-mishra/stock-pulse.git`
- **URL**: `https://rajneesh-mishra.github.io/stock-pulse/`
- **Push command**: `cd /Users/rajneeshmishra/Downloads/stock-pulse && git add docs/ state/ && git commit -m "tick: $(date +%H:%M) {trigger_type}" && git push origin main`
