"""Chat streaming API routes."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sse_starlette.sse import EventSourceResponse

from ..chat.orchestrator import ChatOrchestrator
from ..config import Settings, get_settings
from ..openrouter import OpenRouterClient, OpenRouterError
from ..schemas.chat import ChatCompletionRequest

router = APIRouter(prefix="/api", tags=["chat"])


_MODELS_CACHE_TTL_SECONDS = 60
_models_cache: dict[str, Any] | None = None
_models_cache_expiry: float = 0.0
_models_cache_lock: asyncio.Lock = asyncio.Lock()


def get_openrouter_client(
    settings: Settings = Depends(get_settings),
) -> OpenRouterClient:
    return OpenRouterClient(settings)


@router.post("/chat/stream", response_model=None, status_code=200)
async def stream_chat_completions(
    payload: ChatCompletionRequest,
    request: Request,
) -> EventSourceResponse:
    """Stream chat completions from OpenRouter through Server-Sent Events."""

    orchestrator: ChatOrchestrator = request.app.state.chat_orchestrator

    async def event_publisher():
        try:
            async for event in orchestrator.process_stream(payload):
                yield event
        except OpenRouterError as exc:
            detail = (
                exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail)
            )
            error_chunk = {"choices": [{"delta": {"content": f"Error: {detail}"}}]}
            yield {"event": "message", "data": json.dumps(error_chunk)}
            yield {"event": "message", "data": "[DONE]"}
        except Exception as exc:  # pragma: no cover
            error_chunk = {"choices": [{"delta": {"content": f"Error: {str(exc)}"}}]}
            yield {"event": "message", "data": json.dumps(error_chunk)}
            yield {"event": "message", "data": "[DONE]"}

    return EventSourceResponse(event_publisher())


@router.delete("/chat/session/{session_id}", status_code=204)
async def clear_chat_session(
    session_id: str,
    request: Request,
) -> Response:
    """Clear stored conversation state for a session.

    Saved sessions are preserved — only unsaved sessions are deleted.
    """

    orchestrator: ChatOrchestrator = request.app.state.chat_orchestrator
    repo = orchestrator.repository
    meta = await repo.get_session_metadata(session_id)
    if meta and meta.get("saved"):
        # Session is saved — don't delete it, just return success
        return Response(status_code=204)
    await orchestrator.clear_session(session_id)
    return Response(status_code=204)


@router.get("/chat/conversations", status_code=200)
async def list_conversations(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, min_length=1, max_length=200),
) -> dict[str, Any]:
    """List saved conversations, optionally filtered by search term."""

    orchestrator: ChatOrchestrator = request.app.state.chat_orchestrator
    conversations = await orchestrator.repository.list_saved_conversations(
        limit=limit, offset=offset, search=search
    )
    return {"conversations": conversations}


@router.get("/chat/session/{session_id}/messages", status_code=200)
async def get_session_messages(
    session_id: str,
    request: Request,
) -> dict[str, Any]:
    """Load all messages for a saved session."""

    orchestrator: ChatOrchestrator = request.app.state.chat_orchestrator
    repo = orchestrator.repository
    metadata = await repo.get_session_metadata(session_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = await repo.get_messages(session_id)
    return {"session_id": session_id, "metadata": metadata, "messages": messages}


@router.post("/chat/session/{session_id}/save", status_code=200)
async def save_session(
    session_id: str,
    request: Request,
) -> dict[str, Any]:
    """Mark session as saved."""

    orchestrator: ChatOrchestrator = request.app.state.chat_orchestrator
    repo = orchestrator.repository
    exists = await repo.session_exists(session_id)
    if not exists:
        # Session doesn't exist yet — create it so the save flag is stored
        await repo.ensure_session(session_id)

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        pass

    title = body.get("title") if isinstance(body, dict) else None
    if isinstance(title, str):
        title = title.strip() or None
    else:
        title = None

    # Extract LLM settings if provided
    llm_settings = body.get("llm_settings") if isinstance(body, dict) else None
    if llm_settings is not None and not isinstance(llm_settings, dict):
        llm_settings = None

    await repo.save_session(session_id, title=title, llm_settings=llm_settings)
    meta = await repo.get_session_metadata(session_id)
    return {
        "saved": True,
        "session_id": session_id,
        "title": meta.get("title") if meta else title,
        "llm_settings": meta.get("llm_settings") if meta else llm_settings,
    }


@router.post("/chat/session/{session_id}/unsave", status_code=200)
async def unsave_session(
    session_id: str,
    request: Request,
) -> dict[str, Any]:
    """Remove the saved flag from a session."""

    orchestrator: ChatOrchestrator = request.app.state.chat_orchestrator
    updated = await orchestrator.repository.unsave_session(session_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"saved": False, "session_id": session_id}


@router.patch("/chat/session/{session_id}", status_code=200)
async def update_session(
    session_id: str,
    request: Request,
) -> dict[str, Any]:
    """Update session metadata (title and/or llm_settings)."""

    body = await request.json()
    orchestrator: ChatOrchestrator = request.app.state.chat_orchestrator
    repo = orchestrator.repository

    title = body.get("title")
    llm_settings = body.get("llm_settings")

    # At least one field must be provided
    has_title = isinstance(title, str) and title.strip()
    has_llm_settings = isinstance(llm_settings, dict)

    if not has_title and not has_llm_settings:
        raise HTTPException(
            status_code=400, detail="At least title or llm_settings is required"
        )

    result: dict[str, Any] = {"session_id": session_id}

    if has_title:
        updated = await repo.update_session_title(session_id, title.strip())
        if not updated:
            raise HTTPException(status_code=404, detail="Session not found")
        result["title"] = title.strip()

    if has_llm_settings:
        updated = await repo.update_session_llm_settings(session_id, llm_settings)
        if not updated:
            raise HTTPException(status_code=404, detail="Session not found")
        result["llm_settings"] = llm_settings

    return result


@router.delete("/chat/conversations/{session_id}", status_code=204)
async def delete_saved_conversation(
    session_id: str,
    request: Request,
) -> Response:
    """Permanently delete a saved conversation."""

    orchestrator: ChatOrchestrator = request.app.state.chat_orchestrator
    deleted = await orchestrator.repository.delete_saved_conversation(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@router.post("/chat/session/{session_id}/generate-title", status_code=200)
async def generate_session_title(
    session_id: str,
    request: Request,
) -> dict[str, Any]:
    """Generate an AI title for a conversation using a cheap LLM model."""

    orchestrator: ChatOrchestrator = request.app.state.chat_orchestrator
    repo = orchestrator.repository
    messages = await repo.get_session_messages_for_title(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="No messages found")

    from ..services.title_service import generate_title

    title = await generate_title(orchestrator._settings, messages)
    if title:
        await repo.update_session_ai_title(session_id, title)
        return {"session_id": session_id, "title": title, "title_source": "ai"}

    # Fallback: return existing title
    conv = await repo.get_conversation_metadata(session_id)
    return {
        "session_id": session_id,
        "title": conv.get("title") if conv else None,
        "title_source": conv.get("title_source", "auto") if conv else "auto",
        "generated": False,
    }


@router.delete(
    "/chat/session/{session_id}/messages/{client_message_id}", status_code=204
)
async def delete_chat_message(
    session_id: str,
    client_message_id: str,
    request: Request,
) -> Response:
    """Delete a single message (and related tool outputs) within a session."""

    orchestrator: ChatOrchestrator = request.app.state.chat_orchestrator
    deleted = await orchestrator.delete_message(session_id, client_message_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Message not found")
    return Response(status_code=204)


@router.get("/chat/test-stream", response_model=None, status_code=200)
async def test_stream() -> EventSourceResponse:
    """Emit a short fake SSE chat stream for debugging the frontend."""

    async def generator():
        parts = ["Hello ", "from ", "server!"]
        for part in parts:
            chunk = {"choices": [{"delta": {"content": part}}]}
            yield {"event": "message", "data": json.dumps(chunk)}
            await asyncio.sleep(0.2)
        yield {"event": "message", "data": "[DONE]"}

    return EventSourceResponse(generator())


@router.get("/chat/generation/{generation_id}", status_code=200)
async def get_generation_details(
    generation_id: str,
    client: OpenRouterClient = Depends(get_openrouter_client),
) -> dict[str, Any]:
    """Return detailed usage and cost information for a generation."""

    try:
        return await client.get_generation(generation_id)
    except OpenRouterError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _get_models_payload(client: OpenRouterClient) -> dict[str, Any]:
    global _models_cache, _models_cache_expiry, _models_cache_lock

    now = time.monotonic()
    if _models_cache is not None and now < _models_cache_expiry:
        return _models_cache

    async with _models_cache_lock:
        if _models_cache is not None and now < _models_cache_expiry:
            return _models_cache

        payload = await client.list_models()
        _models_cache = payload
        _models_cache_expiry = now + _MODELS_CACHE_TTL_SECONDS
        return payload


def _invalidate_models_cache() -> None:
    """Reset the cached OpenRouter model payload."""

    global _models_cache, _models_cache_expiry
    _models_cache = None
    _models_cache_expiry = 0.0


@router.get("/models", status_code=200)
async def list_models(
    tools_only: bool = Query(
        False,
        alias="tools_only",
        description="Return only models that allow tool use.",
    ),
    search: str | None = Query(
        None,
        alias="search",
        description="Case-insensitive substring search applied across all model fields.",
    ),
    filters: str | None = Query(
        None,
        alias="filters",
        description=(
            "JSON-encoded mapping of dotted property paths to filter criteria. "
            'Example: {"pricing.prompt": {"max": 0.002}}'
        ),
    ),
    client: OpenRouterClient = Depends(get_openrouter_client),
) -> dict[str, Any]:
    """Expose the available OpenRouter models to the frontend."""

    try:
        payload = await _get_models_payload(client)
    except OpenRouterError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    annotated = _annotate_and_enrich_models(
        payload,
    )

    parsed_filters = _parse_filter_query(filters)

    data = annotated.get("data")
    if not isinstance(data, list):
        return annotated

    base_models = _apply_tools_filter(data, tools_only)

    filtered_models = _apply_search_and_filters(
        base_models,
        search=search,
        filters=parsed_filters,
    )

    response_payload = dict(annotated)
    response_payload["data"] = filtered_models
    response_payload["metadata"] = {
        "total": len(data),
        "base_count": len(base_models),
        "count": len(filtered_models),
    }
    return response_payload


@router.get("/models/metadata", status_code=200)
async def get_models_metadata(
    client: OpenRouterClient = Depends(get_openrouter_client),
) -> dict[str, Any]:
    try:
        payload = await _get_models_payload(client)
    except OpenRouterError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    annotated = _annotate_and_enrich_models(payload)
    data = annotated.get("data")
    if not isinstance(data, list):
        return {
            "total": 0,
            "base_count": 0,
            "properties": [],
            "facets": _build_faceted_metadata([]),
        }

    base_models = _apply_tools_filter(data, tools_only=False)
    return {
        "total": len(data),
        "base_count": len(base_models),
        "properties": _build_model_metadata(base_models),
        "facets": _build_faceted_metadata(base_models),
    }


def _annotate_and_enrich_models(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, list):
        return payload

    filtered_payload = dict(payload)
    filtered_payload["data"] = []

    for item in data:
        if isinstance(item, dict):
            annotated = _enrich_model(item)
            filtered_payload["data"].append(annotated)
        else:
            filtered_payload["data"].append(item)

    return filtered_payload


def _apply_tools_filter(models: list[Any], tools_only: bool) -> list[Any]:
    if not tools_only:
        return [dict(item) if isinstance(item, dict) else item for item in models]

    filtered: list[Any] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        if item.get("supports_tools") is True:
            filtered.append(dict(item))
    return filtered


def _model_supports_tools(model: dict[str, Any]) -> bool:
    capabilities = model.get("capabilities")
    if isinstance(capabilities, dict):
        for key in (
            "tools",
            "functions",
            "function_calling",
            "tool_choice",
            "tool_calls",
        ):
            if _is_truthy(capabilities.get(key)):
                return True

    for key in ("tools", "functions", "supports_tools", "supports_functions"):
        if _is_truthy(model.get(key)):
            return True

    supported_parameters = model.get("supported_parameters")
    if isinstance(supported_parameters, (list, tuple, set)):
        normalized = {str(param).strip().lower() for param in supported_parameters}
        for key in (
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "functions",
            "function_calling",
        ):
            if key in normalized:
                return True

    return False


def _enrich_model(item: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)

    supports_tools = _model_supports_tools(item)
    enriched["supports_tools"] = supports_tools

    architecture = item.get("architecture")
    input_modalities = []
    output_modalities = []
    if isinstance(architecture, dict):
        if isinstance(architecture.get("input_modalities"), list):
            input_modalities = [
                str(mod).lower() for mod in architecture["input_modalities"] if mod
            ]
        if isinstance(architecture.get("output_modalities"), list):
            output_modalities = [
                str(mod).lower() for mod in architecture["output_modalities"] if mod
            ]

    enriched["input_modalities"] = sorted({mod for mod in input_modalities})
    enriched["output_modalities"] = sorted({mod for mod in output_modalities})

    context_length = item.get("context_length")
    if isinstance(context_length, (int, float)):
        enriched["context_length"] = int(context_length)

    prompt_price = _extract_price(item)
    if prompt_price is not None:
        enriched["prompt_price"] = prompt_price
        enriched["prompt_price_per_million"] = prompt_price * 1_000_000

    supported_parameters = item.get("supported_parameters")
    if isinstance(supported_parameters, list):
        trimmed: list[str] = []
        normalized_values: set[str] = set()
        for param in supported_parameters:
            if param is None:
                continue
            text = str(param).strip()
            if not text:
                continue
            trimmed.append(text)
            normalized_param = _normalize_supported_parameter(text)
            if normalized_param:
                normalized_values.add(normalized_param)
        enriched["supported_parameters"] = trimmed
        enriched["supported_parameters_normalized"] = sorted(normalized_values)

    series = _classify_series(item)
    enriched["series"] = series
    enriched["series_normalized"] = sorted(
        {_canonicalize_token(label) for label in series if isinstance(label, str)}
    )
    enriched["provider_prefix"] = _provider_prefix(item)

    return enriched


def _extract_price(item: dict[str, Any]) -> float | None:
    pricing = item.get("pricing")
    if not isinstance(pricing, dict):
        return None
    prompt_price = pricing.get("prompt")
    return _to_number(prompt_price)


def _provider_prefix(item: dict[str, Any]) -> str | None:
    ident = item.get("id")
    if not isinstance(ident, str):
        return None
    if "/" in ident:
        return ident.split("/", 1)[0]
    return ident


def _canonicalize_token(value: str) -> str:
    return value.strip().lower()


def _classify_series(item: dict[str, Any]) -> list[str]:
    ident = item.get("id")
    name = item.get("name")

    candidates: set[str] = set()

    if isinstance(ident, str):
        ident = ident.strip()
        if ident:
            candidates.add(ident)
            if "/" in ident:
                prefix, suffix = ident.split("/", 1)
                if prefix:
                    candidates.add(prefix)
                if suffix:
                    candidates.add(suffix)
            tokens = _tokenize_series_string(ident)
            candidates.update(tokens)

    prefix = _provider_prefix(item)
    if isinstance(prefix, str):
        prefix = prefix.strip()
    if prefix:
        candidates.add(prefix)
        aliases = _PROVIDER_SERIES_ALIASES.get(prefix.lower())
        if aliases:
            candidates.update(aliases)

    if isinstance(name, str):
        tokens = _tokenize_series_string(name)
        candidates.update(tokens)

    candidates.add("Other")

    return sorted({value for value in candidates if value})


_PROVIDER_SERIES_ALIASES: dict[str, set[str]] = {
    "openai": {"gpt"},
    "anthropic": {"claude"},
    "google": {"gemini", "PaLM", "pam"},
    "x-ai": {"grok"},
    "cohere": {"cohere"},
    "amazon": {"nova"},
    "perplexity": {"router"},
    "openrouter": {"router"},
    "mistralai": {"mistral"},
    "deepseek": {"deepseek"},
    "yi": {"yi"},
    "01-ai": {"yi"},
    "meta-llama": {"llama", "llama2", "llama3", "llama4"},
    "meta": {"llama"},
    "qwen": {"qwen"},
    "microsoft": {"other"},
    "nvidia": {"other"},
}


_SERIES_TOKEN_PATTERN = re.compile(r"[\s/_-]+")


_SUPPORTED_PARAMETER_ALIASES: dict[str, str] = {}


def _normalize_supported_parameter(value: str) -> str | None:
    token = _canonicalize_token(value)
    if not token:
        return None
    return _SUPPORTED_PARAMETER_ALIASES.get(token, token)


def _tokenize_series_string(value: str) -> set[str]:
    chunks = {
        chunk.strip() for chunk in _SERIES_TOKEN_PATTERN.split(value) if chunk.strip()
    }
    return chunks


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        return lowered not in {"", "false", "0", "none", "null", "no", "disabled"}
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _parse_filter_query(raw_filters: str | None) -> dict[str, Any]:
    if raw_filters is None or raw_filters == "":
        return {}

    try:
        parsed = json.loads(raw_filters)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=400,
            detail=f"Invalid filters parameter: {exc.msg}",
        ) from exc

    if not isinstance(parsed, dict):  # pragma: no cover - defensive
        raise HTTPException(
            status_code=400,
            detail="Filters parameter must be a JSON object.",
        )

    return parsed


def _apply_search_and_filters(
    models: list[Any],
    *,
    search: str | None,
    filters: dict[str, Any],
) -> list[Any]:
    if not models:
        return []

    normalized_query = _normalize_search_query(search)
    filtered: list[Any] = []

    for item in models:
        if not isinstance(item, dict):
            continue

        if normalized_query and not _matches_search(item, normalized_query):
            continue

        if filters and not _matches_filters(item, filters):
            continue

        filtered.append(dict(item))

    return filtered


def _normalize_search_query(query: str | None) -> list[str]:
    if not query:
        return []
    tokens = [token for token in query.split() if token.strip()]
    return [_canonicalize_token(token) for token in tokens]


def _matches_search(model: dict[str, Any], tokens: list[str]) -> bool:
    if not tokens:
        return True

    haystack = list(_iterate_values(model))
    if not haystack:
        return False

    for token in tokens:
        token_found = any(token in value for value in haystack)
        if not token_found:
            return False
    return True


def _iterate_values(value: Any) -> list[str]:
    results: list[str] = []

    def _walk(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, dict):
            for sub in node.values():
                _walk(sub)
            return
        if isinstance(node, list):
            for sub in node:
                _walk(sub)
            return
        text = str(node).strip().lower()
        if text:
            results.append(text)

    _walk(value)
    return results


def _matches_filters(model: dict[str, Any], filters: dict[str, Any]) -> bool:
    for path, raw_criterion in filters.items():
        path_parts = [part for part in str(path).split(".") if part]
        values = _resolve_path(model, path_parts)
        if not _match_values(values, raw_criterion):
            return False
    return True


def _resolve_path(subject: Any, parts: list[str]) -> list[Any]:
    if not parts:
        if isinstance(subject, list):
            return [item for item in subject]
        return [subject]

    head, *rest = parts
    resolved: list[Any] = []

    if isinstance(subject, dict):
        if head in subject:
            resolved.extend(_resolve_path(subject[head], rest))
        return resolved

    if isinstance(subject, list):
        for element in subject:
            resolved.extend(_resolve_path(element, parts))
        return resolved

    return []


def _match_values(values: list[Any], criterion: Any) -> bool:
    if not values:
        values = []

    if isinstance(criterion, dict):
        return _match_mapping(values, criterion)

    if isinstance(criterion, list):
        allowed = {_normalize_value(item) for item in criterion}
        return any(_normalize_value(value) in allowed for value in values)

    expected = _normalize_value(criterion)
    return any(_normalize_value(value) == expected for value in values)


def _match_mapping(values: list[Any], mapping: dict[str, Any]) -> bool:
    normalized_values = [_normalize_value(value) for value in values]
    numeric_values = [
        _to_number(value) for value in values if _to_number(value) is not None
    ]
    text_values = [str(value).lower() for value in values if value is not None]

    for key, expected in mapping.items():
        if key in {"eq", "equals"}:
            needle = _normalize_value(expected)
            if not any(value == needle for value in normalized_values):
                return False
        elif key in {"neq", "not"}:
            if isinstance(expected, list):
                # Support array negation: return False if ANY of the excluded values are found
                excluded = {_normalize_value(item) for item in expected}
                if any(value in excluded for value in normalized_values):
                    return False
            else:
                # Single value negation (original behavior)
                needle = _normalize_value(expected)
                if any(value == needle for value in normalized_values):
                    return False
        elif key in {"contains", "substring"}:
            if expected is None:
                return False
            needle = str(expected).lower()
            if not any(needle in value for value in text_values):
                return False
        elif key in {"in", "one_of"}:
            if not isinstance(expected, list):
                return False
            allowed = {_normalize_value(item) for item in expected}
            if not any(value in allowed for value in normalized_values):
                return False
        elif key in {"min", "gte"}:
            threshold = _to_number(expected)
            if threshold is None:
                return False
            if not numeric_values or not any(
                value is not None and value >= threshold for value in numeric_values
            ):
                return False
        elif key in {"max", "lte"}:
            threshold = _to_number(expected)
            if threshold is None:
                return False
            if not numeric_values or not any(
                value is not None and value <= threshold for value in numeric_values
            ):
                return False
        elif key == "exists":
            flag = bool(expected)
            if flag and not values:
                return False
            if not flag and values:
                return False
        else:
            return False

    return True


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        token = _canonicalize_token(value)
        return _SUPPORTED_PARAMETER_ALIASES.get(token, token)
    return value


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _build_model_metadata(models: list[Any]) -> list[dict[str, Any]]:
    accumulator: dict[str, dict[str, Any]] = {}

    for item in models:
        if isinstance(item, dict):
            _collect_metadata(item, (), accumulator)

    entries: list[dict[str, Any]] = []
    for path, info in sorted(accumulator.items()):
        entry: dict[str, Any] = {
            "path": path,
            "count": info["count"],
            "types": sorted(info["types"], key=lambda value: str(value)),
        }

        examples = _sorted_examples(info["examples"])
        if examples:
            entry["examples"] = examples[:5]

        boolean_values = sorted(info["boolean_values"])
        if boolean_values:
            entry["boolean_values"] = boolean_values

        if info.get("min") is not None:
            entry["min"] = info["min"]
        if info.get("max") is not None:
            entry["max"] = info["max"]

        if info["item_types"]:
            entry["item_types"] = sorted(
                info["item_types"], key=lambda value: str(value)
            )

        entries.append(entry)

    return entries


def _build_faceted_metadata(models: list[Any]) -> dict[str, Any]:
    facets: dict[str, Any] = {
        "input_modalities": set(),
        "output_modalities": set(),
        "supported_parameters": set(),
        "series": set(),
        "series_normalized": set(),
    }
    min_context: int | None = None
    max_context: int | None = None
    min_price: float | None = None
    max_price: float | None = None

    for item in models:
        if not isinstance(item, dict):
            continue

        for value in item.get("input_modalities", []):
            facets["input_modalities"].add(str(value))
        for value in item.get("output_modalities", []):
            facets["output_modalities"].add(str(value))
        for value in item.get("supported_parameters_normalized", []):
            facets["supported_parameters"].add(str(value))
        for value in item.get("series", []):
            facets["series"].add(str(value))
        for value in item.get("series_normalized", []):
            facets["series_normalized"].add(str(value))

        context_length = item.get("context_length")
        if isinstance(context_length, int):
            min_context = (
                context_length
                if min_context is None
                else min(min_context, context_length)
            )
            max_context = (
                context_length
                if max_context is None
                else max(max_context, context_length)
            )

        price = item.get("prompt_price_per_million")
        if isinstance(price, (int, float)):
            min_price = price if min_price is None else min(min_price, price)
            max_price = price if max_price is None else max(max_price, price)

    return {
        "input_modalities": sorted(facets["input_modalities"]),
        "output_modalities": sorted(facets["output_modalities"]),
        "supported_parameters": sorted(facets["supported_parameters"]),
        "series": sorted(facets["series"]),
        "series_normalized": sorted(facets["series_normalized"]),
        "context_length": {
            "min": min_context,
            "max": max_context,
        },
        "prompt_price_per_million": {
            "min": min_price,
            "max": max_price,
        },
    }


def _collect_metadata(
    value: Any,
    path: tuple[str, ...],
    accumulator: dict[str, dict[str, Any]],
) -> None:
    if not path:
        if isinstance(value, dict):
            for key, sub_value in value.items():
                _collect_metadata(sub_value, (str(key),), accumulator)
        return

    key = ".".join(path)
    entry = accumulator.setdefault(
        key,
        {
            "count": 0,
            "types": set(),
            "examples": set(),
            "boolean_values": set(),
            "min": None,
            "max": None,
            "item_types": set(),
        },
    )

    entry["count"] += 1
    kind = _value_kind(value)
    entry["types"].add(kind)

    if kind == "boolean":
        entry["boolean_values"].add(bool(value))
    elif kind == "number":
        number = float(value)
        entry["examples"].add(number)
        entry["min"] = number if entry["min"] is None else min(entry["min"], number)
        entry["max"] = number if entry["max"] is None else max(entry["max"], number)
    elif kind == "string":
        entry["examples"].add(str(value))
    elif kind == "array":
        entry["item_types"].update(_value_kind(item) for item in value)
    elif kind == "object":
        entry["examples"].add(json.dumps(value, sort_keys=True))

    if isinstance(value, dict):
        for key_name, sub_value in value.items():
            _collect_metadata(sub_value, (*path, str(key_name)), accumulator)
    elif isinstance(value, list):
        for sub_value in value:
            _collect_metadata(sub_value, path, accumulator)


def _sorted_examples(examples: set[Any]) -> list[Any]:
    def _key(value: Any) -> tuple[int, str]:
        if isinstance(value, (int, float)):
            return (0, f"{float(value):.12g}")
        if isinstance(value, bool):
            return (1, str(value).lower())
        return (2, str(value))

    return sorted(examples, key=_key)


def _value_kind(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


__all__ = ["router"]
