#!/bin/bash
# ae8-max weekly health check

ISSUES=""

# SSH in and run checks
RESULT=$(ssh -i /root/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o ConnectTimeout=10 seand@100.80.233.9 "
powershell -Command \"
\$disk_c = Get-PSDrive C | Select-Object -ExpandProperty Free
\$disk_c_gb = [math]::Round(\$disk_c / 1GB, 1)
\$disk_g = Get-PSDrive G -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Free
\$disk_g_gb = [math]::Round(\$disk_g / 1GB, 1)
\$ram = Get-CimInstance Win32_OperatingSystem
\$ram_free = [math]::Round(\$ram.FreePhysicalMemory / 1MB, 1)
\$ram_total = [math]::Round(\$ram.TotalVisibleMemorySize / 1MB, 1)
\$updates = (Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1).InstalledOn
Write-Output \"DISK_C:\$disk_c_gb\"
Write-Output \"DISK_G:\$disk_g_gb\"
Write-Output \"RAM_FREE:\$ram_free\"
Write-Output \"RAM_TOTAL:\$ram_total\"
Write-Output \"LAST_UPDATE:\$updates\"
\"
" 2>&1)

if [ $? -ne 0 ]; then
    echo "ae8-max health check FAILED - could not connect via SSH"
    echo "$RESULT"
    exit 1
fi

# Parse results
DISK_C=$(echo "$RESULT" | grep "DISK_C:" | cut -d: -f2)
DISK_G=$(echo "$RESULT" | grep "DISK_G:" | cut -d: -f2)
RAM_FREE=$(echo "$RESULT" | grep "RAM_FREE:" | cut -d: -f2)
RAM_TOTAL=$(echo "$RESULT" | grep "RAM_TOTAL:" | cut -d: -f2)
LAST_UPDATE=$(echo "$RESULT" | grep "LAST_UPDATE:" | cut -d: -f2)

# Check thresholds
if (( $(echo "$DISK_C < 50" | bc -l) )); then
    ISSUES="$ISSUES\n⚠️ C: drive low: ${DISK_C}GB free"
fi
if [ -n "$DISK_G" ] && (( $(echo "$DISK_G < 20" | bc -l) )); then
    ISSUES="$ISSUES\n⚠️ G: drive low: ${DISK_G}GB free"
fi

# Report
echo "ae8-max Weekly Health Check"
echo "============================="
echo "💾 C: drive: ${DISK_C}GB free"
echo "💾 G: drive: ${DISK_G}GB free"
echo "🧠 RAM: ${RAM_FREE}GB free / ${RAM_TOTAL}GB total"
echo "🔄 Last Windows Update: $LAST_UPDATE"
if [ -n "$ISSUES" ]; then
    echo -e "\nISSUES FOUND:$ISSUES"
    exit 2
else
    echo "✅ All checks passed"
fi
