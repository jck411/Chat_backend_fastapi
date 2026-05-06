"""Per-frontend tool server preferences.

Each frontend (svelte, kiosk, voice, cli) maintains its own list of
enabled MCP server IDs.  A ``None`` return means "all servers allowed".
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ClientToolPreferences:
    """Manage per-frontend tool server preferences."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Internal I/O
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
                    return self._data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read client tool preferences: %s", exc)
        self._data = {}
        return self._data

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data or {}, indent=2, sort_keys=True)
        self._path.write_text(payload + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_enabled_servers(self, client_id: str) -> list[str] | None:
        """Return server IDs enabled for *client_id*, or ``None`` (= all)."""
        async with self._lock:
            data = self._load()
            entry = data.get(client_id)
            if entry is None:
                return None
            if isinstance(entry, dict):
                servers = entry.get("enabled_servers")
                if isinstance(servers, list):
                    return [s for s in servers if isinstance(s, str)]
            return None

    async def set_enabled_servers(self, client_id: str, server_ids: list[str]) -> None:
        """Set which servers *client_id* may use."""
        async with self._lock:
            data = self._load()
            data[client_id] = {"enabled_servers": server_ids}
            self._data = data
            self._save()

    async def get_all(self) -> dict[str, list[str] | None]:
        """Return preferences for every known client."""
        async with self._lock:
            data = self._load()
            result: dict[str, list[str] | None] = {}
            for client_id, entry in data.items():
                if isinstance(entry, dict):
                    servers = entry.get("enabled_servers")
                    if isinstance(servers, list):
                        result[client_id] = [s for s in servers if isinstance(s, str)]
                        continue
                result[client_id] = None
            return result


__all__ = ["ClientToolPreferences"]
