"""Tests for per-frontend client tool preferences."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.client_tool_preferences import ClientToolPreferences

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_get_returns_none_for_unknown_client(tmp_path: Path) -> None:
    prefs = ClientToolPreferences(tmp_path / "prefs.json")
    result = await prefs.get_enabled_servers("unknown")
    assert result is None


async def test_set_and_get_roundtrip(tmp_path: Path) -> None:
    prefs = ClientToolPreferences(tmp_path / "prefs.json")
    await prefs.set_enabled_servers("svelte", ["notes", "housekeeping"])
    result = await prefs.get_enabled_servers("svelte")
    assert result == ["notes", "housekeeping"]


async def test_persistence(tmp_path: Path) -> None:
    path = tmp_path / "prefs.json"
    prefs1 = ClientToolPreferences(path)
    await prefs1.set_enabled_servers("cli", ["shell-control"])

    # New instance reads from disk
    prefs2 = ClientToolPreferences(path)
    result = await prefs2.get_enabled_servers("cli")
    assert result == ["shell-control"]


async def test_get_all(tmp_path: Path) -> None:
    prefs = ClientToolPreferences(tmp_path / "prefs.json")
    await prefs.set_enabled_servers("svelte", ["notes"])
    await prefs.set_enabled_servers("voice", ["housekeeping", "notes"])

    all_prefs = await prefs.get_all()
    assert all_prefs == {
        "svelte": ["notes"],
        "voice": ["housekeeping", "notes"],
    }


async def test_file_created_on_write(tmp_path: Path) -> None:
    path = tmp_path / "subdir" / "prefs.json"
    prefs = ClientToolPreferences(path)
    await prefs.set_enabled_servers("kiosk", ["clock"])
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["kiosk"]["enabled_servers"] == ["clock"]


async def test_handles_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "prefs.json"
    path.write_text("not json!!!", encoding="utf-8")
    prefs = ClientToolPreferences(path)
    result = await prefs.get_enabled_servers("svelte")
    assert result is None


async def test_overwrite_previous(tmp_path: Path) -> None:
    prefs = ClientToolPreferences(tmp_path / "prefs.json")
    await prefs.set_enabled_servers("cli", ["a", "b"])
    await prefs.set_enabled_servers("cli", ["c"])
    result = await prefs.get_enabled_servers("cli")
    assert result == ["c"]
