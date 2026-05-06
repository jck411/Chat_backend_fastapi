#!/bin/bash

echo "📊 Echo Device Memory Assessment - Preloaded Photos"
echo "=================================================="
echo ""

# Check if ADB is connected
if ! adb devices | grep -q "device$"; then
    echo "❌ No ADB devices connected. Please connect your Echo device first."
    exit 1
fi

echo "🔍 Current Memory Status:"
echo ""

# Overall memory usage
echo "📈 System Memory Overview:"
adb shell cat /proc/meminfo | grep -E "(MemTotal|MemFree|MemAvailable|Cached)" | while read line; do
    echo "   $line"
done

echo ""

# WebView processes
echo "🌐 WebView Process Memory (Fully Kiosk Browser):"
adb shell "ps -eo pid,comm,vsz,rss | grep -E '(webview|chromium|chrome|browser)'" | while read line; do
    if [[ ! -z "$line" ]]; then
        pid=$(echo $line | awk '{print $1}')
        name=$(echo $line | awk '{print $2}')
        vsz=$(echo $line | awk '{print $3}')
        rss=$(echo $line | awk '{print $4}')

        # Convert KB to MB
        vsz_mb=$((vsz / 1024))
        rss_mb=$((rss / 1024))

        echo "   PID: $pid | $name | VSZ: ${vsz_mb}MB | RSS: ${rss_mb}MB"
    fi
done

echo ""

# Fully Kiosk Browser specific
echo "📱 Fully Kiosk Browser Processes:"
adb shell "ps -eo pid,comm,vsz,rss | grep -i kiosk" | while read line; do
    if [[ ! -z "$line" ]]; then
        pid=$(echo $line | awk '{print $1}')
        name=$(echo $line | awk '{print $2}')
        vsz=$(echo $line | awk '{print $3}')
        rss=$(echo $line | awk '{print $4}')

        # Convert KB to MB
        vsz_mb=$((vsz / 1024))
        rss_mb=$((rss / 1024))

        echo "   PID: $pid | $name | VSZ: ${vsz_mb}MB | RSS: ${rss_mb}MB"
    fi
done

echo ""

# Calculate total WebView memory (including sandboxed process and Fully Kiosk)
echo "📊 Memory Analysis:"

# Get Fully Kiosk main process
fully_main=$(adb shell "ps -eo rss,comm | grep 'de.ozerov.fully' | grep -v foreground | awk '{print \$1}'" 2>/dev/null | tr -d ' \r')
fully_main=${fully_main:-0}

# Get Fully Kiosk foreground process
fully_fg=$(adb shell "ps -eo rss,comm | grep 'ully:foreground' | awk '{print \$1}'" 2>/dev/null | tr -d ' \r')
fully_fg=${fully_fg:-0}

# Get sandboxed WebView renderer (where actual page runs)
sandboxed=$(adb shell "ps -eo rss,comm | grep 'ocessService' | awk '{print \$1}'" 2>/dev/null | tr -d ' \r')
sandboxed=${sandboxed:-0}

# Get webview zygote and service
zygote=$(adb shell "ps -eo rss,comm | grep 'webview_zygote' | awk '{print \$1}'" 2>/dev/null | tr -d ' \r')
zygote=${zygote:-0}
service=$(adb shell "ps -eo rss,comm | grep 'webview_service' | awk '{print \$1}'" 2>/dev/null | tr -d ' \r')
service=${service:-0}

# Calculate totals
total_webview_rss=$((fully_main + fully_fg + sandboxed + zygote + service))
if [[ "$total_webview_rss" -gt 0 ]]; then
    total_mb=$((total_webview_rss / 1024))
    fully_mb=$(((fully_main + fully_fg) / 1024))
    renderer_mb=$((sandboxed / 1024))
    base_mb=$(((zygote + service) / 1024))
    echo "   📱 Fully Kiosk: ${fully_mb}MB (app + foreground)"
    echo "   🖼️  Renderer: ${renderer_mb}MB (sandboxed process)"
    echo "   🔧 WebView Base: ${base_mb}MB (zygote + service)"
    echo "   🔢 Total Browser Memory: ${total_mb}MB"
else
    echo "   ⚠️  Could not calculate total WebView memory"
fi

# Memory pressure check
free_mem=$(adb shell cat /proc/meminfo | grep MemFree | awk '{print $2}')
if [[ ! -z "$free_mem" ]]; then
    free_mb=$((free_mem / 1024))
    echo "   💾 Free Memory: ${free_mb}MB"

    if [[ $free_mb -lt 100 ]]; then
        echo "   ⚠️  WARNING: Low memory (<100MB free)"
    elif [[ $free_mb -lt 200 ]]; then
        echo "   ⚡ CAUTION: Memory getting low (<200MB free)"
    else
        echo "   ✅ Memory looks healthy (${free_mb}MB+ free)"
    fi
fi

echo ""
echo "📝 Notes:"
echo "   • VSZ = Virtual Size (total virtual memory)"
echo "   • RSS = Resident Set Size (actual RAM usage)"
echo "   • Focus on RSS values for real memory impact"
echo ""
echo "🎯 Expected with Preloaded Photos:"
echo "   • ~40-50MB additional RSS for 50 photos"
echo "   • Stable memory usage (no fragmentation)"
echo "   • No periodic spikes during slideshow transitions"
echo ""
echo "Run this script before and after photo preloading to compare!"
