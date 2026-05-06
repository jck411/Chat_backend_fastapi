"""MCP server management service.

Extracts MCP server management out of ChatOrchestrator so that the router
can manage connections, discovery, and registry independently.
"""

from __future__ import annotations

import logging
from typing import Any

from ..chat.mcp_registry import (
    MCP_DISCOVERY_PORTS,
    MCPServerConfig,
    MCPToolAggregator,
)
from ..services.mcp_server_settings import MCPServerSettingsService

logger = logging.getLogger(__name__)


class MCPManagementService:
    """Manage MCP server connections and registry."""

    def __init__(
        self,
        aggregator: MCPToolAggregator,
        settings_service: MCPServerSettingsService,
    ) -> None:
        self._aggregator = aggregator
        self._settings = settings_service

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    async def connect_server(self, url: str) -> dict[str, Any]:
        """Connect to a new MCP server by URL, discover its tools.

        Returns server status dict on success.
        """
        server_id = await self._aggregator.connect_to_url(url)

        # Persist the new server in the registry
        configs = await self._settings.get_configs()
        if not any(c.id == server_id for c in configs):
            new_cfg = MCPServerConfig(id=server_id, url=url)
            await self._settings.add_server(new_cfg)

        # Return the status for the newly connected server
        for entry in self._aggregator.describe_servers():
            if entry["id"] == server_id:
                return entry
        return {"id": server_id, "url": url, "connected": True, "tools": []}

    async def remove_server(self, server_id: str) -> None:
        """Disconnect and remove a server from the registry."""
        # Remove from settings
        await self._settings.remove_server(server_id)
        # Re-apply configs to disconnect
        configs = await self._settings.get_configs()
        await self._aggregator.apply_configs(configs)

    async def discover_servers(
        self, host: str, ports: list[int]
    ) -> list[dict[str, Any]]:
        """Scan for MCP servers on a network host.

        Returns a list of server status dicts for discovered servers.
        """
        results: list[dict[str, Any]] = []
        known_ids: set[str] = {c.id for c in await self._settings.get_configs()}
        for port in ports:
            is_running = await self._aggregator.is_server_running(host, port)
            if not is_running:
                continue
            url = f"http://{host}:{port}/mcp"
            try:
                server_id = await self._aggregator.connect_to_url(url)
                # Persist if not already known
                if server_id not in known_ids:
                    new_cfg = MCPServerConfig(id=server_id, url=url)
                    await self._settings.add_server(new_cfg)
                    known_ids.add(server_id)

                for entry in self._aggregator.describe_servers():
                    if entry["id"] == server_id:
                        results.append(entry)
                        break
            except Exception:
                logger.exception("Failed to connect to MCP server at %s", url)

        return results

    async def discover_known_hosts(self) -> list[dict[str, Any]]:
        """Scan hosts derived from configured server URLs (+ explicit discovery_hosts).

        For each host, scans MCP_DISCOVERY_PORTS. Any responding server
        is auto-connected and persisted to the registry.
        Returns status dicts for newly discovered servers.
        """
        configs = await self._settings.get_configs()
        explicit_hosts = await self._settings.get_discovery_hosts()

        # Derive hosts from existing server URLs
        hosts: set[str] = set(explicit_hosts)
        for cfg in configs:
            try:
                from urllib.parse import urlparse

                parsed = urlparse(cfg.url)
                if parsed.hostname:
                    hosts.add(parsed.hostname)
            except Exception:
                continue

        if not hosts:
            return []

        # Build set of already-known URLs to skip
        known_urls: set[str] = {cfg.url for cfg in configs}

        all_discovered: list[dict[str, Any]] = []
        ports = list(MCP_DISCOVERY_PORTS)

        for host in sorted(hosts):
            for port in ports:
                url = f"http://{host}:{port}/mcp"
                if url in known_urls:
                    continue  # Already configured, skip
                is_running = await self._aggregator.is_server_running(host, port)
                if not is_running:
                    continue
                try:
                    server_id = await self._aggregator.connect_to_url(url)
                    # Persist if not already in settings
                    existing = await self._settings.get_configs()
                    if not any(c.id == server_id for c in existing):
                        new_cfg = MCPServerConfig(id=server_id, url=url)
                        await self._settings.add_server(new_cfg)
                        logger.info(
                            "Auto-discovered MCP server '%s' at %s", server_id, url
                        )
                    for entry in self._aggregator.describe_servers():
                        if entry["id"] == server_id:
                            all_discovered.append(entry)
                            break
                except Exception:
                    logger.debug(
                        "Port %d on %s responded but MCP connect failed", port, host
                    )

        return all_discovered

    async def reconnect_all(self) -> None:
        """Reload configs from disk, reconnect all, and discover new servers."""
        configs = await self._settings.get_configs()
        await self._aggregator.apply_configs(configs)
        await self.discover_known_hosts()

    # ------------------------------------------------------------------
    # Status & toggles
    # ------------------------------------------------------------------

    async def get_status(self) -> list[dict[str, Any]]:
        """Return all servers with connection status and tools."""
        return self._aggregator.describe_servers()

    async def toggle_tool(self, server_id: str, tool_name: str, enabled: bool) -> None:
        """Enable or disable a specific tool."""
        await self._settings.toggle_tool(server_id, tool_name, enabled=enabled)
        configs = await self._settings.get_configs()
        await self._aggregator.apply_configs(configs)

    async def refresh(self) -> None:
        """Trigger a manual tool catalogue refresh."""
        await self._aggregator.refresh()

    async def update_disabled_tools(
        self, server_id: str, disabled_tools: list[str]
    ) -> None:
        """Update disabled_tools for a server and reload configs."""
        await self._settings.patch_server(server_id, disabled_tools=disabled_tools)
        configs = await self._settings.get_configs()
        await self._aggregator.apply_configs(configs)


__all__ = ["MCPManagementService"]
