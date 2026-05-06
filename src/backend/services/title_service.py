"""Lightweight LLM title generation for conversations."""

from __future__ import annotations

import logging

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)

TITLE_SYSTEM_PROMPT = (
    "Generate a concise title (3-8 words) that summarizes the main topic of this conversation. "
    "Return only the title text. No quotes, no punctuation at the end, no extra commentary."
)


async def generate_title(
    settings: Settings, messages: list[dict[str, str]]
) -> str | None:
    """Call OpenRouter with a cheap model to generate a conversation title.

    Returns the title string, or None on failure.
    """
    if not messages:
        return None

    conversation_text = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    payload = {
        "model": settings.title_model,
        "messages": [
            {"role": "system", "content": TITLE_SYSTEM_PROMPT},
            {"role": "user", "content": conversation_text},
        ],
        "max_tokens": 30,
        "temperature": 0.3,
        "stream": False,
    }
    base_url = str(settings.openrouter_base_url).rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0)
        ) as client:
            resp = await client.post(
                f"{base_url}/chat/completions", headers=headers, json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            title = data["choices"][0]["message"]["content"].strip()
            # Sanity check: reject empty or absurdly long titles
            if not title or len(title) > 120:
                return None
            return title
    except Exception:
        logger.exception("Failed to generate AI title")
        return None
