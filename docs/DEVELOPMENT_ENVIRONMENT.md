# Local Development

## Prerequisites

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) for dependency management
- (Optional) self-signed certs at `certs/server.crt` + `certs/server.key` for HTTPS

## Setup

```bash
cp .env.example .env
# fill in OPENROUTER_API_KEY at minimum
uv sync
```

## Run

```bash
./start.sh
# → https://0.0.0.0:8000 (or http:// if no certs/)
```

Open:
- `https://localhost:8000/admin/` — admin UI
- `https://localhost:8000/health`

The dev server runs with `--reload`, so backend Python changes hot-reload.

## Tests

```bash
uv run pytest -q
```

## MCP servers

MCP servers are external — typically run from the `mcp-servers` repo on a
separate LXC (110), or locally during development.

Configure via the admin UI (`/admin/` → MCP Servers) or by editing
`data/mcp_servers.json` directly.

## Local kiosk preview

If you want to preview the kiosk frontend locally, clone `kiosk_echo_frontend`,
`npm run build`, and point the backend at it:

```bash
KIOSK_STATIC_DIR=/path/to/kiosk_echo_frontend/dist ./start.sh
```

Then visit `https://localhost:8000/kiosk/`.

## Code quality

```bash
uv run ruff check .
uv run ruff format .
```
