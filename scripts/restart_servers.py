#!/usr/bin/env python3
"""
Interactive Server Restart Manager
Restart backend and MCP servers, rebuild/deploy frontends from a menu.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# MCP server host
MCP_HOST = "192.168.1.110"
# Backend host (also serves frontends)
BACKEND_HOST = "192.168.1.111"
BACKEND_SERVICE = "backend-fastapi-dev"  # or backend-fastapi-prod

# Project root (parent of scripts/)
PROJECT_ROOT = Path(__file__).parent.parent

# Frontend configs: local source dir, remote static path, build command
FRONTENDS = [
    {
        "id": "chat",
        "name": "Chat (Svelte)",
        "local_dir": PROJECT_ROOT / "frontend",
        "remote_path": "/opt/backend-fastapi/src/backend/static/chat",
        "build_cmd": ["npm", "run", "build"],
        # Svelte outputs to dist/, needs copy
        "local_dist": PROJECT_ROOT / "frontend" / "dist",
    },
    {
        "id": "voice",
        "name": "Voice PWA",
        "local_dir": PROJECT_ROOT / "frontend-voice",
        "remote_path": "/opt/backend-fastapi/src/backend/static/voice",
        "build_cmd": ["npm", "run", "build"],
        # Voice builds directly to src/backend/static/voice
        "local_dist": PROJECT_ROOT / "src" / "backend" / "static" / "voice",
    },
    {
        "id": "kiosk",
        "name": "Kiosk",
        "local_dir": PROJECT_ROOT / "frontend-kiosk",
        "remote_path": "/opt/backend-fastapi/src/backend/static",
        "build_cmd": ["npm", "run", "build"],
        # Kiosk builds directly to src/backend/static (root)
        "local_dist": PROJECT_ROOT / "src" / "backend" / "static",
        # Note: kiosk deploys to root, so we need to exclude chat/ and voice/
        "rsync_exclude": ["chat/", "voice/"],
    },
]

# Load MCP server config
DATA_DIR = PROJECT_ROOT / "data"
MCP_SERVERS_FILE = DATA_DIR / "mcp_servers.json"


def load_mcp_servers() -> list[dict]:
    """Load MCP servers from config file."""
    if not MCP_SERVERS_FILE.exists():
        return []
    with open(MCP_SERVERS_FILE) as f:
        data = json.load(f)
    return data.get("servers", [])


def get_service_name(server_id: str) -> str:
    """Convert server ID to systemd service name."""
    return f"mcp-{server_id}"


def ssh_restart(host: str, service: str) -> tuple[bool, str]:
    """Restart a service via SSH."""
    cmd = ["ssh", f"root@{host}", f"systemctl restart {service}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, f"✅ Restarted {service} on {host}"
        else:
            return False, f"❌ Failed: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, f"❌ Timeout restarting {service}"
    except Exception as e:
        return False, f"❌ Error: {e}"


def ssh_status(host: str, service: str) -> str:
    """Get service status via SSH."""
    cmd = ["ssh", f"root@{host}", f"systemctl is-active {service}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except Exception:
        return "unknown"


def print_header():
    print("\n" + "=" * 60)
    print("🔄 SERVER RESTART MANAGER")
    print("=" * 60)


def print_menu(mcp_servers: list[dict]):
    print("\n📋 Available Servers:\n")

    # Backend
    print("  --- Backend ---")
    print("  [0] Backend API (192.168.1.111)")

    # Frontends (static - build & deploy)
    print("\n  --- Frontends (build & deploy) ---")
    for i, fe in enumerate(FRONTENDS, 1):
        print(f"  [F{i}] {fe['name']}")

    # MCP servers
    print("\n  --- MCP Servers ---")
    for i, server in enumerate(mcp_servers, 1):
        status = "✅" if server.get("enabled") else "⚪"
        # Extract port from URL
        url = server.get("url", "")
        port = ""
        if "192.168.1.110:" in url:
            port = url.split(":")[2].split("/")[0]
            port = f":{port}"
        print(f"  [{i}] {status} {server['id']:<15} ({MCP_HOST}{port})")

    print("\n  --- Bulk Actions ---")
    print("  [A] Restart ALL MCP servers")
    print("  [B] Restart Backend + ALL MCP")
    print("  [FA] Build & deploy ALL Frontends")
    print("  [X] Restart EVERYTHING (backend + MCP + deploy frontends)")
    print("  [S] Show server status")
    print("  [Q] Quit")
    print()


def restart_backend() -> tuple[bool, str]:
    """Restart the backend API server."""
    return ssh_restart(BACKEND_HOST, BACKEND_SERVICE)


def restart_mcp_server(server_id: str) -> tuple[bool, str]:
    """Restart a specific MCP server."""
    service = get_service_name(server_id)
    return ssh_restart(MCP_HOST, service)


def restart_all_mcp(servers: list[dict]) -> list[tuple[str, bool, str]]:
    """Restart all MCP servers."""
    results = []
    for server in servers:
        if server.get("enabled"):
            server_id = server["id"]
            success, msg = restart_mcp_server(server_id)
            results.append((server_id, success, msg))
    return results


def build_frontend(fe: dict) -> tuple[bool, str]:
    """Build a frontend locally."""
    local_dir = fe["local_dir"]
    if not local_dir.exists():
        return False, f"❌ Directory not found: {local_dir}"

    print(f"  📦 Building {fe['name']}...")
    try:
        result = subprocess.run(
            fe["build_cmd"],
            cwd=local_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return True, f"  ✅ Built {fe['name']}"
        else:
            return False, f"  ❌ Build failed: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, f"  ❌ Build timeout for {fe['name']}"
    except Exception as e:
        return False, f"  ❌ Build error: {e}"


def deploy_frontend(fe: dict) -> tuple[bool, str]:
    """Deploy a built frontend to the server via rsync."""
    local_dist = fe["local_dist"]
    remote_path = fe["remote_path"]

    if not local_dist.exists():
        return False, f"  ❌ Dist not found: {local_dist}"

    print(f"  🚀 Deploying {fe['name']} to {BACKEND_HOST}...")
    try:
        # Use rsync for efficient sync
        cmd = [
            "rsync",
            "-avz",
        ]
        # Add excludes if specified (e.g., kiosk shouldn't delete chat/ and voice/)
        for exclude in fe.get("rsync_exclude", []):
            cmd.extend(["--exclude", exclude])

        # Only use --delete if no excludes (avoid deleting other frontends)
        if not fe.get("rsync_exclude"):
            cmd.append("--delete")

        cmd.extend(
            [
                f"{local_dist}/",
                f"root@{BACKEND_HOST}:{remote_path}/",
            ]
        )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return True, f"  ✅ Deployed {fe['name']}"
        else:
            return False, f"  ❌ Deploy failed: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, f"  ❌ Deploy timeout for {fe['name']}"
    except Exception as e:
        return False, f"  ❌ Deploy error: {e}"


def build_and_deploy_frontend(fe: dict) -> tuple[bool, str]:
    """Build and deploy a single frontend."""
    success, msg = build_frontend(fe)
    print(msg)
    if not success:
        return False, msg

    success, msg = deploy_frontend(fe)
    print(msg)
    return success, msg


def build_and_deploy_all_frontends() -> list[tuple[str, bool, str]]:
    """Build and deploy all frontends."""
    results = []
    for fe in FRONTENDS:
        success, msg = build_and_deploy_frontend(fe)
        results.append((fe["id"], success, msg))
    return results


def show_status(mcp_servers: list[dict]):
    """Show status of all servers."""
    print("\n📊 Server Status:\n")

    # Backend status
    status = ssh_status(BACKEND_HOST, BACKEND_SERVICE)
    icon = "🟢" if status == "active" else "🔴"
    print(f"  {icon} Backend API: {status}")

    # Frontend status (check if static dirs exist on server)
    print("\n  Frontends (static files):")
    for fe in FRONTENDS:
        cmd = [
            "ssh",
            f"root@{BACKEND_HOST}",
            f"test -d {fe['remote_path']} && echo exists",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            exists = "exists" in result.stdout
            icon = "🟢" if exists else "⚪"
            status = "deployed" if exists else "not deployed"
            print(f"  {icon} {fe['name']:<15}: {status}")
        except Exception:
            print(f"  ⚪ {fe['name']:<15}: unknown")

    # MCP server status
    print("\n  MCP Servers:")
    for server in mcp_servers:
        if server.get("enabled"):
            service = get_service_name(server["id"])
            status = ssh_status(MCP_HOST, service)
            icon = "🟢" if status == "active" else "🔴"
            print(f"  {icon} {server['id']:<15}: {status}")


def main():
    servers = load_mcp_servers()

    if not servers:
        print("❌ No MCP servers found in config")
        sys.exit(1)

    # Filter to only local MCP servers (on 192.168.1.110)
    local_servers = [s for s in servers if MCP_HOST in s.get("url", "")]

    while True:
        print_header()
        print_menu(local_servers)

        choice = input("Select server to restart: ").strip().upper()

        if choice == "Q":
            print("\n👋 Goodbye!")
            break

        elif choice == "S":
            show_status(local_servers)

        elif choice == "0":
            print("\n🔄 Restarting Backend API...")
            success, msg = restart_backend()
            print(msg)

        elif choice == "A":
            print("\n🔄 Restarting ALL MCP servers...")
            results = restart_all_mcp(local_servers)
            for server_id, success, msg in results:
                print(msg)

        elif choice == "B":
            print("\n🔄 Restarting Backend + ALL MCP servers...")
            success, msg = restart_backend()
            print(msg)
            results = restart_all_mcp(local_servers)
            for server_id, success, msg in results:
                print(msg)

        elif choice == "FA":
            print("\n🔄 Building & deploying ALL Frontends...")
            build_and_deploy_all_frontends()

        elif choice == "X":
            print("\n🔄 Restarting EVERYTHING...")
            # Backend
            success, msg = restart_backend()
            print(msg)
            # MCP servers
            results = restart_all_mcp(local_servers)
            for server_id, success, msg in results:
                print(msg)
            # Frontends
            print("\n📦 Building & deploying frontends...")
            build_and_deploy_all_frontends()

        elif choice.startswith("F") and len(choice) > 1 and choice[1:].isdigit():
            idx = int(choice[1:]) - 1
            if 0 <= idx < len(FRONTENDS):
                fe = FRONTENDS[idx]
                print(f"\n🔄 Building & deploying {fe['name']}...")
                build_and_deploy_frontend(fe)
            else:
                print("❌ Invalid selection")

        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(local_servers):
                server = local_servers[idx]
                print(f"\n🔄 Restarting {server['id']}...")
                success, msg = restart_mcp_server(server["id"])
                print(msg)
            else:
                print("❌ Invalid selection")

        else:
            print("❌ Invalid option")

        input("\nPress Enter to continue...")


if __name__ == "__main__":
    main()
