# Echo Kiosk Device Setup Guide

Complete guide for setting up an Echo Show device as a memory-optimized kiosk running Fully Kiosk Browser with LineageOS.

**Scope**: This document focuses on **memory optimization** and performance tuning. For initial device setup, boot configuration, and TTS settings, see [ECHO_KIOSK_SETUP.md](ECHO_KIOSK_SETUP.md).

## Prerequisites

- Echo Show device (tested on Echo Show 5)
- LineageOS installed (rooted)
- Fully Kiosk Browser installed
- USB debugging enabled
- ADB access from host machine

## Manual Setup Steps

### 1. Enable Developer Options

1. Go to **Settings → About Phone**
2. Tap **Build number** 7 times
3. Go back to **Settings → System → Developer options**

### 2. Configure Developer Options

| Setting | Value | Reason |
|---------|-------|--------|
| **USB debugging** | ON | Required for ADB access |
| **Rooted debugging** | ON | Required for kernel tuning |
| **Window animation scale** | 0.0x | Reduces GPU memory & CPU |
| **Transition animation scale** | 0.0x | Faster UI transitions |
| **Animator duration scale** | 0.0x | No animated elements |
| **Background process limit** | At most 2 | Prevents memory bloat |
| **Don't keep activities** | ON | Aggressive memory reclaim |
| **Force 4x MSAA** | OFF | Reduces GPU memory |

### 3. Disable Unnecessary Packages

These packages are not needed for kiosk operation and consume memory:

```bash
# Audio equalizer (~70MB)
adb shell pm disable-user org.lineageos.audiofx

# Calendar, contacts, phone
adb shell pm disable-user com.android.providers.calendar
adb shell pm disable-user com.android.contacts
adb shell pm disable-user com.android.phone

# Media apps
adb shell pm disable-user com.android.camera2
adb shell pm disable-user com.android.gallery3d
adb shell pm disable-user org.lineageos.eleven
adb shell pm disable-user org.lineageos.recorder

# Browser & launcher (using Fully Kiosk)
adb shell pm disable-user org.lineageos.jelly
adb shell pm disable-user com.android.launcher3

# Other utilities
adb shell pm disable-user com.android.bluetooth
adb shell pm disable-user com.android.mms.service
adb shell pm disable-user com.android.calculator2
adb shell pm disable-user com.android.deskclock
adb shell pm disable-user org.lineageos.etar
```

To re-enable a package:
```bash
adb shell pm enable org.lineageos.audiofx
```

### 4. Kernel Memory Parameters

These require root access and reset on reboot:

```bash
# Restart ADB as root
adb root
adb wait-for-device

# Reduce swap aggressiveness (default 60)
adb shell "echo 40 > /proc/sys/vm/swappiness"

# Increase cache reclaim pressure (default 100)
adb shell "echo 150 > /proc/sys/vm/vfs_cache_pressure"

# Lower dirty ratio for faster writeback
adb shell "echo 10 > /proc/sys/vm/dirty_ratio"
```

**Explanation:**
- **swappiness=40**: Less aggressive swapping to zRAM, keeps apps in RAM longer
- **vfs_cache_pressure=150**: Reclaim filesystem cache more aggressively
- **dirty_ratio=10**: Write dirty pages to disk sooner, freeing memory

### 5. GPU Rendering Settings

```bash
# Use Skia OpenGL renderer (more efficient)
adb shell setprop debug.hwui.renderer skiagl

# Disable GPU profiling (no debug overhead)
adb shell setprop debug.hwui.profile false
```

### 6. ADB Over WiFi (Production)

In production mode, ADB-over-WiFi is enabled during setup so the USB cable can be removed. The device connects directly to the backend on the LAN.

To reconnect ADB after USB removal:
```bash
adb connect <device-wifi-ip>:5555
```

### ADB Reverse Port Forwarding (Dev Only)

**Only needed for development mode** (`--dev`). ADB reverse port forwarding does NOT persist across reboots.

After each reboot (dev mode only):
```bash
adb reverse tcp:5174 tcp:5174  # Kiosk frontend (dev server)
adb reverse tcp:8000 tcp:8000  # Backend API
```

Or re-run the full setup script:
```bash
scripts/echo/setup_echo.sh --dev SERIAL
```

## Fully Kiosk Browser Settings

Access via: `http://<device-ip>:2323` (password from `FULLY_KIOSK_PASSWORD` in `.env`)

### Web Content Settings

| Setting | Value | Reason |
|---------|-------|--------|
| **Start URL** | `https://192.168.1.111:8000/kiosk/` | Kiosk frontend served by backend on LAN |
| **Ignore SSL Errors** | ON | Self-signed certificate on LAN |
| **Enable JavaScript** | ON | Required for app |
| **Enable Web SQL** | OFF | Not needed |
| **Enable App Cache** | OFF | Manual caching |
| **Clear Cache on Start** | ON | Prevent memory bloat |
| **JavaScript Alerts** | OFF | No popups |
| **Geolocation Access** | OFF | Not needed |

### Device Management

| Setting | Value | Reason |
|---------|-------|--------|
| **Keep Screen On** | ON | Kiosk mode |
| **Screen Brightness** | 60-80% | Power/heat balance |
| **Launch on Boot** | ON | Auto-start |
| **Prevent Sleep** | ON | Always visible |

### Remote Admin

| Setting | Value |
|---------|-------|
| **Enable Remote Admin** | ON |
| **Remote Admin Password** | Value of `FULLY_KIOSK_PASSWORD` in `.env` |
| **Remote Admin Port** | 2323 |

### Useful Remote Commands

Clear WebView cache:
```
http://<device-ip>:2323/?cmd=clearCache&password=$FULLY_KIOSK_PASSWORD
```

Reload page:
```
http://<device-ip>:2323/?cmd=loadStartUrl&password=$FULLY_KIOSK_PASSWORD
```

## Slideshow Configuration

Slideshow photos are served from Immich (192.168.1.113:2283) via the backend proxy.
The backend fetches the latest landscape photos daily — no sync script or cron needed.

Photo count is configured in kiosk UI settings:
```bash
cat src/backend/data/clients/kiosk/ui.json
# {"idle_return_delay_ms": 10000, "slideshow_max_photos": 30}
```

**Memory impact:** ~0.8 MB per photo when decoded. 30 photos ≈ 24 MB decoded bitmap memory.

## Memory Monitoring

### Quick Check

```bash
./scripts/echo/check_echo_memory.sh
```

### Continuous Monitoring

```bash
./scripts/echo/monitor_echo_memory.sh
```

### Manual Memory Check

```bash
adb shell "cat /proc/meminfo | head -6"
adb shell "ps -A -o RSS,NAME --sort=-rss | head -15"
```

## Expected Memory Usage

After optimization on Echo Show 5 (974 MB usable RAM):

| Metric | Target | Notes |
|--------|--------|-------|
| **MemFree** | 70-100 MB | Raw free memory |
| **MemAvailable** | 250-350 MB | Including reclaimable |
| **Swap Used** | < 100 MB | zRAM compressed |
| **Fully Kiosk** | 150-200 MB | WebView main process |
| **System UI** | 150-170 MB | Android overhead |

## Troubleshooting

### Low Memory / Sluggish Performance

1. Check current memory:
   ```bash
   adb shell cat /proc/meminfo | head -6
   ```

2. Clear WebView cache:
   ```bash
   curl "http://<device-ip>:2323/?cmd=clearCache&password=$FULLY_KIOSK_PASSWORD"
   ```

3. Restart Fully Kiosk:
   ```bash
   adb shell am force-stop de.ozerov.fully
   adb shell am start -n de.ozerov.fully/.MainActivity
   ```

4. Reboot the device:
   ```bash
   adb reboot
   ```

### WebView Crashes

If WebView keeps crashing, try:
1. Reduce `slideshow_max_photos` to 20 or less
2. Ensure "Don't keep activities" is ON
3. Check if swap is full: `adb shell free -m`

### ADB Root Access Denied

1. Go to **Settings → System → Developer options**
2. Find **Root access** or **Rooted debugging**
3. Set to "ADB only" or "Apps and ADB"
4. Run `adb root` again

### Device Boots Into TWRP Recovery

See the [boot and power loss recovery](ECHO_KIOSK_SETUP.md#boot-and-power-loss-recovery) section in ECHO_KIOSK_SETUP.md. The setup script fixes this automatically; re-run it if needed.

### Package Won't Disable

Some system packages can't be disabled without system partition modification:
```bash
# Check if package is system
adb shell pm path <package>
# If path starts with /system/, it's a system app
```

## References

- [Fully Kiosk Remote Admin API](https://www.fully-kiosk.com/en/#rest-api)
- [LineageOS Developer Options](https://wiki.lineageos.org/)
- [Android Memory Management](https://developer.android.com/topic/performance/memory-overview)
