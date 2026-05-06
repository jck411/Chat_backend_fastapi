"""Utilities for working with reasoning payloads in streaming responses."""

from __future__ import annotations

import json
from typing import Any


def extend_reasoning_segments(
    accumulator: list[dict[str, Any]],
    new_segments: Any,
    seen: set[tuple[str, str]],
) -> None:
    """Merge new reasoning segments into the accumulator without duplicates."""

    for segment in new_segments or []:
        if not isinstance(segment, dict):
            continue
        text = segment.get("text")
        if not isinstance(text, str):
            continue
        normalized_text = text.strip()
        if not normalized_text:
            continue
        segment_type = segment.get("type")
        normalized_type = segment_type.strip() if isinstance(segment_type, str) else ""
        key = (normalized_type, normalized_text)
        if key in seen:
            continue
        seen.add(key)
        normalized_segment: dict[str, Any] = {"text": normalized_text}
        if normalized_type:
            normalized_segment["type"] = normalized_type
        accumulator.append(normalized_segment)


def extract_reasoning_segments(payload: Any) -> list[dict[str, Any]]:
    """Normalize varied reasoning payload formats into labeled text segments."""

    segments: list[dict[str, Any]] = []

    def _walk(node: Any, current_type: str | None = None) -> None:
        if node is None:
            return

        if isinstance(node, str):
            text = node.strip()
            if text:
                segment: dict[str, Any] = {"text": text}
                if current_type:
                    segment["type"] = current_type
                segments.append(segment)
            return

        if isinstance(node, (int, float)):
            segment = {"text": str(node)}
            if current_type:
                segment["type"] = current_type
            segments.append(segment)
            return

        if isinstance(node, list):
            for item in node:
                _walk(item, current_type)
            return

        if isinstance(node, dict):
            next_type = node.get("type")
            if isinstance(next_type, str) and next_type.strip():
                normalized_type = next_type.strip()
            else:
                normalized_type = current_type

            extracted = False
            for key in (
                "text",
                "output",
                "content",
                "reasoning",
                "message",
                "details",
                "explanation",
            ):
                if key not in node:
                    continue
                value = node[key]
                if isinstance(value, (str, list, dict, int, float, bool)):
                    _walk(value, normalized_type)
                    extracted = True

            if not extracted:
                remaining = {
                    key: value
                    for key, value in node.items()
                    if key not in {"type", "id", "index"}
                }
                if remaining:
                    try:
                        serialized = json.dumps(remaining, ensure_ascii=False)
                    except TypeError:
                        serialized = str(remaining)
                    if serialized:
                        segment: dict[str, Any] = {"text": serialized}
                        if normalized_type:
                            segment["type"] = normalized_type
                        segments.append(segment)
            return

        segment = {"text": str(node)}
        if current_type:
            segment["type"] = current_type
        segments.append(segment)

    _walk(payload)
    return segments


__all__ = ["extend_reasoning_segments", "extract_reasoning_segments"]

