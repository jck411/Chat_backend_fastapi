"""Async MCP client wrapper for tool execution (HTTP-only)."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.types import CallToolResult, ListToolsResult, Tool

logger = logging.getLogger(__name__)

# Connection timeout for HTTP MCP servers (seconds)
HTTP_CONNECTION_TIMEOUT = 30.0


class MCPToolClient:
    """Maintain a long-lived MCP session and expose tool execution helpers.

    This is a pure HTTP client — it connects to an already-running MCP server
    at the given URL.  It never spawns or manages server processes.
    """

    def __init__(self, url: str, *, server_id: str | None = None) -> None:
        self._url = url
        self._server_id = server_id or url
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._init_result: Any | None = None  # InitializeResult from MCP handshake
        self._tools: list[Tool] = []
        self._lock = asyncio.Lock()
        self._lifecycle_task: asyncio.Task | None = None
        self._close_event: asyncio.Event | None = None
        self._ready_event: asyncio.Event | None = None
        self._last_connection_error: Exception | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def server_id(self) -> str:
        return self._server_id

    @property
    def url(self) -> str:
        return self._url

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _run_lifecycle(self) -> None:
        """Own the MCP session lifetime in a single task."""

        exit_stack = AsyncExitStack()
        try:
            from mcp.client.streamable_http import streamablehttp_client

            logger.info(
                "Connecting to MCP server at %s (id=%s)", self._url, self._server_id
            )

            async with asyncio.timeout(HTTP_CONNECTION_TIMEOUT):
                http_manager = streamablehttp_client(self._url)
                read_stream, write_stream, _ = await exit_stack.enter_async_context(
                    http_manager
                )

            session = ClientSession(read_stream, write_stream)
            await exit_stack.enter_async_context(session)
            init_result = await session.initialize()

            async with self._lock:
                self._exit_stack = exit_stack
                self._session = session
                self._init_result = init_result
                self._last_connection_error = None

            await self.refresh_tools()

            if self._ready_event is not None and not self._ready_event.is_set():
                self._ready_event.set()

            # Hold the connection open until close() is called.
            if self._close_event is not None:
                await self._close_event.wait()

        except asyncio.TimeoutError as exc:
            async with self._lock:
                self._last_connection_error = exc
            logger.error(
                "Timeout connecting to MCP server '%s' after %ss",
                self._url,
                HTTP_CONNECTION_TIMEOUT,
            )
            if self._ready_event is not None and not self._ready_event.is_set():
                self._ready_event.set()

        except httpx.ConnectError as exc:
            async with self._lock:
                self._last_connection_error = exc
            error_msg = str(exc).lower()
            if "name or service not known" in error_msg or "getaddrinfo" in error_msg:
                logger.error(
                    "DNS resolution failed for MCP server '%s': %s", self._url, exc
                )
            elif "connection refused" in error_msg:
                logger.error(
                    "Connection refused to MCP server '%s': %s", self._url, exc
                )
            else:
                logger.error("Connect error for MCP server '%s': %s", self._url, exc)
            if self._ready_event is not None and not self._ready_event.is_set():
                self._ready_event.set()

        except httpx.NetworkError as exc:
            async with self._lock:
                self._last_connection_error = exc
            logger.error(
                "Network error connecting to MCP server '%s': %s", self._url, exc
            )
            if self._ready_event is not None and not self._ready_event.is_set():
                self._ready_event.set()

        except httpx.HTTPStatusError as exc:
            async with self._lock:
                self._last_connection_error = exc
            code = exc.response.status_code
            phrase = exc.response.reason_phrase
            if code == 401:
                logger.error("Authentication required for MCP server '%s'", self._url)
            elif code == 403:
                logger.error("Access forbidden to MCP server '%s'", self._url)
            elif code == 404:
                logger.error("MCP endpoint not found '%s'", self._url)
            elif code >= 500:
                logger.error(
                    "Server error from MCP server '%s': %s %s", self._url, code, phrase
                )
            else:
                logger.error(
                    "HTTP error from MCP server '%s': %s %s", self._url, code, phrase
                )
            if self._ready_event is not None and not self._ready_event.is_set():
                self._ready_event.set()

        except ValueError as exc:
            async with self._lock:
                self._last_connection_error = exc
            logger.error(
                "Invalid stream format from MCP server '%s': %s", self._url, exc
            )
            if self._ready_event is not None and not self._ready_event.is_set():
                self._ready_event.set()

        except Exception as exc:  # noqa: BLE001
            async with self._lock:
                self._last_connection_error = exc
            logger.error(
                "Unexpected error connecting to MCP server '%s': %s", self._url, exc
            )
            if self._ready_event is not None and not self._ready_event.is_set():
                self._ready_event.set()

        finally:
            if self._ready_event is not None and not self._ready_event.is_set():
                self._ready_event.set()
            try:
                await asyncio.wait_for(exit_stack.aclose(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "MCP session close timed out for server '%s'", self._server_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Error closing MCP session for server '%s': %s",
                    self._server_id,
                    exc,
                )
            async with self._lock:
                self._exit_stack = None
                self._session = None
                self._tools = []
                self._close_event = None
                self._ready_event = None
                self._lifecycle_task = None

    async def connect(self) -> None:
        """Connect to the MCP server and initialise the session."""

        async with self._lock:
            if self._session is not None:
                return
            lifecycle = self._lifecycle_task
            ready_event = self._ready_event

            if lifecycle is None or lifecycle.done() or ready_event is None:
                self._close_event = asyncio.Event()
                self._ready_event = asyncio.Event()
                self._lifecycle_task = asyncio.create_task(self._run_lifecycle())
                lifecycle = self._lifecycle_task
                ready_event = self._ready_event

        if ready_event is None or lifecycle is None:
            raise RuntimeError("Failed to initialise MCP client lifecycle task")

        await ready_event.wait()

        async with self._lock:
            if self._session is not None:
                return
            error = self._last_connection_error

        if lifecycle.done() and error is None:
            try:
                lifecycle.result()
            except Exception as exc:  # noqa: BLE001
                error = exc

        if error:
            raise ConnectionError(
                f"Failed to connect to MCP server '{self._server_id}': {error}"
            ) from error
        raise ConnectionError(f"Failed to connect to MCP server '{self._server_id}'")

    async def close(self) -> None:
        """Tear down the MCP session."""

        async with self._lock:
            lifecycle = self._lifecycle_task
            close_event = self._close_event

        if lifecycle is None:
            async with self._lock:
                self._exit_stack = None
                self._session = None
                self._tools = []
            return

        logger.info("Closing MCP session for server '%s'", self._server_id)

        if close_event is not None and not close_event.is_set():
            close_event.set()

        try:
            await asyncio.wait_for(lifecycle, timeout=2.5)
        except asyncio.TimeoutError:
            logger.warning(
                "MCP session close timed out for server '%s'", self._server_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Error closing MCP session for server '%s': %s", self._server_id, exc
            )

        async with self._lock:
            self._exit_stack = None
            self._session = None
            self._tools = []
            self._close_event = None
            self._ready_event = None
            self._lifecycle_task = None

    # ------------------------------------------------------------------
    # Tool operations
    # ------------------------------------------------------------------

    async def refresh_tools(self) -> None:
        """Fetch and cache the available tools from the MCP server."""

        if self._session is None:
            raise RuntimeError("MCP session has not been initialised")

        tools: list[Tool] = []
        cursor: str | None = None
        while True:
            result: ListToolsResult = await self._session.list_tools(cursor=cursor)
            tools.extend(result.tools)
            cursor = result.nextCursor
            if not cursor:
                break
        self._tools = tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        """Execute a tool by name with optional JSON arguments."""

        if self._session is None:
            raise RuntimeError("MCP session has not been initialised")

        logger.info("[MCP-CLIENT] Calling tool '%s' with args=%s", name, arguments)
        try:
            result = await self._session.call_tool(name, arguments or {})
            logger.info("[MCP-CLIENT] Tool '%s' completed", name)
            return result
        except Exception as exc:
            logger.error("[MCP-CLIENT] Tool '%s' FAILED: %s", name, exc)
            raise

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """Return tools formatted for OpenAI/OpenRouter tool definitions."""

        formatted: list[dict[str, Any]] = []
        for tool in self._tools:
            description = tool.description or tool.title or ""
            entry: dict[str, Any] = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": description,
                    "parameters": tool.inputSchema
                    or {"type": "object", "properties": {}},
                },
            }
            formatted.append(entry)
        return formatted

    @staticmethod
    def format_tool_result(result: CallToolResult) -> str:
        """Convert an MCP tool result into a plain-text string."""

        texts: list[str] = []
        for item in result.content:
            data = item.model_dump()
            if item.type == "text":
                value = data.get("text")
                if isinstance(value, str):
                    texts.append(value)
            else:
                texts.append(json.dumps(data))
        if not texts:
            if result.structuredContent:
                texts.append(json.dumps(result.structuredContent))
        return "\n".join(texts)


__all__ = ["MCPToolClient"]
