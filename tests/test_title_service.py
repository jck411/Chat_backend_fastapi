"""Tests for the title generation service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.services.title_service import generate_title


def _make_settings(**overrides):
    """Create a minimal mock Settings object."""
    settings = MagicMock()
    settings.title_model = overrides.get(
        "title_model", "google/gemini-2.0-flash-lite-001"
    )
    settings.openrouter_base_url = overrides.get(
        "openrouter_base_url", "https://openrouter.ai/api/v1"
    )
    secret = MagicMock()
    secret.get_secret_value.return_value = "test-api-key"
    settings.openrouter_api_key = secret
    return settings


def _openrouter_response(title: str) -> dict:
    return {"choices": [{"message": {"content": title}}]}


@pytest.mark.asyncio
async def test_generate_title_happy_path():
    settings = _make_settings()
    messages = [{"role": "user", "content": "How do I bake sourdough bread?"}]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = _openrouter_response(
        "Sourdough Bread Baking Guide"
    )

    with patch("backend.services.title_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await generate_title(settings, messages)

    assert result == "Sourdough Bread Baking Guide"


@pytest.mark.asyncio
async def test_generate_title_empty_messages():
    settings = _make_settings()
    result = await generate_title(settings, [])
    assert result is None


@pytest.mark.asyncio
async def test_generate_title_api_error():
    settings = _make_settings()
    messages = [{"role": "user", "content": "Hello"}]

    with patch("backend.services.title_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await generate_title(settings, messages)

    assert result is None


@pytest.mark.asyncio
async def test_generate_title_absurdly_long_response():
    settings = _make_settings()
    messages = [{"role": "user", "content": "Tell me about the universe"}]

    long_title = "A" * 200
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = _openrouter_response(long_title)

    with patch("backend.services.title_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await generate_title(settings, messages)

    assert result is None


@pytest.mark.asyncio
async def test_generate_title_empty_response():
    settings = _make_settings()
    messages = [{"role": "user", "content": "Hello"}]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = _openrouter_response("   ")

    with patch("backend.services.title_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await generate_title(settings, messages)

    assert result is None
