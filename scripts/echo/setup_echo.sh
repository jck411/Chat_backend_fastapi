#!/bin/bash
# ============================================
# Echo Kiosk Device Setup Script
# ============================================
# Run this script after rooting an Amazon Echo
# to configure it for dedicated kiosk mode.
#
# Usage: ./setup_echo.sh [--dev] [DEVICE_SERIAL]
#
# Modes:
#   (default)  Production: connects to backend on LAN via WiFi
#   --dev      Development: uses ADB reverse port forwarding to localhost
#
# Production connects to https://SERVER_IP:8000/kiosk/
# Development connects to https://localhost:5174 (via ADB reverse)
#
# This script:
#   - Sets display to max brightness
#   - Disables lock screen and screen timeout
#   - Disables bloatware and unnecessary apps
#   - Disables system updates
#   - Fixes IDME bootmode and recovery flags
#   - Installs TWRP auto-reboot guard (power loss recovery)
#   - Enables ADB over WiFi (production)
#   - Launches Fully Kiosk
# ============================================

set -e

# --- Parse arguments ---
MODE="production"
DEVICE_SERIAL=""
SERVER_IP="192.168.1.111"

for arg in "$@"; do
    case "$arg" in
        --dev) MODE="dev" ;;
        --help|-h)
            echo "Usage: $0 [--dev] [DEVICE_SERIAL]"
            echo ""
            echo "  --dev    Development mode (ADB reverse port forwarding to localhost)"
            echo "  default  Production mode (direct LAN connection to $SERVER_IP:8000)"
            exit 0
            ;;
        *) DEVICE_SERIAL="$arg" ;;
    esac
done

ADB_CMD="adb"
if [ -n "$DEVICE_SERIAL" ]; then
    ADB_CMD="adb -s $DEVICE_SERIAL"
fi

echo "============================================"
echo "Echo Kiosk Setup Script ($MODE mode)"
echo "============================================"

# Check device connection
echo ""
echo "Checking device connection..."
if ! $ADB_CMD get-state &>/dev/null; then
    echo "❌ No device connected. Please connect via USB and enable ADB."
    exit 1
fi

SERIAL=$($ADB_CMD get-serialno)
echo "✅ Connected to device: $SERIAL"

# ============================================
# DISPLAY SETTINGS
# ============================================
echo ""
echo "[1/12] Setting display brightness to 100%..."
$ADB_CMD shell settings put system screen_brightness 255
$ADB_CMD shell settings put system screen_brightness_mode 0  # Disable auto-brightness
echo "✅ Brightness set to maximum (255)"

# ============================================
# DISABLE LOCK SCREEN
# ============================================
echo ""
echo "[2/12] Disabling lock screen..."
$ADB_CMD shell settings put secure lockscreen.disabled 1
$ADB_CMD shell settings put global device_provisioned 1
echo "✅ Lock screen disabled"

# ============================================
# KEEP SCREEN ON FOREVER
# ============================================
echo ""
echo "[3/12] Disabling screen timeout..."
# Stay awake while charging (USB + AC + Wireless = 7)
$ADB_CMD shell settings put global stay_on_while_plugged_in 7
# Set screen timeout to max (won't matter if stay_on is set, but just in case)
$ADB_CMD shell settings put system screen_off_timeout 2147483647
echo "✅ Screen will stay on indefinitely"

# ============================================
# DISABLE UPDATES & BACKGROUND SERVICES
# ============================================
echo ""
echo "[4/12] Disabling updates and background services..."

# Disable package verifier (speeds up installs, no Google checks)
$ADB_CMD shell settings put global package_verifier_enable 0
$ADB_CMD shell settings put global verifier_verify_adb_installs 0

# Disable usage stats collection
$ADB_CMD shell settings put global netstats_enabled 0

# Disable always-on WiFi scanning
$ADB_CMD shell settings put global wifi_scan_always_enabled 0

# Disable animations (saves GPU memory and CPU)
$ADB_CMD shell settings put global window_animation_scale 0
$ADB_CMD shell settings put global transition_animation_scale 0
$ADB_CMD shell settings put global animator_duration_scale 0

echo "✅ Background services and updates disabled"

# ============================================
# CLEAR RECOVERY BOOT FLAGS
# ============================================
echo ""
echo "[5/12] Clearing recovery boot flags (prevents update/recovery loops)..."
$ADB_CMD shell 'if [ -d /cache/recovery ]; then rm -f /cache/recovery/command /cache/recovery/last_log /cache/recovery/last_install; fi' || true
$ADB_CMD shell 'if [ -d /data/cache/recovery ]; then rm -f /data/cache/recovery/command /data/cache/recovery/last_log /data/cache/recovery/last_install; fi' || true

# Disable vendor recovery reflash script (requires root + rw remount)
$ADB_CMD shell 'mount -o rw,remount / 2>/dev/null && if [ -f /vendor/bin/install-recovery.sh ]; then mv /vendor/bin/install-recovery.sh /vendor/bin/install-recovery.sh.disabled; fi' || true
$ADB_CMD shell 'if [ -f /system/etc/install-recovery.sh ]; then mount -o rw,remount / 2>/dev/null && mv /system/etc/install-recovery.sh /system/etc/install-recovery.sh.disabled; fi' || true

# Disable recovery_update property (prevents bootloader from preferring recovery)
$ADB_CMD shell setprop persist.vendor.recovery_update false
$ADB_CMD shell setprop persist.sys.recovery_update false

# Fix IDME bootmode (the actual root cause of recovery boot loops)
# Amazon stores boot flags in IDME on eMMC boot1 partition (mmcblk0boot1).
# bootmode=1 tells the LK bootloader to boot into TWRP recovery.
# bootmode=0 tells it to boot normally into the OS.
# The value is at a fixed offset in the IDME data structure.
echo "  Fixing IDME bootmode (boot1 partition)..."
IDME_BOOTMODE=$($ADB_CMD shell 'cat /proc/idme/bootmode 2>/dev/null' | tr -d '\r\n')
if [ "$IDME_BOOTMODE" = "1" ]; then
    # Find bootmode offset in boot1, patch it from '1' (0x31) to '0' (0x30)
    $ADB_CMD shell 'dd if=/dev/block/mmcblk0boot1 of=/data/local/tmp/boot1.img bs=4096 2>/dev/null' || true
    $ADB_CMD pull /data/local/tmp/boot1.img /tmp/echo_boot1.img >/dev/null 2>&1 || true
    # Find the bootmode value offset: search for "bootmode" string, value is 28 bytes after field name start
    BOOT_MODE_OFFSET=$(python3 -c "
import sys
data = open('/tmp/echo_boot1.img', 'rb').read()
idx = data.find(b'bootmode')
if idx == -1:
    sys.exit(1)
# IDME field: 16B name + 4B size + 4B count + 4B flags = 28B to value
val_offset = idx + 28
if data[val_offset:val_offset+1] == b'1':
    print(val_offset)
else:
    sys.exit(1)
" 2>/dev/null)
    if [ -n "$BOOT_MODE_OFFSET" ]; then
        printf '\x30' | dd of=/tmp/echo_boot1.img bs=1 seek="$BOOT_MODE_OFFSET" conv=notrunc 2>/dev/null
        $ADB_CMD push /tmp/echo_boot1.img /data/local/tmp/boot1_patched.img >/dev/null 2>&1
        $ADB_CMD shell 'echo 0 > /sys/block/mmcblk0boot1/force_ro && dd if=/data/local/tmp/boot1_patched.img of=/dev/block/mmcblk0boot1 bs=4096 2>/dev/null && sync'
        echo "  ✅ IDME bootmode changed from 1 → 0"
    else
        echo "  ⚠️  Could not locate bootmode offset in boot1 — manual fix may be needed"
    fi
    rm -f /tmp/echo_boot1.img
else
    echo "  ✅ IDME bootmode already 0 (normal boot)"
fi

echo "✅ Recovery flags cleared and recovery update disabled"

# ============================================
# INSTALL TWRP AUTO-REBOOT GUARD
# ============================================
# The amonet LK payload always routes to TWRP recovery on power-only boot
# (no USB). This is hardcoded in the payload — IDME bootmode=0 alone does
# not fix it. The workaround: an Android init service that writes a
# "reboot" command to /cache/recovery/openrecoveryscript after each boot.
# When TWRP starts, it reads and executes the script (rebooting to system),
# then deletes it. The init service recreates it once the OS is up.
# Cycle: power loss → TWRP → reads "reboot" → system → init recreates script
echo ""
echo "[6/12] Installing TWRP auto-reboot guard..."
$ADB_CMD shell 'mount -o rw,remount / 2>/dev/null' || true

# Create the guard script
$ADB_CMD shell 'cat > /system/bin/twrp-guard.sh << "GUARD"
#!/system/bin/sh
sleep 30
echo "reboot" > /cache/recovery/openrecoveryscript
GUARD'
$ADB_CMD shell 'chmod 755 /system/bin/twrp-guard.sh'

# Create the init service
$ADB_CMD shell 'cat > /system/etc/init/twrp-guard.rc << "INITRC"
service twrp-guard /system/bin/twrp-guard.sh
    class late_start
    user root
    group root
    oneshot
    seclabel u:r:su:s0
INITRC'
$ADB_CMD shell 'chmod 644 /system/etc/init/twrp-guard.rc'

# Seed the initial openrecoveryscript
$ADB_CMD shell 'mkdir -p /cache/recovery && echo "reboot" > /cache/recovery/openrecoveryscript'

echo "✅ TWRP auto-reboot guard installed"

# ============================================
# DISABLE BLOATWARE APPS
# ============================================
echo ""
echo "[7/12] Disabling unnecessary apps..."

# Apps to disable - these waste RAM and CPU
BLOATWARE=(
    # LineageOS extras
    "org.lineageos.updater"          # System updater
    "org.lineageos.recorder"         # Screen recorder
    "org.lineageos.eleven"           # Music player
    "org.lineageos.etar"             # Calendar app
    "org.lineageos.jelly"            # Browser (we use Fully Kiosk)
    "org.lineageos.audiofx"          # Audio effects
    "org.lineageos.backgrounds"      # Wallpapers
    "org.lineageos.setupwizard"      # Setup wizard

    # Android apps we don't need
    "com.android.camera2"            # Camera
    "com.android.gallery3d"          # Gallery
    "com.android.calculator2"        # Calculator
    "com.android.deskclock"          # Clock/alarms
    "com.android.contacts"           # Contacts
    "com.android.calendar"           # Calendar
    "com.android.email"              # Email
    "com.android.launcher3"          # Default launcher
    "com.android.documentsui"        # File manager
    "com.android.dreams.basic"       # Screensaver
    "com.android.dreams.phototable"  # Photo screensaver
    "com.android.wallpaper.livepicker" # Live wallpaper
    "com.android.wallpapercropper"   # Wallpaper tools
    "com.android.printspooler"       # Printing
    "com.android.bips"               # Print service
    "com.android.printservice.recommendation" # Print recommendations
    "com.android.egg"                # Easter egg
    "com.android.traceur"            # System tracing
    "com.android.soundpicker"        # Sound picker
    "com.android.storagemanager"     # Storage manager
    "com.android.bookmarkprovider"   # Bookmarks

    # Backup services (not needed for kiosk)
    "com.stevesoltys.seedvault"      # Backup app
    "com.android.wallpaperbackup"    # Wallpaper backup
    "org.calyxos.backup.contacts"    # Contact backup

    # Communication we don't need
    "com.android.mms.service"        # MMS
    "com.android.smspush"            # SMS push
    "com.android.phone"              # Phone app
    "com.android.providers.telephony" # Telephony
    "com.android.server.telecom"     # Telecom
    "com.android.simappdialog"       # SIM dialog
    "com.android.companiondevicemanager" # Companion devices

    # System services not needed for kiosk
    "com.android.dynsystem"          # Dynamic System Updates
)

DISABLED_COUNT=0
for app in "${BLOATWARE[@]}"; do
    if $ADB_CMD shell pm disable-user --user 0 "$app" 2>/dev/null | grep -q "disabled"; then
        DISABLED_COUNT=$((DISABLED_COUNT + 1))
    fi
done
echo "✅ Disabled $DISABLED_COUNT bloatware apps"

# ============================================
# KILL BACKGROUND PROCESSES
# ============================================
echo ""
echo "[8/12] Killing unnecessary background processes..."

# Force stop apps that might be running
PROCESSES_TO_KILL=(
    "org.lineageos.updater"
    "com.android.launcher3"
    "com.android.systemui.plugin.globalactions.wallet"
    "org.lineageos.audiofx"
    "com.android.settings"
    "com.android.dynsystem"
)

for proc in "${PROCESSES_TO_KILL[@]}"; do
    $ADB_CMD shell am force-stop "$proc" 2>/dev/null || true
done
echo "✅ Background processes terminated"

# ============================================
# NETWORK SETUP (mode-dependent)
# ============================================
echo ""
if [ "$MODE" = "dev" ]; then
    echo "[9/12] Setting up ADB reverse port forwarding (dev mode)..."
    $ADB_CMD reverse tcp:5174 tcp:5174  # Kiosk UI (dev server)
    $ADB_CMD reverse tcp:8000 tcp:8000  # Backend API
    KIOSK_URL="https://localhost:5174"
    echo "✅ Port forwarding established:"
    echo "   - localhost:5174 → Kiosk UI (dev server)"
    echo "   - localhost:8000 → Backend API"
else
    echo "[9/12] Enabling ADB over WiFi (production mode)..."
    # Get device WiFi IP for reference
    DEVICE_IP=$($ADB_CMD shell ip addr show wlan0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1)
    # Remove any leftover reverse forwarding
    $ADB_CMD reverse --remove-all 2>/dev/null || true
    # Enable ADB over WiFi so USB can be disconnected
    $ADB_CMD tcpip 5555
    KIOSK_URL="https://${SERVER_IP}:8000/kiosk/"
    echo "✅ ADB WiFi enabled on port 5555"
    echo "   Device WiFi IP: ${DEVICE_IP:-unknown}"
    echo "   Reconnect after USB removal: adb connect ${DEVICE_IP:-<device-ip>}:5555"
    echo "   Kiosk URL: $KIOSK_URL"
fi

# ============================================
# GRANT FULLY KIOSK PERMISSIONS
# ============================================
echo ""
echo "[10/12] Granting Fully Kiosk permissions..."
$ADB_CMD shell pm grant de.ozerov.fully android.permission.RECORD_AUDIO
$ADB_CMD shell pm grant de.ozerov.fully android.permission.CAMERA 2>/dev/null || true
$ADB_CMD shell pm grant de.ozerov.fully android.permission.ACCESS_FINE_LOCATION 2>/dev/null || true
echo "✅ Microphone and other permissions granted"

# ============================================
# SET FULLY KIOSK AS HOME (WHEN SUPPORTED)
# ============================================
echo ""
echo "[11/12] Setting Fully Kiosk as HOME activity (best effort)..."
$ADB_CMD shell cmd package set-home-activity de.ozerov.fully/.MainActivity 2>/dev/null || true
$ADB_CMD shell cmd package set-home-activity --user 0 de.ozerov.fully/.MainActivity 2>/dev/null || true
echo "✅ HOME activity set (if supported)"

# ============================================
# LAUNCH FULLY KIOSK
# ============================================
echo ""
echo "[12/12] Launching Fully Kiosk Browser..."
$ADB_CMD shell am start -n de.ozerov.fully/.MainActivity \
    -a android.intent.action.VIEW \
    -d "$KIOSK_URL"
echo "✅ Fully Kiosk launched with $KIOSK_URL"

# ============================================
# SUMMARY
# ============================================
echo ""
echo "============================================"
echo "✅ Setup Complete!"
echo "============================================"
echo ""
echo "Device: $SERIAL"
echo "Mode: $MODE"
echo ""
echo "Settings applied:"
echo "  • Brightness: 100% (auto-brightness OFF)"
echo "  • Lock screen: Disabled"
echo "  • Screen timeout: Disabled (stays on forever)"
echo "  • System updates: Disabled"
echo "  • Recovery flags: Cleared"
echo "  • TWRP guard: Auto-reboot on power loss"
echo "  • Bloatware: $DISABLED_COUNT apps disabled"
echo "  • Animations: Disabled"
echo "  • Microphone: Permission granted"
echo "  • Home activity: Fully Kiosk (best effort)"
if [ "$MODE" = "dev" ]; then
    echo "  • ADB reverse: 5174, 8000"
    echo "  • Fully Kiosk: https://localhost:5174"
    echo ""
    echo "NOTE: Port forwarding resets on device reboot."
    echo "      Re-run this script after reboot."
else
    echo "  • ADB WiFi: port 5555 (USB can be removed)"
    echo "  • Fully Kiosk: https://${SERVER_IP}:8000/kiosk/"
    echo ""
    echo "NOTE: After removing USB, reconnect via:"
    echo "      adb connect ${DEVICE_IP:-<device-ip>}:5555"
fi
echo "============================================"
