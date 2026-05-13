#!/bin/bash
# Daily heartbeat for the Mac iMessage/Notes/Reminders bridge over Tailscale.
# Surfaces silent failures of the kind that hid for 6 days in early May 2026:
# - Tailscale stopped on Mac
# - Bridge process crashed / not bound to port 8733
# - Mac asleep with Tailscale daemon paused
# - Tailnet routing broken VPS-side
#
# Pattern matches /opt/clawdia/history_prune.sh (shipped 2026-05-09).
# Runs on the VPS (NOT the Mac) so we always have a working alert path
# even when the bridge itself is the thing that is down.

set -euo pipefail

source /opt/clawdia/scripts/alert.sh
trap 'rc=$?; notify "mac_bridge_heartbeat failed" "Exit $rc at line $LINENO. See /var/log/clawdia/mac_bridge_heartbeat.log."' ERR

BRIDGE_HOST=100.77.185.52
BRIDGE_PORT=8733
BRIDGE_URL="http://${BRIDGE_HOST}:${BRIDGE_PORT}/messages_recent"
LOG_DIR=/var/log/clawdia
LOG=$LOG_DIR/mac_bridge_heartbeat.log
mkdir -p "$LOG_DIR"
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Probe the bridge. Acceptable success signals:
#   1. Valid JSON (200 with real data, or empty list)
#   2. {"error":"bad token"} (bridge alive, just rejecting our auth-less probe)
# Anything else (timeout, connection refused, HTML error page) = bridge down.
RESPONSE=$(curl -sS -m 15 -X POST "$BRIDGE_URL" \
    -H "Content-Type: application/json" \
    -d "{\"limit\":1}" 2>&1 || echo "CURL_FAILED:$?")

if echo "$RESPONSE" | grep -qE "(\"error\":\s*\"bad token\"|^\[|^\{)"; then
    # Bridge responded coherently — its alive
    echo "[$TS] OK: bridge responded (truncated: $(echo "$RESPONSE" | head -c 80))" >> "$LOG"
    if [ -n "${ALERT_BOT_TOKEN:-}" ] && [ -n "${ALERT_CHAT_ID:-}" ]; then
        curl -s -X POST "https://api.telegram.org/bot${ALERT_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${ALERT_CHAT_ID}" \
            --data-urlencode "text=✓ mac bridge: alive" \
            > /dev/null 2>&1 || echo "[$TS] heartbeat send failed" >> "$LOG"
    fi
else
    # Bridge unreachable or broken
    echo "[$TS] FAIL: $RESPONSE" >> "$LOG"
    notify "Mac bridge unreachable" "Probe to ${BRIDGE_URL} did not return a valid response. Most likely causes: Mac Tailscale stopped, bridge process down, or Mac asleep. Response excerpt: $(echo "$RESPONSE" | head -c 200)"
fi
