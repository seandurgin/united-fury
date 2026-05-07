#!/bin/bash
# Run a shell script with execution tracing (bash -x) but redact env-var
# values so secrets sourced from env files do not appear in trace output.
#
# Usage: safe_trace.sh <script_to_trace> [args...]
#
# CRITICAL: do NOT use set -e here, or non-zero exits from the traced script
# will short-circuit the redaction step and leak secrets via failed traces.

if [ $# -lt 1 ]; then
    echo "Usage: $0 <script_to_trace> [args...]" >&2
    exit 1
fi

SCRIPT="$1"; shift
TRACE_LOG=$(mktemp -t safetrace.XXXXXX)
trap 'rm -f "$TRACE_LOG"' EXIT

# Run the target script with tracing into the temp log.
# Capture exit code without set -e tripping.
bash -x "$SCRIPT" "$@" 2> "$TRACE_LOG"
RC=$?

# Redact secret-looking patterns from the trace before emitting.
# Patterns:
#   1. Variable assignments where the name contains TOKEN/KEY/SECRET/PASSWORD/CLIENT_ID
#   2. Inline secret prefixes (sk-, ghp_, Bearer, Anthropic api03)
sed -E '
    s/([A-Za-z_][A-Za-z0-9_]*(TOKEN|KEY|SECRET|PASSWORD|CLIENT_ID|CLIENT_SECRET)[A-Za-z0-9_]*=)\S+/\1[REDACTED]/g
    s/(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+/\1[REDACTED]/g
    s/(sk-ant-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+/\1[REDACTED]/g
    s/(ghp_[A-Za-z0-9]{4})[A-Za-z0-9]+/\1[REDACTED]/g
    s/(Bearer +)[A-Za-z0-9._-]+/\1[REDACTED]/g
' "$TRACE_LOG" >&2

exit $RC
