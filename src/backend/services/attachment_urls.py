"""Helpers for refreshing attachment signed URLs on read paths."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from ..repository import AttachmentRecord, ChatRepository
from .gcs import sign_get_url


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed
    return None


async def ensure_fresh_signed_url(
    record: AttachmentRecord,
    repo: ChatRepository,
    *,
    ttl: timedelta,
) -> AttachmentRecord:
    """Re-sign an attachment URL if missing or expired."""

    attachment_id = record.get("attachment_id")
    if not isinstance(attachment_id, str) or not attachment_id:
        return record

    now = datetime.now(timezone.utc)
    signed_url = record.get("signed_url")
    expires_at = _parse_timestamp(record.get("signed_url_expires_at"))

    needs_refresh = not signed_url
    if expires_at is not None and expires_at <= now:
        needs_refresh = True

    if needs_refresh:
        blob_name = record.get("gcs_blob")
        if not blob_name:
            return record
        refreshed_url = sign_get_url(blob_name, expires_delta=ttl)
        if ttl.total_seconds() > 0:
            refreshed_expiry = now + ttl
        else:
            refreshed_expiry = now
        await repo.update_attachment_signed_url(
            attachment_id,
            signed_url=refreshed_url,
            signed_url_expires_at=refreshed_expiry,
        )
        updated = dict(record)
        updated["signed_url"] = refreshed_url
        updated["signed_url_expires_at"] = refreshed_expiry.isoformat()
        updated["display_url"] = refreshed_url
        updated["delivery_url"] = refreshed_url
        return updated

    if signed_url:
        updated = dict(record)
        updated["display_url"] = signed_url
        updated["delivery_url"] = signed_url
        return updated

    return record


async def refresh_message_attachments(
    messages: list[dict[str, Any]],
    repo: ChatRepository,
    *,
    ttl: timedelta,
) -> list[dict[str, Any]]:
    """Ensure message attachment fragments use valid signed URLs."""

    if not messages:
        return messages

    attachment_refs: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    attachment_ids: list[str] = []

    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for fragment in content:
            if not isinstance(fragment, dict):
                continue
            metadata = fragment.get("metadata")
            if not isinstance(metadata, dict):
                continue
            attachment_id = metadata.get("attachment_id")
            if not isinstance(attachment_id, str) or not attachment_id:
                continue
            attachment_refs.append((fragment, metadata, attachment_id))
            attachment_ids.append(attachment_id)

    if not attachment_ids:
        return messages

    records = await repo.get_attachments_by_ids(attachment_ids)
    if not records:
        return messages

    refreshed: dict[str, AttachmentRecord] = {}
    for attachment_id, record in records.items():
        refreshed_record = await ensure_fresh_signed_url(
            record,
            repo,
            ttl=ttl,
        )
        refreshed[attachment_id] = refreshed_record

    for fragment, metadata, attachment_id in attachment_refs:
        record = refreshed.get(attachment_id)
        if not record:
            continue
        signed_url = record.get("signed_url")
        if not isinstance(signed_url, str) or not signed_url:
            continue
        image_block = fragment.get("image_url")
        if isinstance(image_block, dict):
            image_block["url"] = signed_url
        else:
            fragment["image_url"] = {"url": signed_url}
        metadata["attachment_id"] = attachment_id
        metadata["display_url"] = signed_url
        metadata["delivery_url"] = signed_url
        metadata.setdefault("mime_type", record.get("mime_type"))
        metadata.setdefault("size_bytes", record.get("size_bytes"))
        metadata.setdefault("session_id", record.get("session_id"))
        metadata.setdefault("uploaded_at", record.get("created_at"))
        metadata["expires_at"] = record.get("expires_at")
        metadata["signed_url_expires_at"] = record.get("signed_url_expires_at")

    return messages


__all__ = ["ensure_fresh_signed_url", "refresh_message_attachments"]
