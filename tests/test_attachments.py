"""Attachment service tests covering uploads, URL refresh, and cleanup."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

import backend.services.attachments as attachments
from backend.services import attachment_urls
from backend.services.attachment_urls import refresh_message_attachments
from backend.services.attachments import AttachmentService
from backend.services.attachments_cleanup import cleanup_expired_attachments
from src.backend.repository import ChatRepository


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def repository(tmp_path):
    repo = ChatRepository(tmp_path / "chat.db")
    await repo.initialize()
    await repo.ensure_session("session-123")
    try:
        yield repo
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_attachment_service_uploads_to_gcs(monkeypatch) -> None:
    repository = MagicMock()
    repository.ensure_session = AsyncMock()
    repository.add_attachment = AsyncMock(
        return_value={
            "attachment_id": "abc123",
            "session_id": "session1",
            "mime_type": "application/pdf",
            "size_bytes": 25,
            "display_url": "https://signed",
            "delivery_url": "https://signed",
            "signed_url": "https://signed",
            "signed_url_expires_at": "2024-01-01T00:00:00Z",
            "created_at": "2024-01-01T00:00:00Z",
            "expires_at": "2024-01-08T00:00:00Z",
            "metadata": {"filename": "report.pdf"},
        }
    )
    repository.mark_attachments_used = AsyncMock()

    uploaded: dict[str, object] = {}

    def fake_upload_bytes(blob_name: str, data: bytes, *, content_type: str) -> None:
        uploaded["blob_name"] = blob_name
        uploaded["data"] = data
        uploaded["content_type"] = content_type

    monkeypatch.setattr(attachments, "upload_bytes", fake_upload_bytes)
    monkeypatch.setattr(
        attachments,
        "sign_get_url",
        lambda name, expires_delta: "https://signed",
    )
    monkeypatch.setattr(
        attachments,
        "uuid4",
        lambda: SimpleNamespace(hex="abc123"),
    )

    service = AttachmentService(
        repository=repository,
        max_size_bytes=10 * 1024 * 1024,
        retention_days=7,
    )

    upload = UploadFile(
        filename="report.pdf",
        file=io.BytesIO(b"%PDF-1.4 sample content"),
        headers=Headers({"content-type": "application/pdf"}),
    )

    result = await service.save_user_upload(
        session_id="session1",
        upload=upload,
    )

    assert result is repository.add_attachment.return_value
    repository.ensure_session.assert_awaited_once_with("session1")

    call_kwargs = repository.add_attachment.call_args.kwargs
    assert call_kwargs["mime_type"] == "application/pdf"
    assert call_kwargs["size_bytes"] == len(b"%PDF-1.4 sample content")
    assert call_kwargs["delivery_url"] == call_kwargs["display_url"]
    assert call_kwargs["signed_url"] == "https://signed"
    assert call_kwargs["gcs_blob"] == call_kwargs["storage_path"]
    assert call_kwargs["storage_path"].startswith("session1/")
    assert call_kwargs["storage_path"].endswith("__report.pdf")
    assert call_kwargs["metadata"]["filename"] == "report.pdf"
    assert uploaded["blob_name"] == call_kwargs["storage_path"]
    assert uploaded["data"] == b"%PDF-1.4 sample content"
    assert uploaded["content_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_save_bytes_persists_attachment(monkeypatch) -> None:
    repository = MagicMock()
    repository.ensure_session = AsyncMock()
    repository.add_attachment = AsyncMock(
        return_value={
            "attachment_id": "xyz",
            "session_id": "session1",
            "mime_type": "text/plain",
            "size_bytes": 5,
            "display_url": "https://signed",
            "delivery_url": "https://signed",
            "signed_url": "https://signed",
            "signed_url_expires_at": "2024-01-01T00:00:00Z",
            "metadata": {"filename": "note.txt"},
        }
    )

    monkeypatch.setattr(
        attachments,
        "upload_bytes",
        lambda blob_name, data, content_type: None,
    )
    monkeypatch.setattr(
        attachments,
        "sign_get_url",
        lambda name, expires_delta: "https://signed",
    )
    monkeypatch.setattr(attachments, "uuid4", lambda: SimpleNamespace(hex="xyz"))

    service = AttachmentService(
        repository=repository,
        max_size_bytes=1024,
        retention_days=7,
    )

    result = await service.save_bytes(
        session_id="session1",
        data=b"hello",
        mime_type="text/plain",
        filename_hint="note.txt",
    )

    assert result is repository.add_attachment.return_value
    repository.ensure_session.assert_awaited_once_with("session1")
    call_kwargs = repository.add_attachment.call_args.kwargs
    assert call_kwargs["mime_type"] == "text/plain"
    assert call_kwargs["size_bytes"] == 5
    assert call_kwargs["metadata"]["filename"] == "note.txt"


# URL refresh tests


@pytest.mark.anyio
async def test_refresh_message_attachments_resigns_expired_url(
    repository: ChatRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    await repository.add_attachment(
        attachment_id="att-1",
        session_id="session-123",
        storage_path="session-123/att-1__image.png",
        mime_type="image/png",
        size_bytes=64,
        display_url="https://old.example/att-1",
        delivery_url="https://old.example/att-1",
        gcs_blob="session-123/att-1__image.png",
        signed_url="https://old.example/att-1",
        signed_url_expires_at=now - timedelta(minutes=5),
    )
    await repository.add_message(
        "session-123",
        role="user",
        content=[
            {
                "type": "image_url",
                "image_url": {"url": "https://old.example/att-1"},
                "metadata": {"attachment_id": "att-1"},
            }
        ],
    )

    conversation = await repository.get_messages("session-123")
    assert conversation and isinstance(conversation[0].get("content"), list)

    monkeypatch.setattr(
        attachment_urls,
        "sign_get_url",
        lambda blob_name, expires_delta: "https://new.example/att-1",
    )

    await refresh_message_attachments(
        conversation,
        repository,
        ttl=timedelta(days=7),
    )

    fragment = conversation[0]["content"][0]
    assert fragment["image_url"]["url"] == "https://new.example/att-1"
    assert fragment["metadata"]["display_url"] == "https://new.example/att-1"
    assert fragment["metadata"]["delivery_url"] == "https://new.example/att-1"

    updated_record = await repository.get_attachment("att-1")
    assert updated_record is not None
    assert updated_record["signed_url"] == "https://new.example/att-1"
    expires_at = updated_record["signed_url_expires_at"]
    assert isinstance(expires_at, str)
    assert "T" in expires_at


@pytest.mark.anyio
async def test_refresh_message_attachments_skips_when_valid(
    repository: ChatRepository,
) -> None:
    now = datetime.now(timezone.utc)
    ttl = timedelta(days=7)
    awaited_expiry = now + ttl
    await repository.add_attachment(
        attachment_id="att-2",
        session_id="session-123",
        storage_path="session-123/att-2__image.png",
        mime_type="image/png",
        size_bytes=64,
        display_url="https://valid.example/att-2",
        delivery_url="https://valid.example/att-2",
        gcs_blob="session-123/att-2__image.png",
        signed_url="https://valid.example/att-2",
        signed_url_expires_at=awaited_expiry,
    )
    await repository.add_message(
        "session-123",
        role="assistant",
        content=[
            {
                "type": "image_url",
                "image_url": {"url": "https://valid.example/att-2"},
                "metadata": {"attachment_id": "att-2"},
            }
        ],
    )

    conversation = await repository.get_messages("session-123")
    await refresh_message_attachments(
        conversation,
        repository,
        ttl=ttl,
    )

    fragment = conversation[0]["content"][0]
    assert fragment["image_url"]["url"] == "https://valid.example/att-2"
    assert fragment["metadata"]["display_url"] == "https://valid.example/att-2"
    assert fragment["metadata"]["delivery_url"] == "https://valid.example/att-2"


# Cleanup tests


@pytest.mark.anyio
async def test_cleanup_expired_attachments(
    repository: ChatRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)

    stale = await repository.add_attachment(
        attachment_id="expired-1",
        session_id="session-123",
        storage_path="session-123/expired-1__file.png",
        mime_type="image/png",
        size_bytes=10,
        display_url="https://example.com/expired",
        delivery_url="https://example.com/expired",
        gcs_blob="session-123/expired-1__file.png",
        signed_url="https://example.com/expired",
        signed_url_expires_at=now - timedelta(days=1),
        expires_at=now - timedelta(days=1),
    )

    await repository.add_attachment(
        attachment_id="active-1",
        session_id="session-123",
        storage_path="session-123/active-1__file.png",
        mime_type="image/png",
        size_bytes=10,
        display_url="https://example.com/active",
        delivery_url="https://example.com/active",
        gcs_blob="session-123/active-1__file.png",
        signed_url="https://example.com/active",
        signed_url_expires_at=now + timedelta(days=1),
        expires_at=now + timedelta(days=1),
    )

    deleted_blobs: list[str] = []

    def fake_delete(blob_name: str) -> None:
        deleted_blobs.append(blob_name)

    monkeypatch.setattr(
        "backend.services.attachments_cleanup.delete_blob",
        fake_delete,
    )

    removed = await cleanup_expired_attachments(
        repository,
        now=now,
    )

    assert removed == 1
    assert stale["gcs_blob"] in deleted_blobs
    assert await repository.get_attachment("expired-1") is None
    assert await repository.get_attachment("active-1") is not None
