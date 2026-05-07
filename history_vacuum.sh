#!/bin/bash
# Weekly VACUUM to reclaim space after history deletes.
# DELETE alone leaves the file size unchanged in sqlite; VACUUM rebuilds.

set -euo pipefail

source /opt/clawdia/scripts/alert.sh
trap 'rc=$?; notify "history_vacuum failed" "Exit code $rc at line $LINENO."' ERR

DB=/var/lib/clawdia/memory.db
LOG=/var/log/clawdia/history_prune.log
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

SIZE_BEFORE=$(stat -c %s "$DB")
sqlite3 "$DB" "VACUUM;"
SIZE_AFTER=$(stat -c %s "$DB")
RECLAIMED=$((SIZE_BEFORE - SIZE_AFTER))
echo "[$TS] VACUUM: $SIZE_BEFORE -> $SIZE_AFTER bytes (reclaimed $RECLAIMED)" >> "$LOG"
