"""Utilities for reasoning about tool execution within streaming."""

from __future__ import annotations

import re
import shlex
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from ...openrouter import OpenRouterError


SESSION_AWARE_TOOL_NAME = "chat_history"
SESSION_AWARE_TOOL_SUFFIX = "__chat_history"

# Tools that require session_id to store attachments or access conversation state
SESSION_AWARE_TOOLS = {
    "chat_history",
    "download_gmail_attachment",
    "read_gmail_attachment_text",
    "extract_gmail_attachment_by_id",
    "gdrive_display_image",
}

_WEB_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
_WEB_FETCH_COMMANDS = {"curl", "wget", "http", "httpie", "lynx", "w3m", "links"}


def _extract_command_name(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()

    if not parts:
        return None

    idx = 0
    while idx < len(parts):
        token = parts[idx]
        if token in {"sudo", "env"}:
            idx += 1
            continue
        if "=" in token and not token.startswith("-"):
            idx += 1
            continue
        return token
    return None


def _extract_remote_urls(text: str) -> list[str]:
    urls = _WEB_URL_RE.findall(text)
    remote: list[str] = []
    for url in urls:
        try:
            parsed = urlparse(url)
        except Exception:
            remote.append(url)
            continue
        host = (parsed.hostname or "").lower()
        if host and host not in _LOCAL_HOSTS:
            remote.append(url)
    return remote


def _browser_tools_available(available_tools: set[str]) -> bool:
    return any(name.startswith("browser_") for name in available_tools)


def enforce_tool_policy(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    *,
    available_tools: set[str],
) -> str | None:
    if tool_name not in {"shell_execute", "shell_session"}:
        return None

    if not _browser_tools_available(available_tools):
        return None

    command = arguments.get("command") if isinstance(arguments, Mapping) else None
    if not isinstance(command, str) or not command.strip():
        return None

    command_name = _extract_command_name(command)
    if command_name not in _WEB_FETCH_COMMANDS:
        return None

    if not _extract_remote_urls(command):
        return None

    return (
        "Blocked web fetch in shell command. Use browser_* tools for web access."
    )


def summarize_tool_parameters(parameters: Mapping[str, Any] | None) -> str:
    if not isinstance(parameters, Mapping):
        return "none"

    required_raw = parameters.get("required")
    required: set[str] = set()
    if isinstance(required_raw, Sequence):
        for item in required_raw:
            if isinstance(item, str) and item.strip():
                required.add(item.strip())

    props = parameters.get("properties")
    names: list[str] = []
    if isinstance(props, Mapping):
        for key in props.keys():
            if not isinstance(key, str):
                continue
            normalized = key.strip()
            if not normalized:
                continue
            if normalized in required:
                names.append(f"{normalized}*")
                required.discard(normalized)
            else:
                names.append(normalized)

    if not names and required:
        for item in sorted(required):
            names.append(f"{item}*")

    if not names:
        return "none"

    return ", ".join(names)


def looks_like_no_result(result_text: str) -> bool:
    if not isinstance(result_text, str):
        return False
    lowered = result_text.strip().lower()
    if not lowered:
        return False
    phrases = (
        "not found",
        "no results",
        "no result",
        "could not find",
        "can't find",
        "cannot find",
        "wasn't found",
        "nothing found",
        "no matching",
        "no events found",
    )
    return any(phrase in lowered for phrase in phrases)


def classify_tool_followup(
    status: str,
    result_text: str | None,
    *,
    tool_error_flag: bool,
    missing_arguments: bool,
) -> str | None:
    """Classify tool results that require follow-up guidance for the assistant."""

    text = result_text if isinstance(result_text, str) else ""
    normalized = text.strip().lower()

    if missing_arguments:
        return "missing_arguments"

    if status == "error":
        if looks_like_no_result(text):
            return "no_results"
        if tool_error_flag or not normalized:
            return "tool_error"
        if "invalid" in normalized and "argument" in normalized:
            return "tool_error"
        return "tool_error"

    if not normalized:
        return "empty_result"

    if looks_like_no_result(text):
        return "no_results"

    return None


def is_tool_support_error(error: OpenRouterError) -> bool:
    detail = error.detail
    message = ""
    if isinstance(detail, dict):
        message = " ".join(
            str(value) for value in detail.values() if isinstance(value, str)
        )
    elif detail is not None:
        message = str(detail)

    message = message.lower()
    return (
        error.status_code == 404
        and "tool" in message
        and "support" in message
        and "tool use" in message
    )


def tool_requires_session_id(tool_name: str) -> bool:
    return (
        tool_name in SESSION_AWARE_TOOLS
        or tool_name.endswith(SESSION_AWARE_TOOL_SUFFIX)
    )


def merge_tool_calls(
    accumulator: list[dict[str, Any]],
    deltas: Any,
) -> None:
    for delta in deltas or []:
        if not isinstance(delta, dict):
            continue

        index = delta.get("index")
        delta_id = delta.get("id")
        if not isinstance(index, int) or index < 0:
            index = None
            if isinstance(delta_id, str):
                for existing_index, existing in enumerate(accumulator):
                    if existing.get("id") == delta_id:
                        index = existing_index
                        break
            if index is None:
                index = len(accumulator)

        while len(accumulator) <= index:
            accumulator.append(
                {
                    "id": None,
                    "type": "function",
                    "function": {"name": None, "arguments": ""},
                    "rationale": "",
                }
            )

        entry = accumulator[index]

        if delta_id:
            entry["id"] = delta_id
        if delta_type := delta.get("type"):
            entry["type"] = delta_type

        rationale_fragment = delta.get("rationale")
        if isinstance(rationale_fragment, str) and rationale_fragment:
            entry.setdefault("rationale", "")
            entry["rationale"] += rationale_fragment

        function_delta = delta.get("function") or {}
        if function_name := function_delta.get("name"):
            entry.setdefault("function", {"name": None, "arguments": ""})
            entry["function"]["name"] = function_name
        if arguments_fragment := function_delta.get("arguments"):
            entry.setdefault("function", {"name": None, "arguments": ""})
            entry["function"]["arguments"] += arguments_fragment
        if rationale_fragment := function_delta.get("rationale"):
            if isinstance(rationale_fragment, str) and rationale_fragment:
                entry.setdefault("rationale", "")
                entry["rationale"] += rationale_fragment


def finalize_tool_calls(
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    finalized: list[dict[str, Any]] = []
    for index, call in enumerate(tool_calls):
        if not isinstance(call, dict):
            continue

        function = call.get("function") or {}
        if not isinstance(function, dict):
            function = {}

        name = function.get("name")
        arguments = function.get("arguments")
        if not (isinstance(name, str) and name.strip()):
            continue
        if not (isinstance(arguments, str) and arguments.strip()):
            continue

        entry = dict(call)
        entry_function = dict(function)
        entry_function["name"] = name
        entry_function["arguments"] = arguments
        entry["function"] = entry_function
        if not entry.get("id"):
            entry["id"] = f"call_{index}"
        entry.pop("rationale", None)
        finalized.append(entry)

    return finalized


__all__ = [
    "SESSION_AWARE_TOOL_NAME",
    "SESSION_AWARE_TOOL_SUFFIX",
    "SESSION_AWARE_TOOLS",
    "classify_tool_followup",
    "enforce_tool_policy",
    "finalize_tool_calls",
    "is_tool_support_error",
    "looks_like_no_result",
    "merge_tool_calls",
    "summarize_tool_parameters",
    "tool_requires_session_id",
]
