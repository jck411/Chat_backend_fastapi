#!/bin/bash
# Start the FastAPI backend with autoreload (HTTPS via local certs/).
#
# Usage: ./start.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Killing any process on :8000...${NC}"
uv run python scripts/kill_port.py 8000 || true

SSL_ARGS=()
if [[ -f certs/server.key && -f certs/server.crt ]]; then
    SSL_ARGS=(--ssl-keyfile=certs/server.key --ssl-certfile=certs/server.crt)
    echo -e "${GREEN}Starting backend on https://0.0.0.0:8000${NC}"
else
    echo -e "${GREEN}Starting backend on http://0.0.0.0:8000 (no certs/ found)${NC}"
fi

exec uv run uvicorn backend.app:create_app \
    --factory \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    "${SSL_ARGS[@]}"
