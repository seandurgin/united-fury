#!/bin/bash
# Alienware weekly health check

ISSUES=""

# Check disk
DISK_ROOT=$(df -h / | awk 'NR==2 {print $4}')
DISK_PCT=$(df / | awk 'NR==2 {print $5}' | tr -d '%')

# Check RAM
RAM_FREE=$(free -g | awk '/^Mem:/ {print $7}')
RAM_TOTAL=$(free -g | awk '/^Mem:/ {print $2}')

# Check for failed systemd services
FAILED=$(systemctl --failed --no-legend 2>/dev/null | wc -l)

# Check uptime
UPTIME=$(uptime -p)

# Check pending updates
UPDATES=$(apt list --upgradable 2>/dev/null | grep -v "Listing..." | wc -l)

# Check thresholds
if [ "$DISK_PCT" -gt 80 ]; then
    ISSUES="$ISSUES\n⚠️ Disk usage at ${DISK_PCT}%"
fi
if [ "$FAILED" -gt 0 ]; then
    ISSUES="$ISSUES\n⚠️ ${FAILED} failed systemd service(s)"
fi
if [ "$UPDATES" -gt 20 ]; then
    ISSUES="$ISSUES\n⚠️ ${UPDATES} pending apt updates"
fi

# Report
echo "Alienware Weekly Health Check"
echo "=============================="
echo "💾 Disk (/): ${DISK_ROOT} free (${DISK_PCT}% used)"
echo "🧠 RAM: ${RAM_FREE}GB free / ${RAM_TOTAL}GB total"
echo "⏱️ Uptime: $UPTIME"
echo "🔄 Pending apt updates: $UPDATES"
echo "🛠️ Failed services: $FAILED"
if [ -n "$ISSUES" ]; then
    echo -e "\nISSUES FOUND:$ISSUES"
    exit 2
else
    echo "✅ All checks passed"
fi
