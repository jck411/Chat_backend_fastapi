# Echo Show Scripts

Scripts for managing Echo Show devices running LineageOS with Fully Kiosk Browser.

## Scripts

### `setup_echo.sh`
One-time device setup: brightness, bloatware removal, IDME bootmode fix, TWRP auto-reboot guard, permissions, and Fully Kiosk launch.

```bash
scripts/echo/setup_echo.sh [--dev] [SERIAL]
```

See [ECHO_KIOSK_SETUP.md](../../docs/echo/ECHO_KIOSK_SETUP.md).

### `build_kiosk.sh`
Build or serve the kiosk frontend.

```bash
scripts/echo/build_kiosk.sh build   # Production build → src/backend/static/
scripts/echo/build_kiosk.sh serve   # Dev server on https://0.0.0.0:5174
```

### `check_echo_memory.sh`
Quick memory snapshot showing free/available/swap and top processes.

```bash
scripts/echo/check_echo_memory.sh
```

### `monitor_echo_memory.sh`
Continuous real-time memory monitoring (refreshes every 10 seconds).

```bash
scripts/echo/monitor_echo_memory.sh
```

## Documentation

- [ECHO_KIOSK_SETUP.md](../../docs/echo/ECHO_KIOSK_SETUP.md) — Initial device configuration
- [ECHO_DEVICE_SETUP.md](../../docs/echo/ECHO_DEVICE_SETUP.md) — Memory optimization guide
