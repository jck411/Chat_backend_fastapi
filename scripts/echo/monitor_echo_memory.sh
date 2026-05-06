#!/bin/bash

echo "⏱️  Continuous Memory Monitoring for Echo Device"
echo "=============================================="
echo "This will monitor memory usage every 10 seconds"
echo "Press Ctrl+C to stop monitoring"
echo ""

if ! adb devices | grep -q "device$"; then
    echo "❌ No ADB devices connected. Please connect your Echo device first."
    exit 1
fi

# Get initial timestamp
echo "🕐 Starting monitoring at $(date '+%H:%M:%S')"
echo ""

counter=1
while true; do
    timestamp=$(date '+%H:%M:%S')

    # Get memory info
    free_mem=$(adb shell cat /proc/meminfo | grep MemFree | awk '{print $2}')
    free_mb=$((free_mem / 1024))

    # Get WebView memory
    webview_rss=$(adb shell "ps -eo rss,comm | grep -E '(webview|chromium|chrome|browser|kiosk)' | awk '{sum += \$1} END {print sum}'" 2>/dev/null)
    if [[ ! -z "$webview_rss" && "$webview_rss" != "0" ]]; then
        webview_mb=$((webview_rss / 1024))
    else
        webview_mb="N/A"
    fi

    # Status indicator
    if [[ $free_mb -lt 100 ]]; then
        status="🔴 LOW"
    elif [[ $free_mb -lt 200 ]]; then
        status="🟡 CAUTION"
    else
        status="🟢 OK"
    fi

    echo "[$timestamp] #$counter | Free: ${free_mb}MB | WebView: ${webview_mb}MB | Status: $status"

    counter=$((counter + 1))
    sleep 10
done
