#!/usr/bin/env bash
# Gameplay logs shipped back from the iPad (written by game/server.js to
# infra/client.log). Safe to run any time; read-only.
#
#   ./infra/logs.sh          logs from the most recent game played
#   ./infra/logs.sh --live   recent lines, then follow live while a game is played
#   ./infra/logs.sh --all    the whole log
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=infra/client.log

if [ ! -f "$LOG" ]; then
  echo "No logs yet: $LOG doesn't exist." >&2
  echo "It is created when a loaded game page first POSTs to /log —" >&2
  echo "make sure the server is up (./infra/start.sh) and the iPad has reloaded the game." >&2
  exit 1
fi

case "${1:-}" in
  --live)
    tail -n 30 -f "$LOG"
    ;;
  --all)
    cat "$LOG"
    ;;
  '')
    # Everything from the last "game start" marker onward; if no game has
    # been started yet (menu only), fall back to the last 50 lines.
    start=$(grep -n 'game start' "$LOG" | tail -1 | cut -d: -f1 || true)
    if [ -n "$start" ]; then
      tail -n "+$start" "$LOG"
    else
      tail -n 50 "$LOG"
    fi
    ;;
  *)
    echo "Usage: $0 [--live|--all]" >&2
    exit 1
    ;;
esac
