#!/usr/bin/env bash
# Copy forex state into docs/data/forex/ so the static dashboard can fetch it.
# Called at the end of every tick by Claude (or manually).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$REPO/docs/data/forex"
cp "$REPO/state/forex_state.json"              "$REPO/docs/data/forex/state.json"
cp "$REPO/state/forex_watchlist_signals.json"  "$REPO/docs/data/forex/watchlist.json"
cp "$REPO/state/forex_counterfactual_summary.json" "$REPO/docs/data/forex/counterfactual.json"
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
    try: daily = json.loads(dfile.read_text())
    except Exception: daily = []
feed = (ticks[-30:] + daily[-30:])[-40:]
(repo/'docs/data/forex/feed.json').write_text(json.dumps({
    'generated_at': datetime.datetime.utcnow().isoformat()+'Z',
    'ticks': feed,
}, indent=2))
PY
echo "forex data published to docs/data/forex/"
