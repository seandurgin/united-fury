#!/bin/bash
# Sourceable Telegram alert helper for system monitoring.
# Usage: source /opt/clawdia/scripts/alert.sh; notify "Subject" "Body text"
#
# Reads ALERT_BOT_TOKEN and ALERT_CHAT_ID from /etc/clawdia/env.
# Silently no-ops if either is missing (so dev/test environments do not error).
# Designed for the separate clawdia_sysmon_bot, NOT the main Clawdia bot.

# Source env if not already loaded
if [ -z "${ALERT_BOT_TOKEN:-}" ] && [ -r /etc/clawdia/env ]; then
    set -a
    . /etc/clawdia/env
    set +a
fi

notify() {
    local subject="${1:-Alert}"
    local body="${2:-No body provided}"
    local host
    host=$(hostname -s 2>/dev/null || echo "unknown-host")
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    if [ -z "${ALERT_BOT_TOKEN:-}" ] || [ -z "${ALERT_CHAT_ID:-}" ]; then
        # Alert system not configured. Log to stderr and return non-fatal.
        echo "[$ts] ALERT NOT SENT (no bot configured): $subject - $body" >&2
        return 0
    fi

    # Build a markdown-friendly message. Subject in bold, body below.
    local msg="🚨 *${subject}*
\`${host}\` @ ${ts}

${body}"

    # POST to Telegram. Suppress output unless debug.
    curl -s -X POST "https://api.telegram.org/bot${ALERT_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${ALERT_CHAT_ID}" \
        --data-urlencode "text=${msg}" \
        -d "parse_mode=Markdown" \
        > /dev/null 2>&1 || \
        echo "[$ts] ALERT SEND FAILED: $subject" >&2
}
