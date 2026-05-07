#!/bin/bash
# Daily prune of conversation history older than the recall_recent window (7d).
# Logs row count deleted so we can audit retention enforcement.
# Pairs with weekly VACUUM (separate cron) to reclaim disk space.

set -euo pipefail

# Alert on any error
source /opt/clawdia/scripts/alert.sh
trap 'rc=$?; notify "history_prune failed" "Exit code $rc at line $LINENO. See /var/log/clawdia/history_prune.log for context."' ERR

DB=/var/lib/clawdia/memory.db
LOG_DIR=/var/log/clawdia
LOG=$LOG_DIR/history_prune.log

mkdir -p "$LOG_DIR"

# Count what we will delete BEFORE deleting (cheap, single index scan)
TO_DELETE=$(sqlite3 "$DB" "SELECT COUNT(*) FROM history WHERE ts < datetime('now', '-7 days');")
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [ "$TO_DELETE" -gt 0 ]; then
    sqlite3 "$DB" "DELETE FROM history WHERE ts < datetime('now', '-7 days');"
    REMAINING=$(sqlite3 "$DB" "SELECT COUNT(*) FROM history;")
    echo "[$TS] Pruned $TO_DELETE rows. Remaining: $REMAINING" >> "$LOG"
else
    REMAINING=$(sqlite3 "$DB" "SELECT COUNT(*) FROM history;")
    echo "[$TS] No rows older than 7 days. Total rows: $REMAINING" >> "$LOG"
fi
