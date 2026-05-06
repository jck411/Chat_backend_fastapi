# Echo Kiosk Initial Setup

One-time device configuration for Echo Show kiosks: boot settings, bloatware removal, network mode, and TTS setup.

**For memory optimization after setup**, see [ECHO_DEVICE_SETUP.md](ECHO_DEVICE_SETUP.md).

## Prereqs

- Rooted Echo device (LineageOS-based)
- ADB access enabled (USB for initial setup)
- Fully Kiosk Browser installed (`de.ozerov.fully`)
- Backend running on server (LXC 111 at `192.168.1.111:8000`)
- Kiosk UI built and deployed to `src/backend/static/kiosk/`:
  ```bash
  scripts/echo/build_kiosk.sh build
  ```

## Network Modes

| Mode | URL | Connection | Use Case |
|------|-----|------------|----------|
| **Production** (default) | `https://192.168.1.111:8000/kiosk/` | Direct LAN, no USB needed | Normal operation |
| **Development** (`--dev`) | `https://localhost:5174` | ADB reverse port forwarding | Local dev/testing |

Production mode enables ADB-over-WiFi so the USB cable can be removed after setup.

## One-time device setup (per Echo)

Run the setup script for each device. If multiple devices are connected, pass the serial.

```bash
adb devices

# Production mode (default) — connects to server on LAN
scripts/echo/setup_echo.sh [SERIAL]

# Development mode — uses ADB reverse port forwarding
scripts/echo/setup_echo.sh --dev [SERIAL]
```

What the script applies:

- Max brightness, lock screen disabled, screen timeout disabled
- Background/update services disabled, animations disabled
- IDME bootmode set to 0 (prevents recovery boot flag)
- Recovery boot flags cleared, install-recovery.sh disabled
- **TWRP auto-reboot guard installed** (handles power loss — see below)
- Bloatware packages disabled
- Fully Kiosk permissions granted
- Fully Kiosk set as HOME activity (best effort)
- **Production**: ADB-over-WiFi enabled, Fully Kiosk launched with LAN URL
- **Dev**: ADB reverse port forwarding, Fully Kiosk launched with localhost URL

## Boot and power loss recovery

### The problem

The Echo Show 5 uses a MediaTek MT8163 SoC with an amonet-unlocked bootloader (LK). The amonet LK payload's `recovery_keys()` function only overrides `g_boot_mode` in fastboot mode — during normal boot (power-only, no USB), the bootloader always routes to TWRP recovery. This is hardcoded in the payload and cannot be fixed without modifying and reflashing the LK payload itself.

**CRITICAL: Never use `dd` to flash boot or recovery partitions.** The amonet exploit payload lives in the first 0x400 bytes (microloader) of these partitions. Overwriting them bricks the device.

### Two-part solution

#### 1. IDME bootmode fix (necessary but not sufficient)

Amazon stores configuration in IDME on the eMMC boot1 partition (`/dev/block/mmcblk0boot1`). The LK bootloader reads `bootmode` from IDME:
- `bootmode=0` → normal boot intent
- `bootmode=1` → recovery boot intent

The setup script patches this from 1→0. This is a **one-time fix** (persists across reboots and factory resets), but alone it does not prevent TWRP boot on power loss because the LK payload overrides the routing anyway.

#### 2. TWRP auto-reboot guard (the actual fix)

The setup script installs an Android init service that ensures the device auto-recovers from TWRP after power loss:

**Files installed on device:**
- `/system/bin/twrp-guard.sh` — Waits 30s after boot, then writes `echo "reboot" > /cache/recovery/openrecoveryscript`
- `/system/etc/init/twrp-guard.rc` — Init service definition (class `late_start`, `oneshot`, runs as root)
- `/cache/recovery/openrecoveryscript` — Contains "reboot" (seeded during setup)

**The cycle:**
1. Power loss occurs
2. Device boots → LK payload routes to TWRP recovery
3. TWRP reads `openrecoveryscript`, finds "reboot" command
4. TWRP executes reboot to system (and deletes the script file)
5. Android boots, init runs `twrp-guard` service after 30s
6. Guard script recreates `openrecoveryscript` with "reboot"
7. Device is ready for the next power loss

No infinite loop because TWRP deletes the script after processing it.

### Manual IDME bootmode fix

If the device is stuck in TWRP and the setup script hasn't been run:

```bash
# From TWRP or the OS, get root ADB
adb root && adb wait-for-device

# Check current bootmode
adb shell cat /proc/idme/bootmode   # "1" = broken

# Pull, patch, and write back boot1
adb shell 'dd if=/dev/block/mmcblk0boot1 of=/data/local/tmp/boot1.img bs=4096'
adb pull /data/local/tmp/boot1.img /tmp/boot1.img
OFFSET=$(($(python3 -c "print(open('/tmp/boot1.img','rb').read().find(b'bootmode')+28)")))
printf '\x30' | dd of=/tmp/boot1.img bs=1 seek=$OFFSET conv=notrunc
adb push /tmp/boot1.img /data/local/tmp/boot1_patched.img
adb shell 'echo 0 > /sys/block/mmcblk0boot1/force_ro'
adb shell 'dd if=/data/local/tmp/boot1_patched.img of=/dev/block/mmcblk0boot1 bs=4096 && sync'

# Then run setup_echo.sh to install the TWRP guard
```

## Reboots and reconnection

### Production mode

After a reboot or power loss, the TWRP guard handles recovery automatically. Fully Kiosk will auto-launch and connect to the server on LAN — no intervention needed as long as:
- Fully Kiosk is set as the default launcher/HOME
- WiFi reconnects automatically
- The backend is running at `192.168.1.111:8000`

To reconnect ADB after USB is removed:
```bash
adb connect <device-wifi-ip>:5555
```

### Development mode

ADB reverse port forwarding does not persist across reboots.

After reboot:
```bash
adb -s SERIAL reverse tcp:5174 tcp:5174
adb -s SERIAL reverse tcp:8000 tcp:8000
```

Or re-run the full setup script:
```bash
scripts/echo/setup_echo.sh --dev SERIAL
```

## Kiosk TTS segmentation settings

Settings live in `src/backend/data/clients/kiosk/tts.json` and are editable in the kiosk settings modal:

- **Minimum first phrase length** (`first_phrase_min_chars`): floor before the first phrase can emit
- **Segmentation logging** (`segmentation_logging_enabled`): logs when the minimum is met and the segmenter is waiting for a delimiter
