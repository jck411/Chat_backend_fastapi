"""Pydantic models for chat requests and responses."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """Represents a single chat message."""

    role: Literal["system", "user", "assistant", "tool"]
    content: Any
    name: Optional[str] = None
    tool_call_id: Optional[str] = Field(default=None, alias="tool_call_id")
    client_message_id: Optional[str] = Field(default=None, alias="client_message_id")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class ChatCompletionRequest(BaseModel):
    """Incoming chat completion request payload."""

    model: Optional[str] = None
    session_id: Optional[str] = Field(default=None, alias="session_id")
    messages: List[ChatMessage]

    # Basic generation parameters
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    max_tokens: Optional[int] = None
    min_p: Optional[float] = None
    top_a: Optional[float] = None

    # Penalty parameters
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    repetition_penalty: Optional[float] = None

    # Advanced parameters
    seed: Optional[int] = None
    stop: Optional[Union[str, List[str]]] = None
    logit_bias: Optional[Dict[str, float]] = None
    top_logprobs: Optional[int] = None

    # Tool calling
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    parallel_tool_calls: Optional[bool] = None
    plugins: Optional[List[Dict[str, Any]]] = None
    web_search_options: Optional[Dict[str, Any]] = None

    # Response formatting
    response_format: Optional[Dict[str, Any]] = None
    structured_outputs: Optional[bool] = None

    # Reasoning parameters
    reasoning: Optional[Dict[str, Any]] = None

    # Provider and routing
    provider: Optional[Dict[str, Any]] = None
    models: Optional[List[str]] = None
    route: Optional[str] = None
    transforms: Optional[List[str]] = None

    # Safety and moderation
    safe_prompt: Optional[bool] = None
    raw_mode: Optional[bool] = None

    # Metadata and tracking
    metadata: Optional[Dict[str, Any]] = None
    stream_options: Optional[Dict[str, Any]] = None
    user: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None

    # Prediction and caching
    prediction: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    def to_openrouter_payload(self, default_model: str) -> Dict[str, Any]:
        """Serialize the request for OpenRouter, enforcing defaults."""

        payload = self.model_dump(
            by_alias=True, exclude_none=True, exclude={"session_id"}
        )
        payload.setdefault("model", default_model)
        payload["stream"] = True
        payload.setdefault("usage", {"include": True})

        import logging

        logger = logging.getLogger(__name__)
        logger.info(
            "🌐 WEB SEARCH in payload: plugins=%s, web_search_options=%s",
            payload.get("plugins"),
            payload.get("web_search_options"),
        )

        return payload


__all__ = ["ChatMessage", "ChatCompletionRequest"]
