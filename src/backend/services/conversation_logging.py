"""Utilities for persisting per-session conversation transcripts."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.services.time_context import EASTERN_TIMEZONE


def _parse_iso_datetime(value: str | None) -> datetime | None:
    """Return a timezone-aware datetime for an ISO string."""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


# Pattern to detect memory tools: remember_jack, recall_family, forget_jack, etc.
_MEMORY_TOOL_PATTERN = re.compile(r"^(remember|recall|forget|reflect|memory_stats)_(\w+)$")


def extract_memory_profile(tool_name: str) -> str | None:
    """Extract the profile name from a memory tool name, or None if not a memory tool."""
    match = _MEMORY_TOOL_PATTERN.match(tool_name)
    return match.group(2) if match else None


class MemoryBackupLogger:
    """Log full conversation backups when memory tools are invoked.

    Writes to: {base_dir}/{profile}/{YYYY-MM-DD}/{HH-MM-SS}_{session_id}.json

    This is a disaster recovery mechanism — if the vector database or SQLite
    metadata is lost, these logs contain the full conversation context that
    led to memory operations.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir.resolve()
        self._logged_sessions: set[tuple[str, str, str]] = set()  # (session_id, profile, date)

    async def log_if_memory_tool(
        self,
        tool_name: str,
        session_id: str,
        conversation: list[dict[str, Any]],
        tool_arguments: dict[str, Any] | None = None,
        tool_result: str | None = None,
    ) -> Path | None:
        """Log conversation if tool_name is a memory tool. Returns log path or None."""
        profile = extract_memory_profile(tool_name)
        if not profile:
            return None

        timestamp = datetime.now(timezone.utc)
        local_time = timestamp.astimezone(EASTERN_TIMEZONE)
        local_date = local_time.strftime("%Y-%m-%d")

        # Deduplicate: only log once per session/profile/date
        cache_key = (session_id, profile, local_date)
        if cache_key in self._logged_sessions:
            return None
        self._logged_sessions.add(cache_key)

        # Prune old cache entries (keep last 1000)
        if len(self._logged_sessions) > 1000:
            entries = list(self._logged_sessions)
            self._logged_sessions = set(entries[-500:])

        human_time = local_time.strftime("%H-%M-%S")
        safe_session_id = session_id.replace("/", "_")

        log_path = (
            self._base_dir
            / profile
            / local_date
            / f"{human_time}_{safe_session_id}.json"
        )

        entry = {
            "type": "memory_backup",
            "logged_at": timestamp.isoformat(),
            "session_id": session_id,
            "profile": profile,
            "trigger_tool": tool_name,
            "tool_arguments": tool_arguments,
            "tool_result": tool_result,
            "message_count": len(conversation),
            "conversation": conversation,
        }

        rendered = json.dumps(entry, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self._write_entry, log_path, rendered)
        return log_path

    def _write_entry(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(content)


class ConversationLogWriter:
    """Persist conversation snapshots to timestamped log files."""

    def __init__(self, base_dir: Path, *, min_level: int | None) -> None:
        self._base_dir = base_dir.resolve()
        self._min_level = min_level

    async def write(
        self,
        *,
        session_id: str,
        session_created_at: str | None,
        request_snapshot: dict[str, Any],
        conversation: list[dict[str, Any]],
    ) -> Path | None:
        """Append a structured snapshot for a session if enabled."""

        # Treat the snapshot as an INFO-level event.
        if self._min_level is None or logging.INFO < self._min_level:
            return None

        timestamp = datetime.now(timezone.utc)
        created_at = _parse_iso_datetime(session_created_at) or timestamp
        created_at_utc = created_at.astimezone(timezone.utc)
        safe_session_id = session_id.replace("/", "_")

        entry = {
            "type": "conversation_snapshot",
            "logged_at": timestamp.astimezone(timezone.utc).isoformat(),
            "session_id": session_id,
            "session_created_at": session_created_at,
            "message_count": len(conversation),
            "request": request_snapshot,
            "conversation": conversation,
        }
        rendered_entry = json.dumps(entry, ensure_ascii=False, indent=2)
        local_time = created_at_utc.astimezone(EASTERN_TIMEZONE)
        local_date = local_time.strftime("%Y-%m-%d")
        tz_abbr = local_time.tzname() or "ET"
        human_time = local_time.strftime("%Y-%m-%d_%H-%M-%S")

        log_path = (
            self._base_dir
            / local_date
            / f"session_{human_time}_{tz_abbr}_{safe_session_id}.log"
        )

        delimiter = "=" * 80
        header = timestamp.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        payload = f"{header}\n{delimiter}\n{rendered_entry}\n{delimiter}\n"

        await asyncio.to_thread(self._append_entry, log_path, payload)
        return log_path

    def _append_entry(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)


__all__ = ["ConversationLogWriter", "MemoryBackupLogger", "extract_memory_profile"]
