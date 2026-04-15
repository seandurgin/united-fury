#!/bin/bash
cd /opt/clawdia

# Export memory to readable JSON
sqlite3 /var/lib/clawdia/memory.db "SELECT category, key, value, updated FROM memory ORDER BY category, key" > memory_export.txt

# Commit and push
git add bot.py briefing.py memory_export.txt
git commit -m "Auto-backup $(date '+%Y-%m-%d %H:%M UTC')" --allow-empty
git push origin clawdia
echo "Backup complete: $(date)"
