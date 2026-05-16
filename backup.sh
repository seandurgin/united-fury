#!/bin/bash
# Auto-backup of Clawdia code to GitHub.
# Stages all Python and shell scripts (respecting .gitignore), commits, pushes.
# Exits non-zero on failure so callers can detect problems.
#
# Files committed: *.py, *.sh, *.md, .gitignore, requirements.txt
# Files NEVER committed: see .gitignore (databases, tokens, env files, .bak files)

set -euo pipefail
cd /opt/clawdia

# Sysmon alert on any failure (Tier 3 backlog 2026-05-16)
# shellcheck source=/opt/clawdia/scripts/alert.sh
. /opt/clawdia/scripts/alert.sh
trap 'notify "Clawdia backup FAILED" "backup.sh exited non-zero at line $LINENO. Check /var/log/clawdia-backup.log on $(hostname -s)."' ERR

# Export memory to readable text for human-readable diffs over time.
# Only categories/keys/timestamps — values redacted to avoid leaking
# personal data into git history.
sqlite3 /var/lib/clawdia/memory.db \
    "SELECT category, key, length(value) AS value_chars, updated FROM memory ORDER BY category, key" \
    > memory_export.txt

# Stage everything matching our patterns; .gitignore will exclude what should not commit.
git add -A "*.py" "*.sh" "*.md" ".gitignore" 2>/dev/null || true
git add memory_export.txt 2>/dev/null || true

# Commit only if there are real changes
if git diff --cached --quiet; then
    echo "Backup: no code changes to commit at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
else
    git commit -m "Auto-backup $(date -u +%Y-%m-%d\ %H:%M\ UTC)"
fi

# Push to master. If push fails, surface the error and exit non-zero.
if ! git push origin master 2>&1; then
    echo "Backup: PUSH FAILED. Local commits are safe but not on GitHub." >&2
    exit 1
fi

echo "Backup complete: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
