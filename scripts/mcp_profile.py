#!/usr/bin/env python3
"""CLI tool for managing MCP client profiles.

Usage:
    mcp-profile list                                  # List all profiles
    mcp-profile show <profile-id>                     # Show profile details
    mcp-profile create <profile-id> [--servers ...]   # Create new profile
    mcp-profile enable <profile-id> <server-id>       # Enable a server
    mcp-profile disable <profile-id> <server-id>      # Disable a server
    mcp-profile delete <profile-id>                   # Delete a profile
    mcp-profile servers                               # List available MCP servers
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add src to path for imports
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from backend.schemas.client_profiles import ClientProfile
from backend.services.client_profiles import ClientProfileService

# Colors for terminal output
GREEN = "\033[0;32m"
RED = "\033[0;31m"
CYAN = "\033[0;36m"
YELLOW = "\033[1;33m"
BOLD = "\033[1m"
NC = "\033[0m"  # No Color


def get_profiles_dir() -> Path:
    """Return the client profiles directory."""
    return PROJECT_ROOT / "data" / "client_profiles"


def get_mcp_servers_path() -> Path:
    """Return the MCP servers config path."""
    return PROJECT_ROOT / "data" / "mcp_servers.json"


def load_available_servers() -> list[str]:
    """Load available MCP server IDs from config."""
    path = get_mcp_servers_path()
    if not path.exists():
        return []

    with open(path) as f:
        data = json.load(f)

    servers = data.get("servers", data) if isinstance(data, dict) else data
    return [s.get("id") for s in servers if s.get("id")]


async def cmd_list(args: argparse.Namespace) -> int:
    """List all profiles."""
    service = ClientProfileService(get_profiles_dir())
    profiles = await service.list_profiles()

    if not profiles:
        print(f"{YELLOW}No profiles found.{NC}")
        print(f"Create one with: {CYAN}mcp-profile create <profile-id>{NC}")
        return 0

    print(f"\n{BOLD}Client Profiles{NC}\n")
    for profile in profiles:
        server_count = len(profile.enabled_servers)
        print(f"  {GREEN}●{NC} {BOLD}{profile.profile_id}{NC}")
        if profile.description:
            print(f"    {profile.description}")
        print(f"    {CYAN}{server_count}{NC} server(s) enabled")
        print()

    return 0


async def cmd_show(args: argparse.Namespace) -> int:
    """Show profile details."""
    service = ClientProfileService(get_profiles_dir())
    profile = await service.get_profile(args.profile_id)

    if profile is None:
        print(f"{RED}Profile not found: {args.profile_id}{NC}")
        return 1

    print(f"\n{BOLD}Profile: {profile.profile_id}{NC}\n")
    if profile.description:
        print(f"  Description: {profile.description}")
    print(f"\n  {BOLD}Enabled Servers:{NC}")

    if profile.enabled_servers:
        for server in sorted(profile.enabled_servers):
            print(f"    {GREEN}✓{NC} {server}")
    else:
        print(f"    {YELLOW}(none){NC}")

    # Show disabled servers for context
    available = set(load_available_servers())
    disabled = available - set(profile.enabled_servers)
    if disabled:
        print(f"\n  {BOLD}Disabled Servers:{NC}")
        for server in sorted(disabled):
            print(f"    {RED}✗{NC} {server}")

    print()
    return 0


async def cmd_create(args: argparse.Namespace) -> int:
    """Create a new profile."""
    service = ClientProfileService(get_profiles_dir())

    # Check if already exists
    existing = await service.get_profile(args.profile_id)
    if existing is not None:
        print(f"{RED}Profile already exists: {args.profile_id}{NC}")
        return 1

    servers = []
    if args.servers:
        servers = [s.strip() for s in args.servers.split(",") if s.strip()]

    profile = ClientProfile(
        profile_id=args.profile_id,
        enabled_servers=servers,
        description=args.description or "",
    )

    await service.create_profile(profile)
    print(f"{GREEN}Created profile: {args.profile_id}{NC}")

    if servers:
        print(f"  Enabled servers: {', '.join(servers)}")
    else:
        print(f"  {YELLOW}No servers enabled. Use 'mcp-profile enable' to add servers.{NC}")

    return 0


async def cmd_enable(args: argparse.Namespace) -> int:
    """Enable a server in a profile."""
    service = ClientProfileService(get_profiles_dir())

    try:
        profile = await service.add_server(args.profile_id, args.server_id)
        print(f"{GREEN}Enabled {args.server_id} in profile {args.profile_id}{NC}")
        print(f"  Now enabled: {', '.join(sorted(profile.enabled_servers))}")
        return 0
    except KeyError as e:
        print(f"{RED}{e}{NC}")
        return 1


async def cmd_disable(args: argparse.Namespace) -> int:
    """Disable a server in a profile."""
    service = ClientProfileService(get_profiles_dir())

    try:
        profile = await service.remove_server(args.profile_id, args.server_id)
        print(f"{RED}Disabled {args.server_id} in profile {args.profile_id}{NC}")
        if profile.enabled_servers:
            print(f"  Still enabled: {', '.join(sorted(profile.enabled_servers))}")
        else:
            print(f"  {YELLOW}No servers enabled.{NC}")
        return 0
    except KeyError as e:
        print(f"{RED}{e}{NC}")
        return 1


async def cmd_delete(args: argparse.Namespace) -> int:
    """Delete a profile."""
    service = ClientProfileService(get_profiles_dir())

    deleted = await service.delete_profile(args.profile_id)
    if deleted:
        print(f"{GREEN}Deleted profile: {args.profile_id}{NC}")
        return 0
    else:
        print(f"{RED}Profile not found: {args.profile_id}{NC}")
        return 1


async def cmd_servers(args: argparse.Namespace) -> int:
    """List available MCP servers."""
    servers = load_available_servers()

    if not servers:
        print(f"{YELLOW}No MCP servers configured.{NC}")
        return 0

    print(f"\n{BOLD}Available MCP Servers{NC}\n")
    for server in sorted(servers):
        print(f"  • {server}")
    print()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage MCP client profiles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s list
  %(prog)s show cli-default
  %(prog)s create my-profile --servers shell-control,notes
  %(prog)s enable my-profile spotify
  %(prog)s disable my-profile gmail
  %(prog)s delete my-profile
  %(prog)s servers
""",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    subparsers.add_parser("list", help="List all profiles")

    # show
    show_parser = subparsers.add_parser("show", help="Show profile details")
    show_parser.add_argument("profile_id", help="Profile ID to show")

    # create
    create_parser = subparsers.add_parser("create", help="Create a new profile")
    create_parser.add_argument("profile_id", help="Profile ID to create")
    create_parser.add_argument(
        "--servers", "-s",
        help="Comma-separated list of server IDs to enable",
    )
    create_parser.add_argument(
        "--description", "-d",
        help="Profile description",
    )

    # enable
    enable_parser = subparsers.add_parser("enable", help="Enable a server in a profile")
    enable_parser.add_argument("profile_id", help="Profile ID")
    enable_parser.add_argument("server_id", help="Server ID to enable")

    # disable
    disable_parser = subparsers.add_parser("disable", help="Disable a server in a profile")
    disable_parser.add_argument("profile_id", help="Profile ID")
    disable_parser.add_argument("server_id", help="Server ID to disable")

    # delete
    delete_parser = subparsers.add_parser("delete", help="Delete a profile")
    delete_parser.add_argument("profile_id", help="Profile ID to delete")

    # servers
    subparsers.add_parser("servers", help="List available MCP servers")

    args = parser.parse_args()

    commands = {
        "list": cmd_list,
        "show": cmd_show,
        "create": cmd_create,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "delete": cmd_delete,
        "servers": cmd_servers,
    }

    return asyncio.run(commands[args.command](args))


if __name__ == "__main__":
    sys.exit(main())
