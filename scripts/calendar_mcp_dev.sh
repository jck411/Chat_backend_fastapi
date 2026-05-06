#!/bin/bash
# ============================================================
# Calendar MCP Dev Launcher
# Starts: Backend (:8000) + Frontend (:5173) + Calendar MCP (:9004)
# Then connects the local calendar server to the backend.
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_SERVERS_DIR="/home/human/REPOS/mcp-servers"
CALENDAR_PORT=9004

cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PIDS=()

cleanup() {
    echo ""
    echo -e "${YELLOW}Stopping all services...${NC}"
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    pkill -P $$ 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║         Calendar MCP Dev Environment         ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

# Kill any existing processes on the ports we need
echo -e "${YELLOW}Clearing ports 8000, 5173, ${CALENDAR_PORT}...${NC}"
uv run python scripts/kill_port.py 8000 2>/dev/null || true
uv run python scripts/kill_port.py 5173 2>/dev/null || true
uv run python scripts/kill_port.py "$CALENDAR_PORT" 2>/dev/null || true
sleep 1

# Ensure google deps are installed in mcp-servers venv
echo -e "${CYAN}Syncing mcp-servers google dependencies...${NC}"
cd "$MCP_SERVERS_DIR"
uv sync --extra google -q
cd "$SCRIPT_DIR"

# Start calendar MCP server
echo -e "${GREEN}[1] Starting Calendar MCP server on :${CALENDAR_PORT}...${NC}"
cd "$MCP_SERVERS_DIR"
uv run python -m servers.calendar \
    --transport streamable-http \
    --host 127.0.0.1 \
    --port "$CALENDAR_PORT" &
PIDS+=($!)
cd "$SCRIPT_DIR"

# Start backend
echo -e "${GREEN}[2] Starting Backend on :8000...${NC}"
uv run uvicorn backend.app:create_app \
    --factory \
    --host 0.0.0.0 \
    --ssl-keyfile=certs/server.key \
    --ssl-certfile=certs/server.crt \
    --reload &
PIDS+=($!)

# Wait for backend to be ready
echo -n "Waiting for backend"
max_attempts=40
attempt=0
until curl -s -k https://localhost:8000/health > /dev/null 2>&1; do
    sleep 0.5
    attempt=$((attempt + 1))
    echo -n "."
    if [ $attempt -ge $max_attempts ]; then
        echo -e " ${RED}timeout!${NC}"
        break
    fi
done
echo " ready!"

# Start frontend
echo -e "${GREEN}[3] Starting Frontend on :5173...${NC}"
cd frontend && npm run dev &
PIDS+=($!)
cd "$SCRIPT_DIR"

# Wait for calendar server to be ready
echo -n "Waiting for calendar MCP server"
cal_attempts=0
until curl -s "http://127.0.0.1:${CALENDAR_PORT}/mcp" > /dev/null 2>&1; do
    sleep 0.5
    cal_attempts=$((cal_attempts + 1))
    echo -n "."
    if [ $cal_attempts -ge 30 ]; then
        echo -e " ${RED}timeout!${NC}"
        echo -e "${RED}Calendar server may have failed to start. Check logs above.${NC}"
        break
    fi
done
echo " ready!"

# Connect calendar server to backend
echo -e "${CYAN}Connecting calendar MCP server to backend...${NC}"
RESPONSE=$(curl -sk -X POST https://localhost:8000/api/mcp/servers/connect \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"http://127.0.0.1:${CALENDAR_PORT}/mcp\"}")

if echo "$RESPONSE" | grep -q '"connected":true'; then
    TOOL_COUNT=$(echo "$RESPONSE" | grep -o '"tool_count":[0-9]*' | grep -o '[0-9]*')
    echo -e "${GREEN}✓ Calendar server connected (${TOOL_COUNT} tools)${NC}"
else
    echo -e "${YELLOW}⚠ Connect response: ${RESPONSE}${NC}"
    echo -e "${YELLOW}  You can retry manually: curl -sk -X POST https://localhost:8000/api/mcp/servers/connect -H 'Content-Type: application/json' -d '{\"url\": \"http://127.0.0.1:${CALENDAR_PORT}/mcp\"}'${NC}"
fi

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "${BOLD}Running:${NC}"
echo -e "  ${GREEN}✓${NC} Backend:         https://localhost:8000"
echo -e "  ${GREEN}✓${NC} Frontend:        http://localhost:5173"
echo -e "  ${GREEN}✓${NC} Calendar MCP:    http://127.0.0.1:${CALENDAR_PORT}/mcp"
echo -e "${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "Press ${RED}Ctrl+C${NC} to stop all services"
echo ""

wait
