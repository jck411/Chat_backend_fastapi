"""Attachment processing helpers used during streaming."""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any, Sequence
from urllib.parse import unquote_to_bytes

import httpx

from ...config import get_settings
from ...services.attachments import AttachmentService
from .messages import deep_copy_jsonable


logger = logging.getLogger(__name__)


TEXT_FRAGMENT_TYPES = {"text", "output_text"}

MIME_EXTENSION_MAP = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/avif": "avif",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/heic": "heic",
    "image/heif": "heif",
}

IMAGE_DATA_KEYS: tuple[str, ...] = (
    "b64_json",
    "image_base64",
    "image_b64",
    "base64",
    "image_bytes",
    "image_data",
)


async def process_assistant_fragment(
    fragment: dict[str, Any],
    session_id: str,
    attachment_service: AttachmentService | None,
    http_client: httpx.AsyncClient | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize assistant fragments and persist any embedded images."""

    meta = fragment.get("metadata")
    if isinstance(meta, dict) and isinstance(meta.get("attachment_id"), str):
        return deep_copy_jsonable(fragment), None

    fragment_type = fragment.get("type")
    normalized_type = (
        fragment_type.lower().strip() if isinstance(fragment_type, str) else ""
    )

    logger.debug(
        "[IMG-GEN] process_assistant_fragment type=%s normalized=%s keys=%s",
        fragment_type,
        normalized_type,
        list(fragment.keys()),
    )

    if normalized_type in TEXT_FRAGMENT_TYPES:
        text_value = fragment.get("text")
        if isinstance(text_value, str):
            return {"type": "text", "text": text_value}, None
        return None, None

    fallback_text = fragment.get("text")
    if not normalized_type and isinstance(fallback_text, str):
        return {"type": "text", "text": fallback_text}, None

    has_image_payload = any(
        key in fragment
        for key in ("image_url", "image", "b64_json", "image_base64", "image_b64")
    )

    if (
        normalized_type in {"image_url", "image"}
        or normalized_type.startswith("image")
        or has_image_payload
    ):
        logger.info(
            "[IMG-GEN] Image fragment detected session=%s type=%s",
            session_id,
            normalized_type,
        )
        processed, attachment_id = await persist_image_fragment(
            fragment,
            session_id,
            attachment_service,
            http_client,
        )
        logger.info(
            "[IMG-GEN] Image fragment processed attachment_id=%s success=%s",
            attachment_id,
            bool(processed),
        )
        return processed, attachment_id

    return deep_copy_jsonable(fragment), None


async def persist_image_fragment(
    fragment: dict[str, Any],
    session_id: str,
    attachment_service: AttachmentService | None,
    http_client: httpx.AsyncClient | None,
) -> tuple[dict[str, Any] | None, str | None]:
    logger.info("[IMG-GEN] === STARTING IMAGE PERSISTENCE ===")
    logger.debug("[IMG-GEN] Fragment keys: %s", list(fragment.keys()))

    (
        image_payload,
        payload_source,
        data_bytes,
        mime_type,
        filename_hint,
    ) = extract_image_payload(fragment)

    if image_payload is not None:
        logger.debug(
            "[IMG-GEN] Selected image payload source=%s keys=%s",
            payload_source or "unknown",
            list(image_payload.keys()),
        )

    if data_bytes is None:
        logger.debug(
            "[IMG-GEN] No inline bytes found in fragment (source=%s)",
            payload_source,
        )
        url_value: str | None = None
        if isinstance(image_payload, dict):
            candidate_url = image_payload.get("url")
            if isinstance(candidate_url, str) and candidate_url.strip():
                url_value = candidate_url.strip()
        if not url_value:
            candidate_url = fragment.get("url")
            if isinstance(candidate_url, str) and candidate_url.strip():
                url_value = candidate_url.strip()

        if url_value and is_http_url(url_value):
            settings = get_settings()
            if is_allowed_host(url_value, settings.image_download_allowed_hosts):
                if http_client is None:
                    logger.warning(
                        "[IMG-GEN] Cannot fetch image URL; no HTTP client available"
                    )
                else:
                    logger.info(
                        "[IMG-GEN] Fetching image from URL: %s (session=%s)",
                        redact_url(url_value),
                        session_id,
                    )
                    try:
                        fetched_bytes, fetched_mime = await download_image(
                            http_client,
                            url_value,
                            timeout_seconds=float(
                                settings.image_download_timeout_seconds
                            ),
                            max_bytes=settings.image_download_max_bytes,
                        )
                    except Exception as exc:  # pragma: no cover - network fallback
                        logger.warning(
                            "[IMG-GEN] Failed to download image from %s: %s",
                            redact_url(url_value),
                            exc,
                        )
                        fetched_bytes = None
                        fetched_mime = None
                    else:
                        data_bytes = fetched_bytes
                        mime_type = mime_type or fetched_mime

    if data_bytes is None:
        logger.debug("[IMG-GEN] No image bytes available; skipping persistence")
        return deep_copy_jsonable(fragment), None

    if mime_type is None:
        mime_type = sniff_mime_from_bytes(data_bytes) or "image/png"

    filename = filename_hint or guess_filename_from_mime(mime_type)

    if attachment_service is None:
        logger.warning(
            "[IMG-GEN] Attachment service unavailable; returning inline fragment"
        )
        return deep_copy_jsonable(fragment), None

    logger.info(
        "[IMG-GEN] Persisting image bytes len=%d mime=%s filename=%s session=%s source=%s",
        len(data_bytes),
        mime_type,
        filename,
        session_id,
        payload_source or "unknown",
    )

    try:
        record = await attachment_service.save_model_image_bytes(
            session_id=session_id,
            data=data_bytes,
            mime_type=mime_type,
            filename_hint=filename,
        )
        logger.info(
            "[IMG-GEN] ✓ SUCCESS: Image saved attachment_id=%s url=%s",
            record.get("attachment_id"),
            record.get("url"),
        )
    except Exception as exc:  # pragma: no cover - persistence safety
        logger.error(
            "[IMG-GEN] ✗ FAILED: Exception while persisting image for session %s: %s",
            session_id,
            exc,
            exc_info=True,
        )
        return deep_copy_jsonable(fragment), None

    metadata = build_attachment_metadata(record)
    original_type = fragment.get("type")
    if isinstance(original_type, str) and original_type:
        metadata.setdefault("source_fragment_type", original_type)

    fragment_payload = {
        "type": "image_url",
        "image_url": {
            "url": record.get("delivery_url") or record.get("signed_url"),
        },
        "metadata": metadata,
    }
    attachment_id = record.get("attachment_id")
    return fragment_payload, attachment_id if isinstance(attachment_id, str) else None


async def normalize_structured_fragments(
    fragments: Sequence[Any],
    session_id: str,
    attachment_service: AttachmentService | None,
    http_client: httpx.AsyncClient | None,
) -> tuple[list[Any], list[str], bool]:
    """Persist image fragments and report whether content mutated."""

    normalized: list[Any] = []
    created_ids: list[str] = []
    mutated = False

    for fragment in fragments:
        if isinstance(fragment, dict):
            processed, attachment_id = await process_assistant_fragment(
                fragment,
                session_id,
                attachment_service,
                http_client,
            )
            if processed is not None:
                normalized.append(processed)
                if processed is not fragment:
                    mutated = True
            else:
                normalized.append(fragment)
            if attachment_id:
                created_ids.append(attachment_id)
        else:
            normalized.append(fragment)

    return normalized, created_ids, mutated


def decode_data_uri(value: str) -> tuple[bytes | None, str | None]:
    if not isinstance(value, str) or not value.startswith("data:"):
        return None, None

    header, _, data_part = value.partition(",")
    if not data_part:
        return None, None

    meta = header[5:]
    if ";" in meta:
        mime, *params = meta.split(";")
    else:
        mime, params = meta, []

    mime_type = mime or "application/octet-stream"
    params_lower = {param.lower() for param in params}
    is_base64 = "base64" in params_lower

    if is_base64:
        cleaned = data_part.strip().replace("\n", "").replace("\r", "")
        data_bytes = safe_b64decode(cleaned)
    else:
        data_bytes = unquote_to_bytes(data_part)

    return data_bytes, mime_type


def safe_b64decode(value: str) -> bytes | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().replace("\n", "").replace("\r", "")
    padding = len(cleaned) % 4
    if padding:
        cleaned += "=" * (4 - padding)
    try:
        return base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError):
        return None


def is_http_url(value: str) -> bool:
    lower = value.lower()
    return lower.startswith("http://") or lower.startswith("https://")


def is_allowed_host(url: str, allowlist: list[str] | None) -> bool:
    """Return True if URL hostname matches the allowlist. Empty allowlist allows all."""

    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return False

    if not allowlist:
        return True

    for allowed in allowlist:
        candidate = allowed.strip().lower()
        if not candidate:
            continue
        if host == candidate or host.endswith("." + candidate):
            return True
    return False


def redact_url(url: str) -> str:
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        return urlunparse(parsed._replace(query="", fragment=""))
    except Exception:
        return url


async def download_image(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
) -> tuple[bytes | None, str | None]:
    """Fetch image bytes with size limit and basic content-type checks."""

    timeout = httpx.Timeout(timeout_seconds, connect=10.0)
    headers = {"Accept": "image/*"}
    async with client.stream("GET", url, timeout=timeout, headers=headers) as resp:
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
        if content_type and not content_type.lower().startswith("image/"):
            logger.debug(
                "[IMG-GEN] Non-image content-type '%s' from %s",
                content_type,
                redact_url(url),
            )

        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(
                    f"Downloaded image exceeds maximum size of {max_bytes} bytes"
                )
            chunks.append(chunk)

        data = b"".join(chunks)
        mime = content_type or sniff_mime_from_bytes(data)
        if not mime or not mime.lower().startswith("image/"):
            raise ValueError("Fetched content is not a valid image")
        return data, mime


def sniff_mime_from_bytes(data: bytes) -> str | None:
    """Guess image mime type from magic bytes for common formats."""

    if not data or len(data) < 12:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    if b"ftypheic" in data[:64] or b"ftypheif" in data[:64]:
        return "image/heic"
    return None


def extract_image_payload(
    fragment: dict[str, Any],
) -> tuple[dict[str, Any] | None, str, bytes | None, str | None, str | None]:
    """Locate and decode inline image bytes within a fragment."""

    candidates: list[tuple[str, dict[str, Any]] | tuple[str, None]] = [
        ("image_url", fragment.get("image_url")),
        ("image", fragment.get("image")),
    ]

    data_field = fragment.get("data")
    if isinstance(data_field, dict):
        candidates.append(("data", data_field))

    candidates.append(("fragment", fragment))

    fragment_mime = coalesce_str(
        fragment.get("mime_type"),
        fragment.get("mimeType"),
    )
    fragment_filename = coalesce_str(
        fragment.get("file_name"),
        fragment.get("filename"),
        fragment.get("name"),
    )

    for source, payload in candidates:
        if not isinstance(payload, dict):
            continue

        mime_type = coalesce_str(
            payload.get("mime_type"),
            payload.get("mimeType"),
            fragment_mime,
        )
        filename_hint = coalesce_str(
            payload.get("file_name"),
            payload.get("filename"),
            payload.get("name"),
            fragment_filename,
        )

        data_bytes = decode_payload_bytes(payload)
        if data_bytes is not None:
            return payload, source, data_bytes, mime_type, filename_hint

    return None, "", None, fragment_mime, fragment_filename


def decode_payload_bytes(payload: dict[str, Any], *, _depth: int = 0) -> bytes | None:
    """Attempt to decode inline bytes from a payload mapping."""

    if _depth > 5:
        logger.debug("[IMG-GEN] Max decode depth reached; aborting nested decode")
        return None

    for key in IMAGE_DATA_KEYS:
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate:
            logger.debug(
                "[IMG-GEN] Attempting base64 decode from key=%s length=%d",
                key,
                len(candidate),
            )
            decoded = safe_b64decode(candidate)
            if decoded is not None:
                logger.info(
                    "[IMG-GEN] ✓ Successfully decoded base64 field '%s': %d bytes",
                    key,
                    len(decoded),
                )
                return decoded

    data_field = payload.get("data")
    if isinstance(data_field, dict):
        nested = decode_payload_bytes(data_field, _depth=_depth + 1)
        if nested is not None:
            return nested
    elif isinstance(data_field, str) and data_field:
        decoded, _ = decode_data_uri(data_field)
        if decoded is not None:
            return decoded
        inline = safe_b64decode(data_field)
        if inline is not None:
            logger.info(
                "[IMG-GEN] ✓ Successfully decoded inline base64 from data field: %d bytes",
                len(inline),
            )
            return inline

    url_value = payload.get("url")
    if isinstance(url_value, str) and url_value:
        data_bytes, _ = decode_data_uri(url_value)
        if data_bytes is not None:
            return data_bytes
        inline = safe_b64decode(url_value)
        if inline is not None:
            logger.info(
                "[IMG-GEN] ✓ Successfully decoded inline base64 from url: %d bytes",
                len(inline),
            )
            return inline

    return None


def coalesce_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            candidate = value.strip()
            if candidate:
                return candidate
    return None


def guess_filename_from_mime(mime_type: str) -> str:
    extension = MIME_EXTENSION_MAP.get(mime_type.lower())
    if not extension:
        return "generated.bin"
    return f"image.{extension}"


def build_attachment_metadata(record: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "attachment_id": record.get("attachment_id"),
        "display_url": record.get("display_url") or record.get("signed_url"),
        "delivery_url": record.get("delivery_url") or record.get("signed_url"),
        "mime_type": record.get("mime_type"),
        "size_bytes": record.get("size_bytes"),
        "session_id": record.get("session_id"),
        "uploaded_at": record.get("created_at"),
        "expires_at": record.get("expires_at"),
        "signed_url_expires_at": record.get("signed_url_expires_at"),
    }

    extra_metadata = record.get("metadata")
    if isinstance(extra_metadata, dict):
        filename = extra_metadata.get("filename")
        if isinstance(filename, str):
            metadata["filename"] = filename

    return {key: value for key, value in metadata.items() if value is not None}


__all__ = [
    "build_attachment_metadata",
    "coalesce_str",
    "decode_data_uri",
    "decode_payload_bytes",
    "download_image",
    "extract_image_payload",
    "guess_filename_from_mime",
    "is_allowed_host",
    "is_http_url",
    "normalize_structured_fragments",
    "persist_image_fragment",
    "process_assistant_fragment",
    "redact_url",
    "safe_b64decode",
    "sniff_mime_from_bytes",
]
