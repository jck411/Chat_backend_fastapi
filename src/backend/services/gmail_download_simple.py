"""
Minimal Gmail attachment downloader that keeps the original filename.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
from email.message import Message
from email.utils import collapse_rfc2231_value
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import unquote as _url_unquote

SMART_QUOTES = {"\u201c": '"', "\u201d": '"'}


def _clean_quotes(value: str) -> str:
    for bad, good in SMART_QUOTES.items():
        value = value.replace(bad, good)
    return value


def _decode_rfc5987(value: str) -> str:
    parts = value.split("'", 2)
    if len(parts) == 3:
        encoding, _lang, text = parts
        encoding = encoding or "utf-8"
        try:
            return _url_unquote(text, encoding=encoding)
        except LookupError:
            return _url_unquote(text)
    return value


def _coerce_param_value(value: Any) -> str:
    if isinstance(value, tuple):
        try:
            return collapse_rfc2231_value(value)
        except (LookupError, TypeError, ValueError):
            parts = [str(part) for part in value if part]
            return "".join(parts)
    return str(value)


def _collect_segments(
    params: Iterable[tuple[str, Any]],
    raw_header: str,
) -> Optional[str]:
    segments: Dict[int, str] = {}
    for key, val in params:
        base, *suffix_parts = key.split("*", 1)
        if base.lower() not in {"filename", "name"}:
            continue
        suffix = suffix_parts[0] if suffix_parts else ""
        cleaned = _coerce_param_value(val).strip('"')
        if suffix:
            suffix = suffix.rstrip("*")
            if suffix.isdigit():
                segments[int(suffix)] = cleaned
                continue
        if cleaned:
            if re.search(r"(?:filename|name)\*\d+\*?=", cleaned, re.IGNORECASE):
                continue
            return cleaned
    if segments:
        combined = "".join(segments[idx] for idx in sorted(segments))
        return combined or None
    # Fallback: scan the raw header for RFC 2231 style continuations even when
    # parameters are not separated cleanly (common in Gmail payloads).
    continuation_pattern = re.compile(
        r"((?:filename|name)\*(\d+)\*?)=([^;]+?)(?=(?:\s*(?:filename|name)\*\d+\*?=)|$)",
        re.IGNORECASE,
    )
    fallback_segments: Dict[int, str] = {}
    for match in continuation_pattern.finditer(raw_header):
        idx = match.group(2)
        try:
            index = int(idx)
        except ValueError:
            continue
        cleaned = match.group(3).strip().strip('"')
        fallback_segments[index] = cleaned
    if fallback_segments:
        combined = "".join(
            fallback_segments[index] for index in sorted(fallback_segments)
        )
        return combined or None
    return None


def _extract_filename_from_headers(headers: Iterable[Dict[str, Any]] | None) -> str | None:
    if not headers:
        return None
    for header in headers:
        name = header.get("name")
        if name not in {"Content-Disposition", "Content-Type"}:
            continue
        value = _clean_quotes(header.get("value") or "")
        msg = Message()
        msg[name] = value
        params = msg.get_params(header=name, failobj=[])
        candidate = _collect_segments(params, value)
        if candidate:
            candidate = _decode_rfc5987(candidate)
            if candidate:
                return candidate
        match = re.search(r'filename\*?="?([^";]+)', value, re.IGNORECASE)
        if match:
            return _decode_rfc5987(match.group(1))
    return None


def extract_filename_from_part(part: Dict[str, Any]) -> str:
    header_name = _extract_filename_from_headers(part.get("headers"))
    if header_name:
        return Path(header_name).name or "attachment"
    raw = (part.get("filename") or "").strip()
    if raw and raw.lower() != "attachment":
        return Path(raw).name or "attachment"
    return "attachment"


def _sanitize_candidate(name: Optional[str]) -> str:
    if not name:
        return ""
    candidate = Path(str(name)).name.strip()
    return candidate or ""


def _pick_best_filename(candidates: Iterable[Optional[str]]) -> str:
    sanitized: list[str] = []
    for candidate in candidates:
        value = _sanitize_candidate(candidate)
        if not value:
            continue
        sanitized.append(value)
        if value.lower() != "attachment":
            return value
    if sanitized:
        return sanitized[0]
    return "attachment"


def _iter_payload_parts(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    if not payload:
        return []
    queue = [payload]
    while queue:
        current = queue.pop(0)
        yield current
        queue.extend(current.get("parts") or [])


def find_attachment_part(payload: Dict[str, Any], attachment_id: str) -> Dict[str, Any] | None:
    for part in _iter_payload_parts(payload):
        body = part.get("body") or {}
        if body.get("attachmentId") == attachment_id:
            return part
    return None


def _normalize_token(token: Optional[str]) -> str:
    if not token:
        return ""
    # Replace common Unicode dashes with ASCII hyphen, trim whitespace
    return (
        str(token)
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .strip()
    )


def find_attachment_part_by_any_id(payload: Dict[str, Any], identifier: Optional[str]) -> Dict[str, Any] | None:
    """Try to locate a part by attachmentId, partId, or Content-ID/X-Attachment-Id.

    - Matches RFC angle-bracketed content IDs as well.
    - Normalizes Unicode dashes when comparing identifiers.
    """
    if not payload:
        return None
    ident = _normalize_token(identifier)
    # First pass: exact attachmentId match
    for part in _iter_payload_parts(payload):
        body = part.get("body") or {}
        if _normalize_token(body.get("attachmentId")) == ident and ident:
            return part
    # Second pass: match partId directly
    for part in _iter_payload_parts(payload):
        if _normalize_token(part.get("partId")) == ident and ident:
            return part
    # Third pass: match Content-ID or X-Attachment-Id headers
    for part in _iter_payload_parts(payload):
        headers = part.get("headers") or []
        for h in headers:
            name = (h.get("name") or "").lower()
            if name in {"content-id", "x-attachment-id"}:
                val = (h.get("value") or "").strip()
                # strip surrounding angle brackets
                if val.startswith("<") and val.endswith(">"):
                    val = val[1:-1].strip()
                if _normalize_token(val) == ident and ident:
                    return part
    return None


async def fetch_gmail_attachment_data(
    service: Any,
    message_id: str,
    attachment_id: str,
) -> Dict[str, Any]:
    message = await asyncio.to_thread(
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute
    )
    payload = message.get("payload", {})
    # Be liberal in how we locate the part: attachmentId, partId, Content-ID
    part = find_attachment_part_by_any_id(payload, attachment_id) or find_attachment_part(payload, attachment_id)

    # Prefer inline body.data if present; otherwise use body's attachmentId
    body = (part or {}).get("body", {}) if part else {}
    data_b64 = body.get("data")
    if not data_b64:
        att_id = body.get("attachmentId") if body else None
        att_id = _normalize_token(att_id) or _normalize_token(attachment_id)
        attachment = await asyncio.to_thread(
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=att_id)
            .execute
        )
        data_b64 = attachment.get("data")
        if not data_b64:
            raise RuntimeError("Attachment contained no data")
    content_bytes = base64.urlsafe_b64decode(data_b64)

    # If we couldn't locate the part earlier, try to match by decoded size
    if part is None and payload:
        try_size = len(content_bytes)
        for p in _iter_payload_parts(payload):
            b = p.get("body") or {}
            if b.get("attachmentId") and int(b.get("size") or -1) == try_size:
                part = p
                break

    # Choose filename and mime after all attempts to resolve part
    candidates: list[Optional[str]] = []
    if part:
        candidates.append(extract_filename_from_part(part))
        candidates.append(part.get("filename"))
        candidates.append(_extract_filename_from_headers(part.get("headers")))
    else:
        candidates.append("attachment")

    filename = _pick_best_filename(candidates)
    mime_type = (
        (part.get("mimeType") or "application/octet-stream").lower()
        if part
        else "application/octet-stream"
    )

    if not Path(filename).suffix:
        guessed = mimetypes.guess_extension(mime_type) or ".bin"
        filename = f"{filename}{guessed}"

    return {
        "filename": Path(filename).name or "attachment.bin",
        "mime_type": mime_type,
        "content_bytes": content_bytes,
        "size_bytes": len(content_bytes),
    }


async def download_gmail_attachment(
    service: Any,
    message_id: str,
    attachment_id: str,
    save_dir: str | Path,
) -> Dict[str, Any]:
    data = await fetch_gmail_attachment_data(service, message_id, attachment_id)
    filename = data["filename"]
    mime_type = data["mime_type"]
    content_bytes = data["content_bytes"]

    base_dir = Path(save_dir).expanduser().resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(filename).name or "attachment"
    stem = Path(safe_name).stem or "attachment"
    suffix = Path(safe_name).suffix
    candidate = base_dir / safe_name
    counter = 1
    while candidate.exists():
        candidate = base_dir / f"{stem}-{counter}{suffix}"
        counter += 1

    await asyncio.to_thread(candidate.write_bytes, content_bytes)

    return {
        "absolute_path": str(candidate),
        "filename": candidate.name,
        "mime_type": mime_type,
        "size_bytes": len(content_bytes),
    }


__all__ = [
    "download_gmail_attachment",
    "fetch_gmail_attachment_data",
    "extract_filename_from_part",
    "find_attachment_part",
]
