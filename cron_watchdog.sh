#!/bin/bash
# Stale-log watchdog. Runs daily at 09:00 UTC (2hr after prune at 07:00).
# If the prune log has not been touched in 25+ hours, fires alert.
# Catches the failure mode where cron itself stops running, not just script errors.

set -euo pipefail

source /opt/clawdia/scripts/alert.sh

PRUNE_LOG=/var/log/clawdia/history_prune.log
MAX_AGE_MIN=1500  # 25 hours

if [ ! -f "$PRUNE_LOG" ]; then
    notify "Cron watchdog: log missing" "Expected $PRUNE_LOG to exist. Either cron has not run, or the log was deleted. Check: ls -la /var/log/clawdia/ and systemctl status cron"
    exit 0
fi

# find -mmin +N matches files modified MORE than N minutes ago
if find "$PRUNE_LOG" -mmin +${MAX_AGE_MIN} -print | grep -q .; then
    age_hours=$(( ($(date +%s) - $(stat -c %Y "$PRUNE_LOG")) / 3600 ))
    notify "Cron watchdog: prune log stale" "$PRUNE_LOG has not been modified in $age_hours hours. The history_prune cron may have stopped running. Check: systemctl status cron, ls /etc/cron.d/clawdia-history-retention, tail /var/log/clawdia/history_prune.log"
fi

# Silence on success (silence = healthy, per design)
