"""Tests for MCP server configuration loading and aggregation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, Tool

from backend.chat.mcp_registry import (
    MCPServerConfig,
    MCPToolAggregator,
    load_server_configs,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def build_tool_definition(name: str, description: str) -> tuple[Tool, dict[str, Any]]:
    tool = Tool(
        name=name,
        description=description,
        inputSchema={"type": "object", "properties": {}},
    )
    spec = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }
    return tool, spec


def make_config(**kwargs: Any) -> MCPServerConfig:
    if "url" not in kwargs:
        kwargs["url"] = "http://127.0.0.1:9100/mcp"
    return MCPServerConfig(**kwargs)


def make_fake_client_factory(
    tool_map: dict[str, list[tuple[Tool, dict[str, Any]]]],
    created: dict[str, Any],
):
    """Return a FakeClient class that replaces MCPToolClient in tests."""

    class FakeClient:
        def __init__(self, url: str, *, server_id: str | None = None) -> None:
            sid = server_id or url
            definitions = tool_map.get(sid, [])
            self._tools = [tool for tool, _ in definitions]
            self._specs = [spec for _, spec in definitions]
            self._calls: list[tuple[str, dict[str, Any]]] = []
            self._closed = False
            self._server_id = sid
            self._url = url
            created[sid] = self

        @property
        def server_id(self) -> str:
            return self._server_id

        @property
        def url(self) -> str:
            return self._url

        async def connect(self) -> None:
            return None

        async def refresh_tools(self) -> None:
            return None

        def get_openai_tools(self) -> list[dict[str, Any]]:
            return [json.loads(json.dumps(spec)) for spec in self._specs]

        @property
        def tools(self) -> list[Tool]:
            return list(self._tools)

        async def call_tool(
            self,
            name: str,
            arguments: dict[str, Any] | None = None,
        ) -> CallToolResult:
            record = (name, arguments or {})
            self._calls.append(record)
            return CallToolResult(
                content=[],
                structuredContent={
                    "server": self._server_id,
                    "tool": name,
                    "arguments": arguments or {},
                },
                isError=False,
            )

        async def close(self) -> None:
            self._closed = True

        @property
        def calls(self) -> list[tuple[str, dict[str, Any]]]:
            return list(self._calls)

        @property
        def closed(self) -> bool:
            return self._closed

    return FakeClient


# ------------------------------------------------------------------
# Config model tests
# ------------------------------------------------------------------


def test_config_new_format() -> None:
    cfg = MCPServerConfig(id="test", url="http://host:9000/mcp")
    assert cfg.id == "test"
    assert cfg.url == "http://host:9000/mcp"
    assert cfg.disabled_tools == set()


def test_config_ignores_extra_fields() -> None:
    """Legacy fields like module, command, contexts etc. are silently dropped."""
    cfg = MCPServerConfig.model_validate(
        {
            "id": "legacy",
            "url": "http://127.0.0.1:9001/mcp",
            "module": "backend.mcp_servers.foo",
            "command": ["python", "-m", "foo"],
            "cwd": "/tmp",
            "env": {"FOO": "bar"},
            "contexts": ["calendar"],
            "tool_overrides": {},
            "client_enabled": {"svelte": True},
            "tool_prefix": "pfx",
        }
    )
    assert cfg.id == "legacy"
    assert cfg.url == "http://127.0.0.1:9001/mcp"
    # Legacy fields are not present on the model
    assert not hasattr(cfg, "module") or getattr(cfg, "module", None) is None


# ------------------------------------------------------------------
# Config loader tests
# ------------------------------------------------------------------


def test_load_server_configs_from_file(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {"id": "a", "url": "http://host:9001/mcp"},
                    {"id": "b", "url": "http://host:9002/mcp"},
                ]
            }
        ),
        encoding="utf-8",
    )
    configs = load_server_configs(path)
    assert len(configs) == 2
    assert configs[0].id == "a"
    assert configs[1].id == "b"


def test_load_server_configs_uses_fallback(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    fallback = [{"id": "local", "url": "http://127.0.0.1:9003/mcp"}]
    configs = load_server_configs(path, fallback=fallback)
    assert len(configs) == 1
    assert configs[0].id == "local"
    assert configs[0].url == "http://127.0.0.1:9003/mcp"


def test_load_server_configs_overrides_fallback(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {"id": "local", "url": "http://host:9100/mcp"}
                ]
            }
        ),
        encoding="utf-8",
    )
    fallback = [{"id": "local", "url": "http://127.0.0.1:9003/mcp"}]
    configs = load_server_configs(path, fallback=fallback)
    assert len(configs) == 1
    assert configs[0].url == "http://host:9100/mcp"


# ------------------------------------------------------------------
# Aggregator tests
# ------------------------------------------------------------------


async def test_aggregator_preserves_unique_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_map = {
        "server_a": [build_tool_definition("alpha", "Alpha tool")],
        "server_b": [build_tool_definition("beta", "Beta tool")],
    }
    created: dict[str, Any] = {}
    fake_client_cls = make_fake_client_factory(tool_map, created)
    monkeypatch.setattr("backend.chat.mcp_registry.MCPToolClient", fake_client_cls)

    configs = [
        make_config(id="server_a"),
        make_config(id="server_b", url="http://127.0.0.1:9101/mcp"),
    ]

    aggregator = MCPToolAggregator(configs)
    await aggregator.connect()

    tool_names = {entry["function"]["name"] for entry in aggregator.get_openai_tools()}
    assert tool_names == {"alpha", "beta"}

    result = await aggregator.call_tool("alpha", {"value": 1})
    assert result.structuredContent is not None
    assert result.structuredContent["server"] == "server_a"
    assert created["server_a"].calls == [("alpha", {"value": 1})]

    await aggregator.close()
    assert created["server_a"].closed is True
    assert created["server_b"].closed is True


async def test_aggregator_skips_duplicate_tool_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When two servers expose the same tool name, the first one wins."""
    tool_map = {
        "server_a": [build_tool_definition("shared", "Primary shared tool")],
        "server_b": [build_tool_definition("shared", "Secondary shared tool")],
    }
    created: dict[str, Any] = {}
    fake_client_cls = make_fake_client_factory(tool_map, created)
    monkeypatch.setattr("backend.chat.mcp_registry.MCPToolClient", fake_client_cls)

    configs = [
        make_config(id="server_a"),
        make_config(id="server_b", url="http://127.0.0.1:9101/mcp"),
    ]

    aggregator = MCPToolAggregator(configs)
    await aggregator.connect()

    try:
        tool_names = [
            entry["function"]["name"] for entry in aggregator.get_openai_tools()
        ]
        # Only one "shared" tool — from server_a (first in config order)
        assert tool_names == ["shared"]

        result = await aggregator.call_tool("shared", {"value": 42})
        assert result.structuredContent["server"] == "server_a"
    finally:
        await aggregator.close()


async def test_aggregator_filters_by_server_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_map = {
        "server_a": [build_tool_definition("alpha", "Alpha tool")],
        "server_b": [build_tool_definition("beta", "Beta tool")],
    }
    created: dict[str, Any] = {}
    fake_client_cls = make_fake_client_factory(tool_map, created)
    monkeypatch.setattr("backend.chat.mcp_registry.MCPToolClient", fake_client_cls)

    configs = [
        make_config(id="server_a"),
        make_config(id="server_b", url="http://127.0.0.1:9101/mcp"),
    ]

    aggregator = MCPToolAggregator(configs)
    await aggregator.connect()

    try:
        # Filter to server_b only
        filtered = aggregator.get_openai_tools_for_servers({"server_b"})
        names = {entry["function"]["name"] for entry in filtered}
        assert names == {"beta"}

        # Filter to server_a only
        filtered = aggregator.get_openai_tools_for_servers({"server_a"})
        names = {entry["function"]["name"] for entry in filtered}
        assert names == {"alpha"}

        # All servers
        all_tools = aggregator.get_openai_tools_for_servers({"server_a", "server_b"})
        assert len(all_tools) == 2

        # Empty set = no tools
        assert aggregator.get_openai_tools_for_servers(set()) == []
    finally:
        await aggregator.close()


async def test_aggregator_disabled_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_map = {
        "server_a": [
            build_tool_definition("alpha", "Alpha tool"),
            build_tool_definition("beta", "Beta tool"),
        ],
    }
    created: dict[str, Any] = {}
    fake_client_cls = make_fake_client_factory(tool_map, created)
    monkeypatch.setattr("backend.chat.mcp_registry.MCPToolClient", fake_client_cls)

    configs = [
        make_config(id="server_a", disabled_tools={"beta"}),
    ]

    aggregator = MCPToolAggregator(configs)
    await aggregator.connect()

    try:
        names = {entry["function"]["name"] for entry in aggregator.get_openai_tools()}
        assert names == {"alpha"}
    finally:
        await aggregator.close()


async def test_http_server_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that HTTP servers can be configured and connected."""
    tool_map = {
        "http-server": [build_tool_definition("http_tool", "HTTP-based tool")],
    }
    created: dict[str, Any] = {}
    fake_client_cls = make_fake_client_factory(tool_map, created)
    monkeypatch.setattr("backend.chat.mcp_registry.MCPToolClient", fake_client_cls)

    configs = [
        make_config(
            id="http-server",
            url="http://localhost:8080/mcp",
            enabled=True,
        )
    ]

    aggregator = MCPToolAggregator(configs)
    await aggregator.connect()

    try:
        assert "http-server" in aggregator.active_servers()
        tool_names = {
            entry["function"]["name"] for entry in aggregator.get_openai_tools()
        }
        assert "http_tool" in tool_names

        result = await aggregator.call_tool("http_tool", {"param": "value"})
        assert result.structuredContent is not None
        assert result.structuredContent["server"] == "http-server"
    finally:
        await aggregator.close()


async def test_http_server_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disabled servers are not connected."""
    tool_map: dict[str, list[tuple[Tool, dict[str, Any]]]] = {}
    created: dict[str, Any] = {}
    fake_client_cls = make_fake_client_factory(tool_map, created)
    monkeypatch.setattr("backend.chat.mcp_registry.MCPToolClient", fake_client_cls)

    configs = [
        make_config(
            id="disabled-http",
            url="http://disabled.example.com/mcp",
            enabled=False,
        )
    ]

    aggregator = MCPToolAggregator(configs)
    await aggregator.connect()

    try:
        assert "disabled-http" not in aggregator.active_servers()
        assert len(aggregator.get_openai_tools()) == 0
    finally:
        await aggregator.close()


async def test_http_server_connection_failure_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection failures are handled gracefully."""

    class FailingFakeClient:
        def __init__(self, url: str, **kwargs: Any) -> None:
            self._server_id = kwargs.get("server_id", "unknown")

        @property
        def server_id(self) -> str:
            return self._server_id

        async def connect(self) -> None:
            raise ConnectionError("Failed to connect to HTTP server")

        async def close(self) -> None:
            pass

        @property
        def tools(self) -> list[Tool]:
            return []

        def get_openai_tools(self) -> list[dict[str, Any]]:
            return []

    monkeypatch.setattr("backend.chat.mcp_registry.MCPToolClient", FailingFakeClient)

    configs = [
        make_config(
            id="failing-http",
            url="http://unreachable.example.com/mcp",
            enabled=True,
        )
    ]

    aggregator = MCPToolAggregator(configs)
    await aggregator.connect()

    try:
        assert "failing-http" not in aggregator.active_servers()
        assert len(aggregator.get_openai_tools()) == 0
    finally:
        await aggregator.close()


async def test_describe_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    tool_map = {
        "server_a": [build_tool_definition("alpha", "Alpha tool")],
    }
    created: dict[str, Any] = {}
    fake_client_cls = make_fake_client_factory(tool_map, created)
    monkeypatch.setattr("backend.chat.mcp_registry.MCPToolClient", fake_client_cls)

    configs = [make_config(id="server_a")]
    aggregator = MCPToolAggregator(configs)
    await aggregator.connect()

    try:
        details = aggregator.describe_servers()
        assert len(details) == 1
        entry = details[0]
        assert entry["id"] == "server_a"
        assert entry["connected"] is True
        assert entry["tool_count"] == 1
        assert entry["url"] == "http://127.0.0.1:9100/mcp"
        assert any(t["name"] == "alpha" for t in entry["tools"])
    finally:
        await aggregator.close()
