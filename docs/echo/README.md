# Echo Show Kiosk Documentation

Amazon Echo Show 5 devices (974 MB RAM, 960×480 display) running LineageOS as memory-optimized kiosks with Fully Kiosk Browser.

Kiosks connect directly to the backend on LAN (`https://192.168.1.111:8000/kiosk/`) — no Cloudflare or public internet required. A development mode with ADB reverse port forwarding is available for local testing.

## Credentials

Fully Kiosk Browser Remote Admin credentials are stored in `.env`:

| Variable | Description |
|----------|-------------|
| `ECHO_SHOW_IP` | Device IP on local network |
| `FULLY_KIOSK_PASSWORD` | Remote Admin API password |
| `FULLY_KIOSK_PORT` | Remote Admin port (default 2323) |
| `FULLY_KIOSK_START_URL` | Production start URL |

## Documentation

1. **[ECHO_KIOSK_SETUP.md](ECHO_KIOSK_SETUP.md)** — Initial one-time setup
   - Production vs development mode
   - Boot configuration, TWRP auto-reboot guard, and power loss recovery
   - Bloatware removal, permissions, TTS settings

2. **[ECHO_DEVICE_SETUP.md](ECHO_DEVICE_SETUP.md)** — Memory optimization and device tuning
   - Kernel parameters and developer options
   - Fully Kiosk Browser configuration
   - Slideshow photos and monitoring tools

3. **[ALARM_MEMORY_CONSTRAINTS.md](ALARM_MEMORY_CONSTRAINTS.md)** — Building alarm features
   - Memory constraints and failed approaches
   - Component lifecycle and unmounting patterns

## Related Documentation

- [../DEVELOPMENT_ENVIRONMENT.md](../DEVELOPMENT_ENVIRONMENT.md) — Network setup and frontend deployment
- [../TIME_MANAGEMENT_ARCHITECTURE.md](../TIME_MANAGEMENT_ARCHITECTURE.md) — Timezone and time context
