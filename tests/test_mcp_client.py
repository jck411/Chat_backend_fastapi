"""Tests for MCP client HTTP transport."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from mcp.types import CallToolResult, TextContent, Tool

from backend.chat.mcp_client import MCPToolClient

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def mock_tool() -> Tool:
    return Tool(
        name="test_tool",
        description="A test tool",
        inputSchema={"type": "object", "properties": {"arg": {"type": "string"}}},
    )


@pytest.fixture
def mock_session(mock_tool: Tool) -> MagicMock:
    """Create a mock ClientSession that supports async context manager protocol."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(
        return_value=MagicMock(tools=[mock_tool], nextCursor=None)
    )
    session.call_tool = AsyncMock(
        return_value=CallToolResult(
            content=[TextContent(type="text", text="result")], isError=False
        )
    )
    return session


def _make_http_context(streams: tuple[Any, ...] | None = None) -> MagicMock:
    """Create a mock async context manager for streamablehttp_client."""
    if streams is None:
        streams = (MagicMock(), MagicMock(), MagicMock())
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=streams)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_error_context(error: Exception) -> MagicMock:
    """Create a mock context manager that raises on enter."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(side_effect=error)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ------------------------------------------------------------------
# Constructor
# ------------------------------------------------------------------


class TestConstructor:
    def test_defaults(self) -> None:
        client = MCPToolClient(url="http://example.com/mcp")
        assert client.url == "http://example.com/mcp"
        assert client.server_id == "http://example.com/mcp"
        assert client.tools == []

    def test_with_server_id(self) -> None:
        client = MCPToolClient(url="http://host:9001/mcp", server_id="my-server")
        assert client.url == "http://host:9001/mcp"
        assert client.server_id == "my-server"


# ------------------------------------------------------------------
# Connection lifecycle
# ------------------------------------------------------------------


class TestConnection:
    async def test_successful_connection(
        self, mock_session: MagicMock, mock_tool: Tool
    ) -> None:
        client = MCPToolClient(url="http://example.com/mcp", server_id="test")

        with (
            patch("mcp.client.streamable_http.streamablehttp_client") as mock_http,
            patch("backend.chat.mcp_client.ClientSession", return_value=mock_session),
        ):
            mock_http.return_value = _make_http_context()
            await client.connect()

            assert len(client.tools) == 1
            assert client.tools[0].name == "test_tool"
            mock_http.assert_called_once_with("http://example.com/mcp")

        await client.close()

    async def test_close_clears_state(self, mock_session: MagicMock) -> None:
        client = MCPToolClient(url="http://example.com/mcp", server_id="test")

        with (
            patch("mcp.client.streamable_http.streamablehttp_client") as mock_http,
            patch("backend.chat.mcp_client.ClientSession", return_value=mock_session),
        ):
            mock_http.return_value = _make_http_context()
            await client.connect()

        await client.close()
        assert client.tools == []

    async def test_double_connect_idempotent(self, mock_session: MagicMock) -> None:
        client = MCPToolClient(url="http://example.com/mcp", server_id="test")

        with (
            patch("mcp.client.streamable_http.streamablehttp_client") as mock_http,
            patch("backend.chat.mcp_client.ClientSession", return_value=mock_session),
        ):
            mock_http.return_value = _make_http_context()
            await client.connect()
            await client.connect()  # should be a no-op
            assert mock_http.call_count == 1

        await client.close()


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


class TestErrorHandling:
    async def test_timeout(self) -> None:
        client = MCPToolClient(url="http://example.com/mcp", server_id="test")

        with patch("mcp.client.streamable_http.streamablehttp_client") as mock_http:
            mock_http.return_value = _make_error_context(
                asyncio.TimeoutError("Connection timed out")
            )
            with pytest.raises(ConnectionError) as exc_info:
                await client.connect()
            assert "test" in str(exc_info.value)

    async def test_dns_failure(self) -> None:
        client = MCPToolClient(url="http://bad.host/mcp", server_id="test")

        with patch("mcp.client.streamable_http.streamablehttp_client") as mock_http:
            err = httpx.ConnectError("Name or service not known", request=MagicMock())
            mock_http.return_value = _make_error_context(err)
            with pytest.raises(ConnectionError) as exc_info:
                await client.connect()
            assert "service not known" in str(exc_info.value).lower()

    async def test_connection_refused(self) -> None:
        client = MCPToolClient(url="http://localhost:9999/mcp", server_id="test")

        with patch("mcp.client.streamable_http.streamablehttp_client") as mock_http:
            err = httpx.ConnectError(
                "[Errno 111] Connection refused", request=MagicMock()
            )
            mock_http.return_value = _make_error_context(err)
            with pytest.raises(ConnectionError) as exc_info:
                await client.connect()
            assert "refused" in str(exc_info.value).lower()

    async def test_network_error(self) -> None:
        client = MCPToolClient(url="http://example.com/mcp", server_id="test")

        with patch("mcp.client.streamable_http.streamablehttp_client") as mock_http:
            err = httpx.NetworkError("Network unreachable", request=MagicMock())
            mock_http.return_value = _make_error_context(err)
            with pytest.raises(ConnectionError) as exc_info:
                await client.connect()
            assert "network unreachable" in str(exc_info.value).lower()

    async def test_http_401(self) -> None:
        client = MCPToolClient(url="http://example.com/mcp", server_id="test")

        with patch("mcp.client.streamable_http.streamablehttp_client") as mock_http:
            resp = MagicMock()
            resp.status_code = 401
            resp.reason_phrase = "Unauthorized"
            err = httpx.HTTPStatusError(
                "Unauthorized", request=MagicMock(), response=resp
            )
            mock_http.return_value = _make_error_context(err)
            with pytest.raises(ConnectionError) as exc_info:
                await client.connect()
            msg = str(exc_info.value).lower()
            assert "401" in msg or "unauthorized" in msg

    async def test_http_403(self) -> None:
        client = MCPToolClient(url="http://example.com/mcp", server_id="test")

        with patch("mcp.client.streamable_http.streamablehttp_client") as mock_http:
            resp = MagicMock()
            resp.status_code = 403
            resp.reason_phrase = "Forbidden"
            err = httpx.HTTPStatusError("Forbidden", request=MagicMock(), response=resp)
            mock_http.return_value = _make_error_context(err)
            with pytest.raises(ConnectionError) as exc_info:
                await client.connect()
            msg = str(exc_info.value).lower()
            assert "403" in msg or "forbidden" in msg

    async def test_http_404(self) -> None:
        client = MCPToolClient(url="http://example.com/wrong", server_id="test")

        with patch("mcp.client.streamable_http.streamablehttp_client") as mock_http:
            resp = MagicMock()
            resp.status_code = 404
            resp.reason_phrase = "Not Found"
            err = httpx.HTTPStatusError("Not Found", request=MagicMock(), response=resp)
            mock_http.return_value = _make_error_context(err)
            with pytest.raises(ConnectionError) as exc_info:
                await client.connect()
            msg = str(exc_info.value).lower()
            assert "404" in msg or "not found" in msg

    async def test_http_500(self) -> None:
        client = MCPToolClient(url="http://example.com/mcp", server_id="test")

        with patch("mcp.client.streamable_http.streamablehttp_client") as mock_http:
            resp = MagicMock()
            resp.status_code = 500
            resp.reason_phrase = "Internal Server Error"
            err = httpx.HTTPStatusError(
                "500 Internal Server Error", request=MagicMock(), response=resp
            )
            mock_http.return_value = _make_error_context(err)
            with pytest.raises(ConnectionError) as exc_info:
                await client.connect()
            msg = str(exc_info.value).lower()
            assert "500" in msg or "internal" in msg

    async def test_invalid_stream_format(self) -> None:
        client = MCPToolClient(url="http://example.com/mcp", server_id="test")

        with patch("mcp.client.streamable_http.streamablehttp_client") as mock_http:
            err = ValueError("Invalid SSE stream format: missing event type")
            mock_http.return_value = _make_error_context(err)
            with pytest.raises(ConnectionError) as exc_info:
                await client.connect()
            assert "invalid" in str(exc_info.value).lower()


# ------------------------------------------------------------------
# Tool execution
# ------------------------------------------------------------------


class TestToolExecution:
    async def test_call_tool(self, mock_session: MagicMock) -> None:
        client = MCPToolClient(url="http://example.com/mcp", server_id="test")

        with (
            patch("mcp.client.streamable_http.streamablehttp_client") as mock_http,
            patch("backend.chat.mcp_client.ClientSession", return_value=mock_session),
        ):
            mock_http.return_value = _make_http_context()
            await client.connect()

            result = await client.call_tool("test_tool", {"arg": "value"})
            assert result is not None
            mock_session.call_tool.assert_called_once_with(
                "test_tool", {"arg": "value"}
            )

        await client.close()

    async def test_get_openai_tools(self, mock_session: MagicMock) -> None:
        client = MCPToolClient(url="http://example.com/mcp", server_id="test")

        with (
            patch("mcp.client.streamable_http.streamablehttp_client") as mock_http,
            patch("backend.chat.mcp_client.ClientSession", return_value=mock_session),
        ):
            mock_http.return_value = _make_http_context()
            await client.connect()

            tools = client.get_openai_tools()
            assert len(tools) == 1
            assert tools[0]["function"]["name"] == "test_tool"
            assert tools[0]["function"]["description"] == "A test tool"

        await client.close()

    def test_format_tool_result_text(self) -> None:
        result = CallToolResult(
            content=[TextContent(type="text", text="hello world")],
            isError=False,
        )
        assert MCPToolClient.format_tool_result(result) == "hello world"

    def test_format_tool_result_empty(self) -> None:
        result = CallToolResult(content=[], isError=False)
        assert MCPToolClient.format_tool_result(result) == ""
