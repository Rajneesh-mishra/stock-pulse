#!/usr/bin/env bash
# Control script for the forex watcher daemon.
# Usage:
#   daemon/watcher_ctl.sh install   # copy plist to LaunchAgents + load
#   daemon/watcher_ctl.sh uninstall # unload + remove plist
#   daemon/watcher_ctl.sh start     # launchctl kickstart
#   daemon/watcher_ctl.sh stop      # graceful stop via control file
#   daemon/watcher_ctl.sh pause     # set control file to "pause"
#   daemon/watcher_ctl.sh run       # set control file to "run"
#   daemon/watcher_ctl.sh status    # show launchd state + status.json
#   daemon/watcher_ctl.sh logs      # tail -f the stdout log
#   daemon/watcher_ctl.sh errlog    # tail -f the stderr log
#   daemon/watcher_ctl.sh events    # tail -f events.jsonl (human-readable)

set -euo pipefail

REPO="/Users/rajneeshmishra/Downloads/stock-pulse"
EVENTS="$REPO/state/forex_events.jsonl"
cmd="${1:-help}"

# Daemon selector only matters for daemon-specific commands. Commands that
# read the shared events file (recent/events/tally) ignore $2 entirely.
DAEMON_COMMANDS="install uninstall start stop pause run status logs errlog"
needs_daemon() { [[ " $DAEMON_COMMANDS " == *" $1 "* ]]; }

if needs_daemon "$cmd"; then
  DAEMON="${2:-watcher}"
  case "$DAEMON" in
    watcher)
      LABEL="com.stockpulse.forexwatcher"
      CONTROL="$REPO/state/forex_watcher.control"
      STATUS="$REPO/state/forex_watcher_status.json"
      LOG_OUT="$REPO/logs/forex_watcher.out.log"
      LOG_ERR="$REPO/logs/forex_watcher.err.log"
      ;;
    posync|positionsync)
      LABEL="com.stockpulse.forexpositionsync"
      CONTROL="$REPO/state/forex_position_sync.control"
      STATUS="$REPO/state/forex_position_sync_status.json"
      LOG_OUT="$REPO/logs/forex_position_sync.out.log"
      LOG_ERR="$REPO/logs/forex_position_sync.err.log"
      ;;
    *) echo "Unknown daemon: $DAEMON (use 'watcher' or 'posync')"; exit 1 ;;
  esac
  PLIST_SRC="$REPO/daemon/$LABEL.plist"
  PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
fi

case "$cmd" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"
    cp "$PLIST_SRC" "$PLIST_DST"
    launchctl bootstrap "gui/$UID" "$PLIST_DST" 2>/dev/null \
      || launchctl load -w "$PLIST_DST"
    echo run > "$CONTROL"
    echo "installed + loaded: $LABEL"
    ;;

  uninstall)
    echo stop > "$CONTROL"
    sleep 2
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null \
      || launchctl unload -w "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "uninstalled: $LABEL"
    ;;

  start)
    echo run > "$CONTROL"
    launchctl kickstart -k "gui/$UID/$LABEL" 2>/dev/null \
      || launchctl load -w "$PLIST_DST"
    echo "start requested"
    ;;

  stop)
    echo stop > "$CONTROL"
    echo "stop requested (daemon exits on next loop iteration)"
    ;;

  pause)
    echo pause > "$CONTROL"
    echo "paused (no polls; daemon stays alive)"
    ;;

  run)
    echo run > "$CONTROL"
    echo "resumed"
    ;;

  status)
    echo "--- launchd ---"
    launchctl list | grep -E "$LABEL" || echo "(not loaded)"
    echo "--- control file ---"
    cat "$CONTROL" 2>/dev/null || echo "(absent → defaults to run)"
    echo "--- status file ---"
    cat "$STATUS" 2>/dev/null || echo "(no status yet)"
    ;;

  logs)
    tail -f "$LOG_OUT"
    ;;

  errlog)
    tail -f "$LOG_ERR"
    ;;

  events)
    if [ ! -f "$EVENTS" ]; then
      echo "(no events yet)"
      exit 0
    fi
    # Pretty-print each event line (streaming)
    tail -f "$EVENTS" | while read -r line; do
      echo "$line" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(f\"[{d['ts_utc'][:19]}Z] {d['type']:18s} {d.get('instrument',''):10s} {d.get('alert_id') or d.get('timeframe') or ''} price={d.get('payload',{}).get('price') or d.get('payload',{}).get('last_close')}\")
except Exception as e:
    print(line)
"
    done
    ;;

  live|snapshot)
    # Full dashboard. `live` loops every 10s, `snapshot` is one-shot.
    _render() {
      clear
      echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
      echo "  FOREX SYSTEM — $(date '+%Y-%m-%d %H:%M:%S %Z')"
      echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
      echo
      echo "▸ DAEMONS (launchd, KeepAlive):"
      launchctl list | grep stockpulse | awk '{printf "    %-35s  pid=%s\n", $3, $1}'
      echo
      python3 - <<'PY'
import json, datetime as dt, subprocess
def age(ts):
    t = dt.datetime.fromisoformat(ts.replace("Z","+00:00"))
    return int((dt.datetime.now(dt.timezone.utc)-t).total_seconds())

# Heartbeats
for name, path in [("WATCHER", "state/forex_watcher_status.json"),
                   ("POSYNC ", "state/forex_position_sync_status.json")]:
    try:
        s = json.load(open(path))
        pos = f"open={s.get('open_position_count')}" if 'open_position_count' in s else ''
        print(f"▸ {name}: {s['status']}  {age(s['last_poll'])}s ago  "
              f"polls={s['polls_total']}  events={s.get('events_emitted',0)}  "
              f"errors={s.get('errors',0)}  {pos}")
    except Exception as e:
        print(f"▸ {name}: ERROR {e}")

# Broker
print()
try:
    r = subprocess.run(["python3","forex/api.py","account"], capture_output=True, text=True, timeout=8)
    d = json.loads(r.stdout)
    print(f"▸ BROKER: balance=${d['balance']}  available=${d['available']}  pnl=${d['profit_loss']}")
except Exception as e:
    print(f"▸ BROKER: ERROR {e}")

# Positions
try:
    r = subprocess.run(["python3","forex/api.py","positions"], capture_output=True, text=True, timeout=8)
    d = json.loads(r.stdout)
    if d["count"] == 0:
        print(f"▸ POSITIONS: none")
    else:
        print(f"▸ POSITIONS: {d['count']} open")
        for p in d["positions"]:
            print(f"    {p['direction']} {p['size']} {p['epic']}  entry={p['level']}  sl={p['stopLevel']}  tp={p['profitLevel']}  upl=${p['upl']}")
except Exception as e:
    print(f"▸ POSITIONS: ERROR {e}")

# Live prices + distance to nearest alert
print()
print("▸ WATCHLIST — distance to nearest alert:")
try:
    signals = json.load(open("state/forex_watchlist_signals.json"))
    epics = sorted(set(a['instrument'] for a in signals['level_alerts']))
    for e in epics:
        try:
            r = subprocess.run(["python3","forex/api.py","price",e], capture_output=True, text=True, timeout=8)
            p = json.loads(r.stdout)['prices'][0]
            mid = (p['bid']+p['offer'])/2
            alerts = [a for a in signals['level_alerts'] if a['instrument']==e]
            nearest=None
            for a in alerts:
                if 'zone_low' in a:
                    inside = a['zone_low']<=mid<=a['zone_high']
                    dist = 0 if inside else min(abs(mid-a['zone_low']),abs(mid-a['zone_high']))
                else:
                    dist = abs(mid-a['level'])
                if nearest is None or dist < nearest[0]:
                    nearest = (dist, a, inside if 'zone_low' in a else None)
            if nearest:
                pips = nearest[0] * (100 if e=='USDJPY' else 1 if e in ('OIL_CRUDE','GOLD','BTCUSD') else 10000)
                marker = "🎯 IN ZONE" if nearest[2] is True else f"{pips:>7.1f} pips"
                print(f"    {e:10s}  mid={mid:>10.5f}   {marker}   '{nearest[1]['id']}'")
        except Exception as ex:
            print(f"    {e}: ERROR {ex}")
except Exception as e:
    print(f"    ERROR {e}")

# Recent events
print()
print("▸ RECENT EVENTS (last 5):")
try:
    with open("state/forex_events.jsonl") as f:
        lines = [l for l in f if l.strip()][-5:]
    if not lines:
        print("    (none yet — watchlist zones all away from current prices)")
    for line in lines:
        d = json.loads(line)
        c = "✓" if d.get("consumed_by_claude") else " "
        p = d.get("payload",{})
        price = p.get("price") or p.get("last_close") or ""
        detail = d.get("alert_id") or d.get("timeframe") or p.get("tier") or ""
        print(f"    {c} [{d['ts_utc'][:19]}Z] {d['type']:20s} {d.get('instrument','-'):10s} {detail:30s} {price}")
except FileNotFoundError:
    print("    (no events file yet)")
except Exception as e:
    print(f"    ERROR {e}")
PY
      echo
      if [ "$cmd" = "live" ]; then
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "  refreshing every 10s  (Ctrl-C to quit)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
      fi
    }

    if [ "$cmd" = "snapshot" ]; then
      _render
    else
      trap 'echo; echo "live dashboard stopped"; exit 0' INT
      while true; do _render; sleep 10; done
    fi
    ;;

  recent)
    # One-shot pretty-print of the last N events (default 20). No follow.
    # Usage: watcher_ctl.sh recent [N]
    N="${2:-20}"
    if [ ! -f "$EVENTS" ]; then
      echo "(no events yet)"
      exit 0
    fi
    python3 - "$EVENTS" "$N" <<'PY'
import json, sys
path, n = sys.argv[1], int(sys.argv[2])
with open(path) as f:
    lines = [l for l in f if l.strip()][-n:]
rows = []
for line in lines:
    try:
        d = json.loads(line)
        consumed = "✓" if d.get("consumed_by_claude") else " "
        p = d.get("payload", {})
        price = p.get("price") or p.get("last_close") or p.get("current_bid") or ""
        detail = (d.get("alert_id") or d.get("timeframe")
                  or p.get("tier") or p.get("direction") or "")
        rows.append(f"  {consumed} [{d['ts_utc'][:19]}Z] {d['type']:20s} "
                    f"{d.get('instrument','-'):10s} {detail:35s} {price}")
    except Exception:
        rows.append(f"  ? {line.rstrip()}")
print(f"Last {len(rows)} events (✓ = consumed by Claude):")
for r in rows:
    print(r)
PY
    ;;

  tally)
    # Event counts by type (quick "what fired most today?" view)
    if [ ! -f "$EVENTS" ]; then
      echo "(no events yet)"
      exit 0
    fi
    python3 - "$EVENTS" <<'PY'
import json, sys, collections
from datetime import datetime, timezone, timedelta
path = sys.argv[1]
c_total = collections.Counter()
c_24h = collections.Counter()
cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
unconsumed = 0
with open(path) as f:
    for line in f:
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            c_total[d['type']] += 1
            ts = datetime.fromisoformat(d['ts_utc'].replace('Z','+00:00'))
            if ts >= cutoff:
                c_24h[d['type']] += 1
            if not d.get('consumed_by_claude'):
                unconsumed += 1
        except Exception:
            pass
print(f"  Total events:     {sum(c_total.values())} (unconsumed by Claude: {unconsumed})")
print(f"  Last 24h:         {sum(c_24h.values())}")
print("  By type:")
for t, n in sorted(c_total.items(), key=lambda x: -x[1]):
    print(f"    {t:24s} {n:>4d}  (24h: {c_24h.get(t,0)})")
PY
    ;;

  help|*)
    cat <<USAGE
Usage: $0 <cmd> [watcher|posync]
  install        copy plist to LaunchAgents + load (default: watcher)
  uninstall      unload + remove plist
  start          start/kickstart
  stop           set control file to "stop" (graceful exit)
  pause          set control file to "pause"
  run            set control file to "run"
  status         show launchd state + status.json
  logs           tail -f stdout log
  errlog         tail -f stderr log
  events         tail -f events.jsonl (stream, pretty, all daemons)
  recent [N]     last N events (default 20, one-shot, pretty)
  tally          count of events by type (all-time + last 24h)
  live           full dashboard, refreshes every 10s (Ctrl-C to quit)
  snapshot       one-shot dashboard (same as live, no refresh)

Examples:
  $0 install           # install watcher
  $0 install posync    # install position-sync
  $0 status posync
USAGE
    exit 1
    ;;
esac
