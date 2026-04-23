#!/usr/bin/env bash
# Copy forex state into docs/data/forex/ so the static dashboard can fetch it.
# Called at the end of every tick by Claude (or manually).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$REPO/docs/data/forex"
cp "$REPO/state/forex_state.json"              "$REPO/docs/data/forex/state.json"
cp "$REPO/state/forex_watchlist_signals.json"  "$REPO/docs/data/forex/watchlist.json"
cp "$REPO/state/forex_counterfactual_summary.json" "$REPO/docs/data/forex/counterfactual.json"
[ -f "$REPO/state/forex_scalp_config.json" ] && cp "$REPO/state/forex_scalp_config.json" "$REPO/docs/data/forex/scalp_config.json" || true
[ -f "$REPO/state/forex_scalp_status.json" ] && cp "$REPO/state/forex_scalp_status.json" "$REPO/docs/data/forex/scalp_status.json" || true

# Publish scalp ledger — last N rows of the jsonl, paired open→close into
# trades for the dashboard. Invisible otherwise; ledger file is append-only
# and only the scalp engine touches it at write time.
python3 - <<'PY'
import json, os
from pathlib import Path
repo = Path(os.environ.get('REPO') or '.')
src = repo / 'state' / 'forex_scalp_ledger.jsonl'
dst = repo / 'docs' / 'data' / 'forex' / 'scalp_ledger.json'
if not src.exists():
    dst.write_text(json.dumps({"trades": [], "stats": {}, "raw": []}, indent=2))
else:
    rows = []
    for line in src.open():
        try: rows.append(json.loads(line))
        except Exception: pass

    # Pair opens to closes chronologically
    opens = {}                # epic -> open row
    trades = []
    for r in rows:
        k = r.get('kind')
        epic = r.get('epic')
        if k == 'opened' and epic:
            opens[epic] = r
        elif k == 'closed' and epic and epic in opens:
            o = opens.pop(epic)
            from datetime import datetime
            try:
                ot = datetime.fromisoformat(o['ts_utc'].replace('Z','+00:00'))
                ct = datetime.fromisoformat(r['ts_utc'].replace('Z','+00:00'))
                held_min = round((ct - ot).total_seconds() / 60, 1)
            except Exception:
                held_min = r.get('held_min')
            trades.append({
                "opened_at": o.get('ts_utc'),
                "closed_at": r.get('ts_utc'),
                "epic": epic,
                "direction": o.get('direction'),
                "setup": o.get('setup'),
                "entry": o.get('entry'),
                "exit":  r.get('exit'),
                "sl":    o.get('sl'),
                "tp":    o.get('tp'),
                "size":  o.get('size'),
                "how":   r.get('how'),
                "pnl_usd": r.get('pnl_usd'),
                "held_min": held_min,
                "shadow": o.get('shadow'),
            })

    # Aggregate stats (all-time + today)
    import datetime as _dt
    today_str = _dt.datetime.utcnow().strftime('%Y-%m-%d')
    def stats(sub):
        # Classify by `how` — pnl_usd is rounded to 2dp and FP tiny-loss
        # collapses to 0.0, so filtering by pnl sign miscounts losses.
        # Compute pips so UI has a meaningful decision-quality number even
        # when USD values are microscopic (shadow sizing cap).
        PIP = {'EURUSD':0.0001,'GBPUSD':0.0001,'AUDUSD':0.0001,'USDCAD':0.0001,
               'USDCHF':0.0001,'USDJPY':0.01,'GOLD':0.1,'OIL_CRUDE':0.01,'BTCUSD':1.0}
        if not sub:
            return {"count": 0, "wins": 0, "losses": 0, "time_exits": 0,
                    "win_rate": None, "net_pips": 0, "net_pnl_usd": 0}
        tp = [t for t in sub if t.get('how') == 'tp_hit']
        sl = [t for t in sub if t.get('how') == 'sl_hit']
        te = [t for t in sub if t.get('how') == 'time_exit']
        def pips_of(t):
            pip = PIP.get(t.get('epic'), 0.0001)
            entry = t.get('entry') or 0
            exitp = t.get('exit') or 0
            d = (exitp - entry) / pip
            return d if t.get('direction') == 'BUY' else -d
        net_pips = round(sum(pips_of(t) for t in sub), 1)
        return {
            "count": len(sub),
            "wins": len(tp),
            "losses": len(sl),
            "time_exits": len(te),
            "win_rate": round(len(tp) / len(sub), 3) if sub else None,
            "net_pips": net_pips,
            "net_pnl_usd": round(sum((t.get('pnl_usd') or 0) for t in sub), 3),
        }
    today = [t for t in trades if (t.get('closed_at') or '').startswith(today_str)]
    all_stats = stats(trades)
    today_stats = stats(today)
    # Also count rejected opens
    rejected = sum(1 for r in rows if r.get('kind') == 'rejected')

    dst.write_text(json.dumps({
        "generated_at": _dt.datetime.utcnow().isoformat() + "Z",
        "stats": {"all": all_stats, "today": today_stats, "rejected_total": rejected},
        "trades": trades[-25:],      # last 25 for the UI
    }, indent=2, default=str))
PY
# Also publish a lightweight "last activity" feed for the ticker — pull last 30 ticks
python3 - <<'PY'
import json, os, glob
from pathlib import Path
repo = Path(os.environ.get('REPO') or '.')
state = json.loads((repo/'state/forex_state.json').read_text())
ticks = state.get('tick_history', []) or []
# Also fold in today's daily log if present
import datetime
today = datetime.datetime.utcnow().strftime('%Y-%m-%d')
dfile = repo/'state'/'daily'/f'{today}.json'
daily = []
if dfile.exists():
    try:
        raw = json.loads(dfile.read_text())
        daily = raw.get('ticks', []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    except Exception:
        daily = []
feed = (ticks[-30:] + daily[-30:])[-40:]
(repo/'docs/data/forex/feed.json').write_text(json.dumps({
    'generated_at': datetime.datetime.utcnow().isoformat()+'Z',
    'ticks': feed,
}, indent=2))
PY
echo "forex data published to docs/data/forex/"
