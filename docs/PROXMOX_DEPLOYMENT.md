# Proxmox Deployment

Backend runs on Proxmox LXC `${PROXMOX_LXC_ID}` (`192.168.1.${PROXMOX_LXC_IP}`) at
`https://chat.jackshome.com` (LAN-only via Cloudflare Tunnel).

## Layout

| Item | Value |
|------|-------|
| Code | `/opt/chat-backend/` |
| Service user | `backend` |
| systemd unit | `chat-backend` (autoreload via `--reload`) |
| Port | `8000` (HTTPS, self-signed) |
| Kiosk static | `/opt/kiosk-frontend/dist/` (deployed by `kiosk_echo_frontend`) |

## Routes served by FastAPI

| Path | Source |
|------|--------|
| `/api/...` | FastAPI routers |
| `/admin/` | `src/backend/admin/` (vanilla HTML in this repo) |
| `/kiosk/` | `KIOSK_STATIC_DIR` (default `/opt/kiosk-frontend/dist/`) |
| `/health` | health check |

## Deploy

The `./scripts/deploy.sh` script handles everything from your dev machine.

```bash
./scripts/deploy.sh             # push + git pull on LXC + chown
./scripts/deploy.sh deps        # also `uv sync` + restart service
./scripts/deploy.sh restart     # restart service
./scripts/deploy.sh status      # service status + last 3 commits
./scripts/deploy.sh logs        # tail journal logs
./scripts/deploy.sh env         # push missing .env keys to server
```

The script reads `PROXMOX_HOST`, `PROXMOX_USER`, `PROXMOX_PASSWORD`,
`PROXMOX_LXC_ID` from local `.env`. When off-LAN, it prints the
`pct exec ...` command for you to paste on the Proxmox host shell.

## File ownership (critical)

The service runs as `User=backend`. Any `git pull` creates files owned by
`root` — the runtime `data/` dir must stay writable by `backend`.

```bash
chown -R backend:backend /opt/chat-backend/data/
```

The deploy script always runs this. Symptom of missing chown: 500 errors on
PUT/POST endpoints, `PermissionError` in `journalctl -u chat-backend`.

## Manual ops (paste on Proxmox host)

```bash
# Reset to remote (nuke local changes)
pct exec ${PROXMOX_LXC_ID} -- bash -c 'cd /opt/chat-backend && git fetch origin && git reset --hard origin/master && chown -R backend:backend data/ && systemctl restart chat-backend'

# Service status / logs
pct exec ${PROXMOX_LXC_ID} -- bash -c 'systemctl status chat-backend --no-pager -l'
pct exec ${PROXMOX_LXC_ID} -- bash -c 'journalctl -u chat-backend -f'
```

## Initial server setup

One-time (when first standing up `/opt/chat-backend/`):

```bash
useradd -r -s /bin/bash backend
cd /opt && git clone <repo-url> chat-backend
cd chat-backend
uv sync
mkdir -p data logs
chown -R backend:backend /opt/chat-backend
# place .env, certs/, credentials/
# install systemd unit pointing User=backend, ExecStart=/opt/chat-backend/.venv/bin/uvicorn ...
systemctl enable --now chat-backend
```

## Kiosk frontend artifact flow

The kiosk repo (`kiosk_echo_frontend`) builds its `dist/` and `rsync`s it to
`/opt/kiosk-frontend/dist/` on the LXC. The backend mounts that directory at
`/kiosk/` on startup if it exists. No Vite, no Node on the backend repo.

If `KIOSK_STATIC_DIR` is missing or empty at startup, `/kiosk/` returns 404 —
the rest of the backend keeps working.
