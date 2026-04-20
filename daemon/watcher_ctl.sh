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
LABEL="com.stockpulse.forexwatcher"
PLIST_SRC="$REPO/daemon/com.stockpulse.forexwatcher.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
CONTROL="$REPO/state/forex_watcher.control"
STATUS="$REPO/state/forex_watcher_status.json"
EVENTS="$REPO/state/forex_events.jsonl"
LOG_OUT="$REPO/logs/forex_watcher.out.log"
LOG_ERR="$REPO/logs/forex_watcher.err.log"

cmd="${1:-help}"

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
    # Pretty-print each event line
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

  help|*)
    echo "Usage: $0 {install|uninstall|start|stop|pause|run|status|logs|errlog|events}"
    exit 1
    ;;
esac
