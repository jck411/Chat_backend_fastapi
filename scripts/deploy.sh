#!/bin/bash
# Deploy the FastAPI backend to Proxmox LXC ${PROXMOX_LXC_ID:-111}.
#
# Usage:
#   ./scripts/deploy.sh                # push + pull + dep/env check
#   ./scripts/deploy.sh deps           # push + pull + uv sync + restart
#   ./scripts/deploy.sh restart        # restart service
#   ./scripts/deploy.sh status         # service status + current commit
#   ./scripts/deploy.sh logs           # tail recent service logs
#   ./scripts/deploy.sh env            # push missing .env keys to server
#   ./scripts/deploy.sh check          # dep + env checks only

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Load Proxmox credentials from .env
if [[ -f "$ROOT_DIR/.env" ]]; then
    PROXMOX_HOST=$(grep -E '^PROXMOX_HOST=' "$ROOT_DIR/.env" | cut -d= -f2)
    PROXMOX_USER=$(grep -E '^PROXMOX_USER=' "$ROOT_DIR/.env" | cut -d= -f2)
    PROXMOX_PASSWORD=$(grep -E '^PROXMOX_PASSWORD=' "$ROOT_DIR/.env" | cut -d= -f2)
    PROXMOX_LXC_ID=$(grep -E '^PROXMOX_LXC_ID=' "$ROOT_DIR/.env" | cut -d= -f2)
    APP_DIR_OVERRIDE=$(grep -E '^APP_DIR=' "$ROOT_DIR/.env" | cut -d= -f2)
fi

PROXMOX_HOST="${PROXMOX_HOST:-192.168.1.11}"
PROXMOX_USER="${PROXMOX_USER:-root}"
PROXMOX_LXC_ID="${PROXMOX_LXC_ID:-111}"
APP_DIR="${APP_DIR_OVERRIDE:-/opt/chat-backend}"
SERVICE="${BACKEND_SERVICE:-chat-backend}"

ON_LAN=false
if ping -c1 -W1 "$PROXMOX_HOST" &>/dev/null; then
    ON_LAN=true
fi

if [[ "$ON_LAN" == true && -z "$PROXMOX_PASSWORD" ]]; then
    echo -e "${RED}PROXMOX_PASSWORD not set in .env${NC}"
    exit 1
fi

run_on_server() {
    sshpass -p "$PROXMOX_PASSWORD" ssh -o StrictHostKeyChecking=accept-new \
        "${PROXMOX_USER}@${PROXMOX_HOST}" \
        "pct exec ${PROXMOX_LXC_ID} -- bash -c '$1'"
}

print_server_commands() {
    local cmds="$1"
    echo ""
    echo -e "${YELLOW}=== Off-LAN — paste this into your Proxmox shell ===${NC}"
    echo ""
    echo -e "${GREEN}pct exec ${PROXMOX_LXC_ID} -- bash -c '${cmds}'${NC}"
    echo ""
}

deploy_to_server() {
    local cmds="$1"
    if [[ "$ON_LAN" == true ]]; then
        run_on_server "$cmds"
    else
        print_server_commands "$cmds"
    fi
}

check_deps() {
    if [[ "$ON_LAN" != true ]]; then return; fi
    echo -e "${YELLOW}Checking dependencies...${NC}"
    local result
    result=$(run_on_server "cd $APP_DIR && uv sync --dry-run 2>&1" 2>/dev/null) || true
    if echo "$result" | grep -q "^Would install"; then
        echo -e "${RED}Server is missing packages:${NC}"
        echo "$result" | grep "^Would install" || true
        read -rp "Install now? [Y/n] " REPLY
        REPLY="${REPLY:-y}"
        if [[ "$REPLY" =~ ^[Yy]$ ]]; then
            run_on_server "cd $APP_DIR && uv sync"
            echo -e "${GREEN}Dependencies synced.${NC}"
            return 0
        fi
    else
        echo -e "${GREEN}Dependencies up to date.${NC}"
    fi
    return 1
}

check_env_keys() {
    if [[ "$ON_LAN" != true ]]; then return; fi
    if [[ ! -f "$ROOT_DIR/.env" ]]; then return; fi

    echo -e "${YELLOW}Checking .env keys...${NC}"
    local local_keys
    local_keys=$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ROOT_DIR/.env" | cut -d= -f1 | sort -u)
    local server_keys
    server_keys=$(run_on_server "grep -E '^[A-Za-z_][A-Za-z0-9_]*=' $APP_DIR/.env" 2>/dev/null | cut -d= -f1 | tr -d '\r' | sort -u)
    local missing
    missing=$(comm -23 <(echo "$local_keys" | grep -v '^PROXMOX_' | grep -E '^[A-Z]') <(echo "$server_keys"))

    if [[ -n "$missing" ]]; then
        echo -e "${RED}Server .env is missing:${NC}"
        echo "$missing"
        read -rp "Push missing keys to server now? [Y/n] " REPLY
        REPLY="${REPLY:-y}"
        if [[ "$REPLY" =~ ^[Yy]$ ]]; then
            for key in $missing; do
                local value
                value=$(grep -E "^${key}=" "$ROOT_DIR/.env" | head -1 | cut -d= -f2-)
                run_on_server "cat >> $APP_DIR/.env <<'ENVEOF'
${key}=${value}
ENVEOF"
            done
            echo -e "${GREEN}Added $(echo "$missing" | wc -w) key(s).${NC}"
        fi
    else
        echo -e "${GREEN}.env keys in sync.${NC}"
    fi
}

MODE="${1:-backend}"

case "$MODE" in
    backend)
        echo -e "${YELLOW}=== Backend Deploy ===${NC}"
        cd "$ROOT_DIR"
        git push
        echo -e "${YELLOW}Pulling on server...${NC}"
        deploy_to_server "cd $APP_DIR && git pull && chown -R backend:backend $APP_DIR/data/"

        NEEDS_RESTART=false
        if check_deps; then NEEDS_RESTART=true; fi
        check_env_keys

        if [[ "$NEEDS_RESTART" == true ]]; then
            echo -e "${YELLOW}Restarting service (deps changed)...${NC}"
            deploy_to_server "systemctl restart $SERVICE"
        fi
        [[ "$ON_LAN" == true ]] && echo -e "${GREEN}Pushed + pulled. Dev service auto-reloads.${NC}"
        ;;

    deps)
        echo -e "${YELLOW}=== Dependency Deploy ===${NC}"
        cd "$ROOT_DIR"
        git push
        deploy_to_server "cd $APP_DIR && git pull && uv sync && chown -R backend:backend $APP_DIR/data/ && systemctl restart $SERVICE"
        [[ "$ON_LAN" == true ]] && echo -e "${GREEN}Dependencies synced and service restarted.${NC}"
        ;;

    restart)
        deploy_to_server "systemctl restart $SERVICE"
        [[ "$ON_LAN" == true ]] && echo -e "${GREEN}Restarted.${NC}"
        ;;

    status)
        deploy_to_server "cd $APP_DIR && echo 'Commit:' && git log --oneline -3 && echo '---' && systemctl status $SERVICE --no-pager -l 2>&1 | head -15"
        ;;

    logs)
        deploy_to_server "journalctl -u $SERVICE --no-pager -n 50"
        ;;

    env)
        check_env_keys
        ;;

    check)
        check_deps || true
        check_env_keys
        ;;

    *)
        echo "Usage: ./scripts/deploy.sh [backend|deps|restart|status|logs|env|check]"
        exit 1
        ;;
esac

echo -e "${GREEN}Done.${NC}"
