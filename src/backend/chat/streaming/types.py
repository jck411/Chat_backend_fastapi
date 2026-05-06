"""Type definitions for the chat streaming subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol


SseEvent = dict[str, str | None]


class ToolExecutor(Protocol):
    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        ...

    def get_openai_tools(self) -> list[dict[str, Any]]:
        ...

    def get_openai_tools_for_contexts(
        self, contexts: Iterable[str]
    ) -> list[dict[str, Any]]:
        ...

    def format_tool_result(self, result: Any) -> str:
        ...


@dataclass
class AssistantTurn:
    content: str | list[dict[str, Any]] | None
    tool_calls: list[dict[str, Any]]
    finish_reason: str | None
    model: str | None
    usage: dict[str, Any] | None
    meta: dict[str, Any] | None
    generation_id: str | None
    reasoning: list[dict[str, Any]] | None
    created_at: str | None = None
    created_at_utc: str | None = None

    def to_message_dict(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
        }
        if self.content is not None:
            message["content"] = self.content
        if self.tool_calls:
            message["tool_calls"] = self.tool_calls
        if self.created_at is not None:
            message["created_at"] = self.created_at
        if self.created_at_utc is not None:
            message["created_at_utc"] = self.created_at_utc
        return message


__all__ = ["AssistantTurn", "SseEvent", "ToolExecutor"]

