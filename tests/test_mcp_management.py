"""Tests for the MCP management service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.chat.mcp_registry import MCPServerConfig
from backend.services.mcp_management import MCPManagementService
from backend.services.mcp_server_settings import MCPServerSettingsService

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ------------------------------------------------------------------
# Stub aggregator for unit tests
# ------------------------------------------------------------------


class StubAggregator:
    """Minimal MCPToolAggregator replacement for management service tests."""

    def __init__(self) -> None:
        self._configs: list[MCPServerConfig] = []
        self._connected: dict[str, str] = {}  # server_id -> url
        self.refresh_calls = 0
        self.apply_configs_calls = 0

    async def connect_to_url(self, url: str, server_id: str | None = None) -> str:
        sid = server_id or url.rstrip("/").rsplit("/", 1)[0].rsplit(":", 1)[-1]
        self._connected[sid] = url
        return sid

    async def apply_configs(self, configs: Any) -> None:
        self.apply_configs_calls += 1
        self._configs = list(configs)
        # Remove clients whose server was removed
        new_ids = {c.id for c in configs}
        self._connected = {k: v for k, v in self._connected.items() if k in new_ids}

    async def refresh(self) -> None:
        self.refresh_calls += 1

    async def discover_and_connect(self) -> dict[str, bool]:
        return {}

    async def is_server_running(self, host: str, port: int) -> bool:
        return False

    def get_configs(self) -> list[MCPServerConfig]:
        return list(self._configs)

    @property
    def has_configs(self) -> bool:
        return bool(self._configs)

    def describe_servers(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for cfg in self._configs:
            result.append(
                {
                    "id": cfg.id,
                    "url": cfg.url,
                    "connected": cfg.id in self._connected,
                    "tool_count": 0,
                    "tools": [],
                    "disabled_tools": sorted(cfg.disabled_tools),
                }
            )
        return result


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_connect_server(tmp_path: Path) -> None:
    settings_path = tmp_path / "servers.json"
    settings = MCPServerSettingsService(settings_path)
    agg = StubAggregator()
    mgmt = MCPManagementService(agg, settings)  # type: ignore[arg-type]

    entry = await mgmt.connect_server("http://127.0.0.1:9001/mcp")
    assert entry["connected"] is True

    # Should be persisted
    configs = await settings.get_configs()
    assert any(c.id == entry["id"] for c in configs)


async def test_connect_server_already_known(tmp_path: Path) -> None:
    settings_path = tmp_path / "servers.json"
    settings_path.write_text(
        json.dumps({"servers": [{"id": "9001", "url": "http://127.0.0.1:9001/mcp"}]}),
        encoding="utf-8",
    )
    settings = MCPServerSettingsService(settings_path)
    agg = StubAggregator()
    mgmt = MCPManagementService(agg, settings)  # type: ignore[arg-type]

    entry = await mgmt.connect_server("http://127.0.0.1:9001/mcp")
    # Should not duplicate the config
    configs = await settings.get_configs()
    assert sum(1 for c in configs if c.id == "9001") == 1


async def test_remove_server(tmp_path: Path) -> None:
    settings_path = tmp_path / "servers.json"
    settings_path.write_text(
        json.dumps(
            {
                "servers": [
                    {"id": "alpha", "url": "http://127.0.0.1:9001/mcp"},
                    {"id": "beta", "url": "http://127.0.0.1:9002/mcp"},
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = MCPServerSettingsService(settings_path)
    agg = StubAggregator()
    mgmt = MCPManagementService(agg, settings)  # type: ignore[arg-type]

    await mgmt.remove_server("alpha")

    configs = await settings.get_configs()
    assert len(configs) == 1
    assert configs[0].id == "beta"
    assert agg.apply_configs_calls >= 1


async def test_remove_unknown_raises(tmp_path: Path) -> None:
    settings_path = tmp_path / "servers.json"
    settings = MCPServerSettingsService(settings_path)
    agg = StubAggregator()
    mgmt = MCPManagementService(agg, settings)  # type: ignore[arg-type]

    with pytest.raises(KeyError):
        await mgmt.remove_server("nope")


async def test_toggle_tool(tmp_path: Path) -> None:
    settings_path = tmp_path / "servers.json"
    settings_path.write_text(
        json.dumps({"servers": [{"id": "alpha", "url": "http://127.0.0.1:9001/mcp"}]}),
        encoding="utf-8",
    )
    settings = MCPServerSettingsService(settings_path)
    agg = StubAggregator()
    mgmt = MCPManagementService(agg, settings)  # type: ignore[arg-type]

    await mgmt.toggle_tool("alpha", "echo", False)
    configs = await settings.get_configs()
    assert "echo" in configs[0].disabled_tools
    assert agg.apply_configs_calls >= 1

    await mgmt.toggle_tool("alpha", "echo", True)
    configs = await settings.get_configs()
    assert "echo" not in configs[0].disabled_tools


async def test_refresh(tmp_path: Path) -> None:
    settings_path = tmp_path / "servers.json"
    settings = MCPServerSettingsService(settings_path)
    agg = StubAggregator()
    mgmt = MCPManagementService(agg, settings)  # type: ignore[arg-type]

    await mgmt.refresh()
    assert agg.refresh_calls == 1


async def test_get_status(tmp_path: Path) -> None:
    settings_path = tmp_path / "servers.json"
    settings_path.write_text(
        json.dumps({"servers": [{"id": "alpha", "url": "http://127.0.0.1:9001/mcp"}]}),
        encoding="utf-8",
    )
    settings = MCPServerSettingsService(settings_path)
    agg = StubAggregator()
    mgmt = MCPManagementService(agg, settings)  # type: ignore[arg-type]
    # Load configs via apply_configs (simpler than reconnect_all which scans ports)
    configs = await settings.get_configs()
    await agg.apply_configs(configs)

    status = await mgmt.get_status()
    assert len(status) == 1
    assert status[0]["id"] == "alpha"
