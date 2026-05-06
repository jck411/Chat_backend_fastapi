"""Conversation streaming orchestration."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

import httpx

from ...config import get_settings
from ...openrouter import OpenRouterClient, OpenRouterError
from ...repository import ChatRepository, format_timestamp_for_client
from ...schemas.chat import ChatCompletionRequest
from ...services.attachment_urls import refresh_message_attachments
from ...services.attachments import AttachmentService
from ...services.conversation_logging import ConversationLogWriter, MemoryBackupLogger
from ...services.model_settings import ModelCapabilities, ModelSettingsService
from .attachments import (
    normalize_structured_fragments as _normalize_structured_fragments,
)
from .content_builder import AssistantContentBuilder as _AssistantContentBuilder
from .messages import (
    parse_attachment_references as _parse_attachment_references,
)
from .messages import (
    prepare_messages_for_model as _prepare_messages_for_model,
)
from .reasoning import (
    extend_reasoning_segments as _extend_reasoning_segments,
)
from .reasoning import (
    extract_reasoning_segments as _extract_reasoning_segments,
)
from .tooling import (
    classify_tool_followup as _classify_tool_followup,
)
from .tooling import (
    enforce_tool_policy as _enforce_tool_policy,
)
from .tooling import (
    finalize_tool_calls as _finalize_tool_calls,
)
from .tooling import (
    is_tool_support_error as _is_tool_support_error,
)
from .tooling import (
    merge_tool_calls as _merge_tool_calls,
)
from .tooling import (
    tool_requires_session_id as _tool_requires_session_id,
)
from .types import AssistantTurn, SseEvent, ToolExecutor

logger = logging.getLogger(__name__)


class StreamingHandler:
    """Stream chat responses, execute tools, and persist conversation state."""

    def __init__(
        self,
        client: OpenRouterClient,
        repository: ChatRepository,
        tool_client: ToolExecutor,
        *,
        default_model: str,
        tool_hop_limit: int = 40,
        tool_error_limit: int = 10,
        model_settings: ModelSettingsService | None = None,
        attachment_service: AttachmentService | None = None,
        conversation_logger: ConversationLogWriter | None = None,
        memory_backup_logger: MemoryBackupLogger | None = None,
    ) -> None:
        self._client = client
        self._repo = repository
        self._tool_client = tool_client
        self._default_model = default_model
        self._tool_hop_limit = tool_hop_limit
        self._tool_error_limit = max(1, tool_error_limit)
        self._model_settings = model_settings
        self._attachment_service = attachment_service
        self._conversation_logger = conversation_logger
        self._memory_backup_logger = memory_backup_logger

    def set_attachment_service(self, service: AttachmentService | None) -> None:
        """Attach or replace the attachment service used for image persistence."""

        self._attachment_service = service

    async def _log_conversation_snapshot(
        self,
        session_id: str,
        request: ChatCompletionRequest,
    ) -> None:
        """Persist the latest conversation state for debugging and replay."""

        if self._conversation_logger is None:
            return

        try:
            conversation = await self._repo.get_messages(session_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to load conversation for session %s: %s", session_id, exc
            )
            return

        try:
            metadata = await self._repo.get_session_metadata(session_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to load session metadata for session %s: %s", session_id, exc
            )
            metadata = None

        request_snapshot = request.model_dump(mode="json", exclude_none=True)
        request_snapshot.pop("session_id", None)

        try:
            await self._conversation_logger.write(
                session_id=session_id,
                session_created_at=(metadata or {}).get("created_at"),
                request_snapshot=request_snapshot,
                conversation=conversation,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to write conversation log for session %s: %s", session_id, exc
            )

    async def stream_conversation(
        self,
        session_id: str,
        request: ChatCompletionRequest,
        conversation: list[dict[str, Any]],
        tools_payload: list[dict[str, Any]],
        assistant_parent_message_id: str | None,
        model_settings: ModelSettingsService | None = None,
    ) -> AsyncGenerator[SseEvent, None]:
        """Yield SSE events while maintaining state and executing tools."""

        hop_count = 0
        conversation_state = list(conversation)
        assistant_client_message_id: str | None = None
        request_metadata: dict[str, Any] | None = None
        if isinstance(request.metadata, dict):
            request_metadata = request.metadata
            candidate = request_metadata.get("client_assistant_message_id")
            if isinstance(candidate, str):
                assistant_client_message_id = candidate
        active_tools_payload = list(tools_payload)
        available_tool_names = {
            tool.get("function", {}).get("name")
            for tool in active_tools_payload
            if isinstance(tool, dict)
            and isinstance(tool.get("function"), dict)
            and isinstance(tool.get("function", {}).get("name"), str)
        }
        tool_choice_value = request.tool_choice
        requested_tool_choice = (
            tool_choice_value if isinstance(tool_choice_value, str) else None
        )
        active_model_settings = model_settings or self._model_settings
        base_tools_disabled = requested_tool_choice == "none"
        has_structured_tool_choice = isinstance(tool_choice_value, dict)
        can_retry_without_tools = (
            requested_tool_choice in (None, "auto") and not has_structured_tool_choice
        )

        total_tool_calls = 0
        # Track tool attachments to inject into next assistant response
        pending_tool_attachments: list[dict[str, Any]] = []
        consecutive_tool_errors = 0

        while True:
            tools_available = bool(active_tools_payload)
            tools_disabled = base_tools_disabled or not tools_available
            routing_headers: dict[str, Any] | None = None
            active_model = self._default_model
            overrides: dict[str, Any] = {}
            capability: ModelCapabilities | None = None
            model_supports_tools = True
            if active_model_settings is not None:
                (
                    model_override,
                    overrides,
                ) = await active_model_settings.get_openrouter_overrides()
                if model_override:
                    active_model = model_override
                overrides = dict(overrides) if overrides else {}

            # Refresh attachment URLs before sending to LLM
            conversation_state = await refresh_message_attachments(
                conversation_state,
                self._repo,
                ttl=get_settings().attachment_signed_url_ttl,
            )

            payload = request.to_openrouter_payload(active_model)
            payload["messages"] = _prepare_messages_for_model(conversation_state)

            if overrides:
                provider_overrides = overrides.get("provider")
                if isinstance(provider_overrides, dict):
                    existing_provider = payload.get("provider")
                    if isinstance(existing_provider, dict):
                        # Persisted provider preferences act as defaults.
                        merged_provider = dict(provider_overrides)
                        merged_provider.update(existing_provider)
                        payload["provider"] = merged_provider
                    else:
                        payload["provider"] = dict(provider_overrides)
                for key, value in overrides.items():
                    if key == "provider":
                        continue
                    payload.setdefault(key, value)

            if active_model_settings is not None:
                if hasattr(active_model_settings, "sanitize_payload_for_model"):
                    capability = await active_model_settings.sanitize_payload_for_model(  # type: ignore[attr-defined]
                        active_model,
                        payload,
                        client=self._client,
                    )
                if capability and capability.supports_tools is not None:
                    model_supports_tools = capability.supports_tools
                else:
                    try:
                        model_supports_tools = (
                            await active_model_settings.model_supports_tools(  # type: ignore[misc]
                                client=self._client,  # type: ignore[arg-type]
                            )
                        )
                    except TypeError:
                        model_supports_tools = (
                            await active_model_settings.model_supports_tools()
                        )  # type: ignore[misc]

            if not model_supports_tools and tools_available and not tools_disabled:
                logger.debug(
                    "Skipping tool payload for session %s because active model does not support tool use",
                    session_id,
                )

            allow_tools = (
                tools_available and not tools_disabled and model_supports_tools
            )
            if allow_tools:
                payload["tools"] = active_tools_payload
                payload.setdefault("tool_choice", request.tool_choice or "auto")
            else:
                payload.pop("tools", None)
                payload.pop("tool_choice", None)

            content_builder = _AssistantContentBuilder()

            # Inject pending tool attachments from previous hop into this assistant response
            if pending_tool_attachments:
                # Add minimal source text before images, then inject images
                for attachment_fragment in pending_tool_attachments:
                    # Extract tool source without modifying original
                    tool_source = attachment_fragment.get("_tool_source")
                    if tool_source:
                        # Map tool names to user-friendly source labels
                        source_label = (
                            "Google Drive"
                            if "gdrive" in tool_source.lower()
                            else "tool"
                        )
                        content_builder.add_text(f"Image from {source_label}:")

                    # Add image without _tool_source metadata
                    clean_fragment = {
                        k: v
                        for k, v in attachment_fragment.items()
                        if k != "_tool_source"
                    }
                    content_builder.add_structured([clean_fragment])

                    # Emit attachment as SSE delta for frontend
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {
                                "choices": [
                                    {
                                        "delta": {
                                            "content": [clean_fragment],
                                            "role": "assistant",
                                        },
                                        "index": 0,
                                    }
                                ],
                            }
                        ),
                    }

                pending_tool_attachments.clear()

            streamed_tool_calls: list[dict[str, Any]] = []
            finish_reason: str | None = None
            model_name: str | None = None
            usage_details: dict[str, Any] | None = None
            meta_details: dict[str, Any] | None = None
            generation_id: str | None = None
            reasoning_segments: list[dict[str, Any]] = []
            seen_reasoning: set[tuple[str, str]] = set()
            try:
                async for event in self._client.stream_chat_raw(payload):
                    data = event.get("data")
                    if not data:
                        continue
                    event_name = event.get("event") or "message"

                    if event_name == "openrouter_headers":
                        try:
                            parsed_headers = json.loads(data)
                        except json.JSONDecodeError:
                            logger.debug(
                                "Skipping invalid routing metadata payload: %s", data
                            )
                        else:
                            if isinstance(parsed_headers, dict):
                                routing_headers = parsed_headers
                        continue

                    if event_name != "message":
                        yield event
                        continue

                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        logger.debug("Skipping non-JSON SSE payload: %s", data)
                        continue

                    # DEBUG: Log full chunk to see what OpenRouter sends
                    if (
                        "meta" in chunk
                        or "plugins" in chunk
                        or any("web" in str(k).lower() for k in chunk.keys())
                    ):
                        logger.info(
                            "[WEB-SEARCH-DEBUG] Chunk with web-related keys: %s",
                            json.dumps(chunk, indent=2),
                        )

                    chunk_modified = False
                    http_client_cache: httpx.AsyncClient | None = None

                    choices = chunk.get("choices") or []
                    for choice in choices:
                        delta = choice.get("delta") or {}

                        delta_content = delta.get("content")
                        if isinstance(delta_content, str):
                            logger.debug(
                                "[IMG-GEN] delta content str len=%d preview=%r",
                                len(delta_content),
                                delta_content[:120],
                            )
                            content_builder.add_text(delta_content)
                        elif isinstance(delta_content, list):
                            http_client: httpx.AsyncClient | None = None
                            if hasattr(self._client, "_get_http_client"):
                                if http_client_cache is None:
                                    http_client_cache = (
                                        await self._client._get_http_client()
                                    )
                                http_client = http_client_cache
                            (
                                new_fragments,
                                attachment_ids,
                                mutated,
                            ) = await _normalize_structured_fragments(
                                delta_content,
                                session_id,
                                self._attachment_service,
                                http_client,
                            )
                            if mutated:
                                delta["content"] = new_fragments
                                chunk_modified = True
                            content_builder.add_structured(new_fragments)
                            for attachment_id in attachment_ids:
                                content_builder.register_attachment(attachment_id)

                        delta_images = delta.get("images")
                        if isinstance(delta_images, list) and delta_images:
                            http_client: httpx.AsyncClient | None = None
                            if hasattr(self._client, "_get_http_client"):
                                if http_client_cache is None:
                                    http_client_cache = (
                                        await self._client._get_http_client()
                                    )
                                http_client = http_client_cache
                            (
                                normalized_images,
                                image_attachment_ids,
                                images_mutated,
                            ) = await _normalize_structured_fragments(
                                delta_images,
                                session_id,
                                self._attachment_service,
                                http_client,
                            )
                            if images_mutated:
                                delta["images"] = normalized_images
                                chunk_modified = True
                            content_builder.add_structured(normalized_images)
                            for attachment_id in image_attachment_ids:
                                content_builder.register_attachment(attachment_id)

                        if tool_deltas := delta.get("tool_calls"):
                            _merge_tool_calls(streamed_tool_calls, tool_deltas)

                        choice_finish = choice.get("finish_reason")
                        if choice_finish:
                            finish_reason = choice_finish

                        if "reasoning" in delta:
                            new_segments = _extract_reasoning_segments(
                                delta["reasoning"]
                            )
                            _extend_reasoning_segments(
                                reasoning_segments, new_segments, seen_reasoning
                            )

                    model_value = chunk.get("model")
                    if isinstance(model_value, str):
                        model_name = model_value

                    usage_value = chunk.get("usage")
                    if isinstance(usage_value, dict):
                        usage_details = usage_value

                    meta_value = chunk.get("meta")
                    if isinstance(meta_value, dict):
                        meta_details = meta_value

                    chunk_id = chunk.get("id")
                    if isinstance(chunk_id, str) and chunk_id:
                        generation_id = chunk_id

                    for key in ("reasoning", "message"):
                        if key not in chunk:
                            continue
                        payload_value = chunk[key]
                        if key == "message" and isinstance(payload_value, dict):
                            reasoning_value = payload_value.get("reasoning")
                        else:
                            reasoning_value = payload_value
                        if reasoning_value is not None:
                            new_segments = _extract_reasoning_segments(reasoning_value)
                            _extend_reasoning_segments(
                                reasoning_segments, new_segments, seen_reasoning
                            )

                    message_payload = chunk.get("message")
                    if isinstance(message_payload, dict):
                        message_content = message_payload.get("content")
                        if message_content is not None:
                            logger.debug(
                                "[IMG-GEN] message payload content type=%s",
                                type(message_content).__name__,
                            )
                        if isinstance(message_content, str):
                            logger.debug(
                                "[IMG-GEN] message content str len=%d preview=%r",
                                len(message_content),
                                message_content[:120],
                            )
                            content_builder.add_text(message_content)
                        elif isinstance(message_content, list):
                            http_client: httpx.AsyncClient | None = None
                            if hasattr(self._client, "_get_http_client"):
                                if http_client_cache is None:
                                    http_client_cache = (
                                        await self._client._get_http_client()
                                    )
                                http_client = http_client_cache
                            (
                                new_fragments,
                                attachment_ids,
                                mutated,
                            ) = await _normalize_structured_fragments(
                                message_content,
                                session_id,
                                self._attachment_service,
                                http_client,
                            )
                            if mutated:
                                message_payload["content"] = new_fragments
                                chunk_modified = True
                            content_builder.add_structured(new_fragments)
                            for attachment_id in attachment_ids:
                                content_builder.register_attachment(attachment_id)

                        message_images = message_payload.get("images")
                        if isinstance(message_images, list) and message_images:
                            http_client: httpx.AsyncClient | None = None
                            if hasattr(self._client, "_get_http_client"):
                                if http_client_cache is None:
                                    http_client_cache = (
                                        await self._client._get_http_client()
                                    )
                                http_client = http_client_cache
                            (
                                normalized_images,
                                image_attachment_ids,
                                images_mutated,
                            ) = await _normalize_structured_fragments(
                                message_images,
                                session_id,
                                self._attachment_service,
                                http_client,
                            )
                            if images_mutated:
                                message_payload["images"] = normalized_images
                                chunk_modified = True
                            content_builder.add_structured(normalized_images)
                            for attachment_id in image_attachment_ids:
                                content_builder.register_attachment(attachment_id)

                    if chunk_modified:
                        event["data"] = json.dumps(chunk)

                    yield event
            except OpenRouterError as exc:
                if (
                    allow_tools
                    and can_retry_without_tools
                    and _is_tool_support_error(exc)
                ):
                    logger.info(
                        "Retrying without tools for session %s: %s",
                        session_id,
                        exc.detail,
                    )
                    tools_disabled = True
                    warning_text = (
                        "Tools unavailable for this model; continuing without them."
                    )
                    yield {
                        "event": "tool",
                        "data": json.dumps(
                            {
                                "status": "notice",
                                "name": "system",
                                "message": warning_text,
                            }
                        ),
                    }
                    continue
                raise

            tool_calls = _finalize_tool_calls(streamed_tool_calls)
            if streamed_tool_calls:
                fallback_calls: list[dict[str, Any]] = []
                for index, raw_call in enumerate(streamed_tool_calls):
                    if not isinstance(raw_call, dict):
                        continue
                    raw_function = raw_call.get("function")
                    if not isinstance(raw_function, dict):
                        continue
                    name_value = raw_function.get("name")
                    arguments_value = raw_function.get("arguments")
                    if not (isinstance(name_value, str) and name_value.strip()):
                        continue
                    arguments_str = (
                        arguments_value if isinstance(arguments_value, str) else ""
                    )
                    if arguments_str.strip():
                        continue
                    fallback_calls.append(
                        {
                            "id": raw_call.get("id")
                            or f"call_{len(tool_calls) + index}",
                            "type": raw_call.get("type") or "function",
                            "function": {
                                "name": name_value.strip(),
                                "arguments": arguments_str,
                            },
                        }
                    )
                if fallback_calls:
                    tool_calls.extend(fallback_calls)
            assistant_content = await content_builder.finalize(
                session_id,
                self._attachment_service,
                # Reuse the OpenRouter HTTP client pool for image downloads (if available)
                (await self._client._get_http_client())
                if hasattr(self._client, "_get_http_client")
                else None,
            )
            new_attachment_ids = list(content_builder.created_attachment_ids)

            assistant_turn = AssistantTurn(
                content=assistant_content if assistant_content else None,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                model=model_name,
                usage=usage_details,
                meta=meta_details,
                generation_id=generation_id,
                reasoning=reasoning_segments if reasoning_segments else None,
            )
            metadata: dict[str, Any] = {}
            if assistant_turn.finish_reason is not None:
                metadata["finish_reason"] = assistant_turn.finish_reason
            if assistant_turn.tool_calls:
                metadata["tool_calls"] = assistant_turn.tool_calls
            if assistant_turn.model is not None:
                metadata["model"] = assistant_turn.model
            if assistant_turn.usage is not None:
                metadata["usage"] = assistant_turn.usage
            if assistant_turn.meta is not None:
                metadata["meta"] = assistant_turn.meta
            if assistant_turn.generation_id is not None:
                metadata["generation_id"] = assistant_turn.generation_id
            if assistant_turn.reasoning is not None:
                metadata["reasoning"] = assistant_turn.reasoning
            if routing_headers:
                metadata["routing"] = routing_headers
            if assistant_client_message_id is not None:
                metadata.setdefault("client_message_id", assistant_client_message_id)

            assistant_result = await self._repo.add_message(
                session_id,
                role="assistant",
                content=assistant_turn.content,
                metadata=metadata or None,
                client_message_id=assistant_client_message_id,
                parent_client_message_id=assistant_parent_message_id,
            )
            if isinstance(assistant_result, tuple):
                assistant_record_id, assistant_created_at = assistant_result
            else:
                assistant_record_id = int(assistant_result)
                assistant_created_at = None
            edt_iso, utc_iso = format_timestamp_for_client(assistant_created_at)
            assistant_turn.created_at = edt_iso or assistant_created_at
            assistant_turn.created_at_utc = utc_iso or assistant_created_at
            conversation_state.append(assistant_turn.to_message_dict())
            if new_attachment_ids:
                await self._repo.mark_attachments_used(session_id, new_attachment_ids)

            metadata_event_payload = {
                "role": "assistant",
                "finish_reason": assistant_turn.finish_reason,
                "model": assistant_turn.model,
                "usage": assistant_turn.usage,
                "routing": routing_headers,
                "meta": assistant_turn.meta,
                "generation_id": assistant_turn.generation_id,
                "reasoning": assistant_turn.reasoning,
                "tool_calls": assistant_turn.tool_calls
                if assistant_turn.tool_calls
                else None,
            }
            if assistant_client_message_id is not None:
                metadata_event_payload["client_message_id"] = (
                    assistant_client_message_id
                )
            metadata_event_payload["message_id"] = assistant_record_id
            if assistant_turn.created_at is not None:
                metadata_event_payload["created_at"] = assistant_turn.created_at
            if assistant_turn.created_at_utc is not None:
                metadata_event_payload["created_at_utc"] = assistant_turn.created_at_utc
            yield {
                "event": "metadata",
                "data": json.dumps(metadata_event_payload),
            }
            routing_headers = None

            if not assistant_turn.tool_calls:
                break

            if hop_count >= self._tool_hop_limit:
                pause_message = (
                    f"I've completed {hop_count} steps so far. "
                    "Would you like me to continue?"
                )
                logger.info(
                    "Hop limit (%d) reached for session %s, pausing for user",
                    self._tool_hop_limit,
                    session_id,
                )
                # Save pause message to conversation so LLM sees it on continue
                await self._repo.add_message(
                    session_id,
                    role="assistant",
                    content=pause_message,
                    metadata={"hop_limit_pause": True, "hop_count": hop_count},
                    client_message_id=assistant_client_message_id,
                    parent_client_message_id=assistant_parent_message_id,
                )
                # Stream the pause message using 'tool' event (frontend handles this)
                yield {
                    "event": "tool",
                    "data": json.dumps(
                        {
                            "status": "hop_limit",
                            "name": "system",
                            "message": pause_message,
                            "hop_count": hop_count,
                            "limit": self._tool_hop_limit,
                        }
                    ),
                }
                break

            processed_tool_calls = 0
            stop_due_to_errors = False

            for call_index, tool_call in enumerate(assistant_turn.tool_calls):
                function = tool_call.get("function") or {}
                tool_name = function.get("name")
                tool_id = tool_call.get("id") or f"call_{call_index}"

                if not tool_name:
                    warning_text = (
                        "Tool call missing function name; skipping execution."
                    )
                    logger.warning(warning_text)
                    tool_result = await self._repo.add_message(
                        session_id,
                        role="tool",
                        content=warning_text,
                        tool_call_id=tool_id,
                        metadata={
                            "tool_name": "unknown",
                            "parent_client_message_id": assistant_client_message_id,
                        },
                        parent_client_message_id=assistant_client_message_id,
                    )
                    if isinstance(tool_result, tuple):
                        tool_record_id, tool_created_at = tool_result
                    else:
                        tool_record_id = int(tool_result)
                        tool_created_at = None
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": warning_text,
                    }
                    edt_iso, utc_iso = format_timestamp_for_client(tool_created_at)
                    if edt_iso is not None:
                        tool_message["created_at"] = edt_iso
                    if utc_iso is not None:
                        tool_message["created_at_utc"] = utc_iso
                    conversation_state.append(tool_message)
                    yield {
                        "event": "tool",
                        "data": json.dumps(
                            {
                                "status": "error",
                                "name": "unknown",
                                "call_id": tool_id,
                                "result": warning_text,
                                "message_id": tool_record_id,
                                "created_at": edt_iso or tool_created_at,
                                "created_at_utc": utc_iso or tool_created_at,
                            }
                        ),
                    }
                    continue

                # Rationale validation removed - execute tools regardless

                yield {
                    "event": "tool",
                    "data": json.dumps(
                        {
                            "status": "started",
                            "name": tool_name,
                            "call_id": tool_id,
                        }
                    ),
                }

                arguments_raw = function.get("arguments")
                status = "finished"
                result_text = ""
                result_obj: Any | None = None
                tool_error_flag = False

                # Parse arguments - treat empty/missing as empty dict for no-arg tools
                if not arguments_raw or arguments_raw.strip() == "":
                    arguments = {}
                else:
                    try:
                        arguments = json.loads(arguments_raw)
                    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                        result_text = (
                            f"Invalid JSON arguments for tool {tool_name}: {exc}"
                        )
                        status = "error"
                        logger.warning(
                            "Tool argument parse failure for %s: %s", tool_name, exc
                        )
                        arguments = None

                if arguments is not None:
                    if not isinstance(arguments, dict):
                        result_text = (
                            f"Tool {tool_name} expected a JSON object for arguments but "
                            f"received {type(arguments).__name__}."
                        )
                        status = "error"
                        logger.warning(
                            "Unexpected tool argument type for %s: %s",
                            tool_name,
                            type(arguments).__name__,
                        )
                    else:
                        working_arguments = dict(arguments)
                        if session_id and _tool_requires_session_id(tool_name):
                            working_arguments.setdefault("session_id", session_id)
                        policy_violation = _enforce_tool_policy(
                            tool_name,
                            working_arguments,
                            available_tools=available_tool_names,
                        )
                        if policy_violation:
                            result_text = policy_violation
                            status = "error"
                            tool_error_flag = True
                        else:
                            try:
                                # DEBUG: Log the arguments being sent to the tool
                                logger.info(
                                    "[TOOL-DEBUG] Calling tool '%s' with arguments: %s",
                                    tool_name,
                                    json.dumps(working_arguments, indent=2),
                                )
                                result_obj = await self._tool_client.call_tool(
                                    tool_name, working_arguments
                                )
                                result_text = self._tool_client.format_tool_result(
                                    result_obj
                                )
                                # DEBUG: Log the result from the tool
                                logger.info(
                                    "[TOOL-DEBUG] Tool '%s' returned: %s",
                                    tool_name,
                                    result_text[:500]
                                    if len(result_text) > 500
                                    else result_text,
                                )
                                tool_error_flag = getattr(result_obj, "isError", False)
                                status = "error" if tool_error_flag else "finished"

                                # Memory backup: log conversation when memory tools are used
                                if self._memory_backup_logger is not None:
                                    try:
                                        await self._memory_backup_logger.log_if_memory_tool(
                                            tool_name=tool_name,
                                            session_id=session_id,
                                            conversation=conversation_state,
                                            tool_arguments=working_arguments,
                                            tool_result=result_text,
                                        )
                                    except Exception as backup_exc:
                                        logger.warning(
                                            "Memory backup logging failed: %s",
                                            backup_exc,
                                        )
                            except Exception as exc:  # pragma: no cover - MCP errors
                                logger.exception(
                                    "Tool '%s' raised an exception", tool_name
                                )
                                result_text = f"Tool error: {exc}"
                                status = "error"
                                tool_error_flag = True

                tool_metadata = {
                    "tool_name": tool_name,
                    "parent_client_message_id": assistant_client_message_id,
                }

                tool_record_id, tool_created_at = await self._repo.add_message(
                    session_id,
                    role="tool",
                    content=result_text,
                    tool_call_id=tool_id,
                    metadata=tool_metadata,
                    parent_client_message_id=assistant_client_message_id,
                )

                # Check if result contains attachment references that need conversion
                cleaned_text, attachment_ids = _parse_attachment_references(result_text)

                if attachment_ids:
                    # Convert to multimodal content with image references
                    content_parts: list[dict[str, Any]] = []

                    # Add text part if there's any cleaned text
                    if cleaned_text:
                        content_parts.append({"type": "text", "text": cleaned_text})

                    # Add image parts for each attachment with populated URLs
                    for attachment_id in attachment_ids:
                        # Fetch attachment record to get signed URL
                        try:
                            attachment_record = await self._repo.get_attachment(
                                attachment_id
                            )
                            signed_url = ""
                            attachment_metadata: dict[str, Any] = {
                                "attachment_id": attachment_id
                            }

                            if attachment_record:
                                signed_url = (
                                    attachment_record.get("signed_url")
                                    or attachment_record.get("display_url")
                                    or ""
                                )
                                # Include additional metadata
                                attachment_metadata.update(
                                    {
                                        "mime_type": attachment_record.get("mime_type"),
                                        "size_bytes": attachment_record.get(
                                            "size_bytes"
                                        ),
                                        "display_url": signed_url,
                                        "delivery_url": signed_url,
                                    }
                                )
                                # Add filename from metadata if available
                                record_metadata = attachment_record.get("metadata")
                                if isinstance(record_metadata, dict):
                                    filename = record_metadata.get("filename")
                                    if filename:
                                        attachment_metadata["filename"] = filename

                            attachment_fragment = {
                                "type": "image_url",
                                "image_url": {"url": signed_url},
                                "metadata": attachment_metadata,
                            }
                            content_parts.append(attachment_fragment)

                            # Add to pending attachments for next assistant response
                            # Include tool source for minimal text prefix
                            attachment_with_source = dict(attachment_fragment)
                            attachment_with_source["_tool_source"] = tool_name
                            pending_tool_attachments.append(attachment_with_source)
                        except Exception:  # pragma: no cover - best effort
                            # If we can't fetch the attachment, include placeholder
                            content_parts.append(
                                {
                                    "type": "image_url",
                                    "image_url": {"url": ""},
                                    "metadata": {"attachment_id": attachment_id},
                                }
                            )

                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": content_parts,
                    }
                else:
                    # Plain text result
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": result_text,
                    }

                edt_iso, utc_iso = format_timestamp_for_client(tool_created_at)
                if edt_iso is not None:
                    tool_message["created_at"] = edt_iso
                if utc_iso is not None:
                    tool_message["created_at_utc"] = utc_iso
                conversation_state.append(tool_message)

                # Build SSE event payload - include content for frontend rendering
                tool_event_data: dict[str, Any] = {
                    "status": status,
                    "name": tool_name,
                    "call_id": tool_id,
                    "result": result_text,
                    "message_id": tool_record_id,
                    "created_at": edt_iso or tool_created_at,
                    "created_at_utc": utc_iso or tool_created_at,
                }

                # If there are attachments, include the multimodal content structure
                if attachment_ids:
                    tool_event_data["content"] = content_parts

                yield {
                    "event": "tool",
                    "data": json.dumps(tool_event_data),
                }

                notice_reason = _classify_tool_followup(
                    status,
                    result_text,
                    tool_error_flag=tool_error_flag,
                    missing_arguments=False,
                )
                if notice_reason is not None:
                    notice_payload = {
                        "type": "tool_followup_required",
                        "tool": tool_name or "unknown",
                        "reason": notice_reason,
                        "message": result_text,
                        "attempt": hop_count,
                        "confirmation_required": True,
                    }
                    yield {
                        "event": "notice",
                        "data": json.dumps(notice_payload),
                    }

                processed_tool_calls += 1
                if status == "error":
                    consecutive_tool_errors += 1
                else:
                    consecutive_tool_errors = 0
                if consecutive_tool_errors >= self._tool_error_limit:
                    stop_due_to_errors = True
                    break

            total_tool_calls += processed_tool_calls
            if stop_due_to_errors:
                pause_message = (
                    "Tool calls are failing repeatedly. "
                    "I paused to avoid excessive retries. "
                    "Would you like me to keep trying?"
                )
                logger.info(
                    "Tool error limit (%d) reached for session %s, pausing for user",
                    self._tool_error_limit,
                    session_id,
                )
                await self._repo.add_message(
                    session_id,
                    role="assistant",
                    content=pause_message,
                    metadata={
                        "tool_error_pause": True,
                        "tool_error_count": consecutive_tool_errors,
                        "tool_error_limit": self._tool_error_limit,
                    },
                    client_message_id=assistant_client_message_id,
                    parent_client_message_id=assistant_parent_message_id,
                )
                yield {
                    "event": "tool",
                    "data": json.dumps(
                        {
                            "status": "tool_error_limit",
                            "name": "system",
                            "message": pause_message,
                            "tool_error_count": consecutive_tool_errors,
                            "limit": self._tool_error_limit,
                        }
                    ),
                }
                break

            hop_count += 1

        if self._conversation_logger is not None:
            await self._log_conversation_snapshot(session_id, request)

        yield {"event": "message", "data": "[DONE]"}


__all__ = ["StreamingHandler"]
