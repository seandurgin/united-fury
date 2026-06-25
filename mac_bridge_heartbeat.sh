#!/bin/bash
# Mac bridge heartbeat — state-tracking version (shipped 2026-06-24).
#
# Daily canary at 14:00 UTC + every-15-min recovery detection during 14:00-22:00 UTC.
# Emits explicit recovery messages on down→up transitions, suppresses repeat "down"
# alerts to avoid noise. Outside the awake window, runs nothing (dark-wake safe).
#
# State file: /var/lib/clawdia/mac_bridge_state (shell-sourceable PREV_STATE/PREV_SINCE).
#
# Runs on the VPS (NOT the Mac) so the alert path is independent of the thing
# being monitored. Pattern matches /opt/clawdia/history_prune.sh.

set -euo pipefail

LOCK_FILE=/var/run/clawdia-mac-bridge-heartbeat.lock
exec 9>"$LOCK_FILE" 2>/dev/null || true
flock -n 9 || { echo "Another heartbeat already running, exiting."; exit 0; }

source /opt/clawdia/scripts/alert.sh
trap 'rc=$?; notify "mac_bridge_heartbeat failed" "Exit $rc at line $LINENO. See /var/log/clawdia/mac_bridge_heartbeat.log."' ERR

BRIDGE_HOST=100.77.185.52
BRIDGE_PORT=8733
BRIDGE_URL="http://${BRIDGE_HOST}:${BRIDGE_PORT}/messages_recent"
LOG_DIR=/var/log/clawdia
LOG=$LOG_DIR/mac_bridge_heartbeat.log
STATE_DIR=/var/lib/clawdia
STATE_FILE=$STATE_DIR/mac_bridge_state
mkdir -p "$LOG_DIR" "$STATE_DIR"
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
HOUR_UTC=$(date -u +"%-H")
MINUTE_UTC=$(date -u +"%-M")

# Load previous state (default to unknown on first run)
PREV_STATE="unknown"
PREV_SINCE="$TS"
if [ -r "$STATE_FILE" ]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE"
fi

# Probe bridge. Success = valid JSON or {"error":"bad token"}. Anything else = down.
RESPONSE=$(curl -sS -m 15 -X POST "$BRIDGE_URL" \
    -H "Content-Type: application/json" \
    -d "{\"limit\":1}" 2>&1 || echo "CURL_FAILED:$?")

if echo "$RESPONSE" | grep -qE "(\"error\":\s*\"bad token\"|^\[|^\{)"; then
    CUR_STATE="up"
    echo "[$TS] OK: bridge responded (truncated: $(echo "$RESPONSE" | head -c 80))" >> "$LOG"
else
    CUR_STATE="down"
    echo "[$TS] FAIL: $RESPONSE" >> "$LOG"
fi

# Decide what to send
SEND_ALIVE_PING=0
SEND_DOWN_ALERT=0
SEND_RECOVERY=0

# Daily canary fires at 14:00 UTC (only on the :00 tick, when bridge is up)
if [ "$HOUR_UTC" = "14" ] && [ "$MINUTE_UTC" -lt 5 ] && [ "$CUR_STATE" = "up" ]; then
    SEND_ALIVE_PING=1
fi

# State transitions
if [ "$PREV_STATE" = "up" ] && [ "$CUR_STATE" = "down" ]; then
    SEND_DOWN_ALERT=1
elif [ "$PREV_STATE" = "down" ] && [ "$CUR_STATE" = "up" ]; then
    SEND_RECOVERY=1
elif [ "$PREV_STATE" = "unknown" ] && [ "$CUR_STATE" = "down" ]; then
    SEND_DOWN_ALERT=1   # first run with bridge down — alert
fi

# Compute new "since" timestamp
if [ "$CUR_STATE" = "$PREV_STATE" ]; then
    NEW_SINCE="$PREV_SINCE"
else
    NEW_SINCE="$TS"
fi

# Send messages
if [ "$SEND_ALIVE_PING" = "1" ]; then
    if [ -n "${ALERT_BOT_TOKEN:-}" ] && [ -n "${ALERT_CHAT_ID:-}" ]; then
        curl -s -X POST "https://api.telegram.org/bot${ALERT_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${ALERT_CHAT_ID}" \
            --data-urlencode "text=✓ mac bridge: alive" \
            > /dev/null 2>&1 || echo "[$TS] heartbeat send failed" >> "$LOG"
    fi
fi

if [ "$SEND_DOWN_ALERT" = "1" ]; then
    notify "Mac bridge unreachable" "Probe to ${BRIDGE_URL} did not return a valid response. Most likely causes: Mac Tailscale stopped, bridge process down, or Mac asleep. Response excerpt: $(echo "$RESPONSE" | head -c 200)"
fi

if [ "$SEND_RECOVERY" = "1" ]; then
    PREV_EPOCH=$(date -u -d "$PREV_SINCE" +%s 2>/dev/null || echo 0)
    NOW_EPOCH=$(date -u +%s)
    DURATION_S=$((NOW_EPOCH - PREV_EPOCH))
    DURATION_H=$((DURATION_S / 3600))
    DURATION_M=$(( (DURATION_S % 3600) / 60 ))
    if [ "$DURATION_H" -gt 0 ]; then
        DURATION_STR="${DURATION_H}h ${DURATION_M}m"
    else
        DURATION_STR="${DURATION_M}m"
    fi
    if [ -n "${ALERT_BOT_TOKEN:-}" ] && [ -n "${ALERT_CHAT_ID:-}" ]; then
        curl -s -X POST "https://api.telegram.org/bot${ALERT_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${ALERT_CHAT_ID}" \
            --data-urlencode "text=✅ mac bridge: RECOVERED (was down ${DURATION_STR}, since ${PREV_SINCE})" \
            > /dev/null 2>&1 || echo "[$TS] recovery send failed" >> "$LOG"
    fi
fi

# Persist state
cat > "$STATE_FILE" <<EOF
PREV_STATE=$CUR_STATE
PREV_SINCE=$NEW_SINCE
EOF
