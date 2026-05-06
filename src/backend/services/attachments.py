"""Attachment storage and metadata management."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import UploadFile

from ..repository import AttachmentRecord, ChatRepository
from .attachments_naming import make_blob_name
from .gcs import delete_blob, sign_get_url, upload_bytes

logger = logging.getLogger(__name__)


ALLOWED_ATTACHMENT_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
        "application/pdf",
    }
)


class AttachmentError(RuntimeError):
    """Base error raised for attachment failures."""


class UnsupportedAttachmentType(AttachmentError):
    """Raised when an unsupported file type is uploaded."""


class AttachmentTooLarge(AttachmentError):
    """Raised when an uploaded file exceeds the configured limit."""


class AttachmentNotFound(AttachmentError):
    """Raised when an attachment record cannot be located."""


class AttachmentService:
    """Persist attachment binaries and metadata for chat sessions."""

    def __init__(
        self,
        repository: ChatRepository,
        *,
        max_size_bytes: int,
        retention_days: int,
    ) -> None:
        self._repo = repository
        self._max_size_bytes = max_size_bytes
        self._retention = timedelta(days=retention_days)

    async def save_user_upload(
        self,
        *,
        session_id: str,
        upload: UploadFile,
    ) -> AttachmentRecord:
        """Validate, persist, and register an uploaded attachment."""

        if not session_id:
            raise AttachmentError("session_id is required")

        mime_type = (upload.content_type or "application/octet-stream").lower()
        if mime_type not in ALLOWED_ATTACHMENT_MIME_TYPES:
            raise UnsupportedAttachmentType(mime_type or "unknown")

        data = await self._read_upload(upload)
        if not data:
            raise AttachmentError("Uploaded file was empty")

        attachment_id = uuid4().hex
        return await self._persist_bytes(
            session_id=session_id,
            attachment_id=attachment_id,
            data=data,
            mime_type=mime_type,
            filename_hint=upload.filename or "file.bin",
        )

    async def save_model_image_bytes(
        self,
        *,
        session_id: str,
        data: bytes,
        mime_type: str,
        filename_hint: str = "image.png",
    ) -> AttachmentRecord:
        """Persist model-generated image bytes and return the stored record."""

        if not session_id:
            raise AttachmentError("session_id is required")

        if len(data) > self._max_size_bytes:
            raise AttachmentTooLarge(
                f"Attachment exceeded {self._max_size_bytes} bytes limit"
            )

        attachment_id = uuid4().hex
        return await self._persist_bytes(
            session_id=session_id,
            attachment_id=attachment_id,
            data=data,
            mime_type=mime_type or "application/octet-stream",
            filename_hint=filename_hint,
        )

    async def save_bytes(
        self,
        *,
        session_id: str,
        data: bytes,
        mime_type: str,
        filename_hint: str,
    ) -> AttachmentRecord:
        """Persist arbitrary bytes provided by external integrations."""

        if not session_id:
            raise AttachmentError("session_id is required")
        if not data:
            raise AttachmentError("Attachment payload was empty")
        if len(data) > self._max_size_bytes:
            raise AttachmentTooLarge(
                f"Attachment exceeded {self._max_size_bytes} bytes limit"
            )

        attachment_id = uuid4().hex
        return await self._persist_bytes(
            session_id=session_id,
            attachment_id=attachment_id,
            data=data,
            mime_type=mime_type or "application/octet-stream",
            filename_hint=filename_hint or "attachment.bin",
        )

    async def touch(self, attachment_ids: list[str], *, session_id: str) -> None:
        """Mark attachments as referenced in a conversation turn."""

        if not attachment_ids:
            return
        await self._repo.mark_attachments_used(session_id, attachment_ids)

    async def delete(self, attachment_id: str) -> bool:
        """Remove attachment metadata and delete the blob if it exists."""

        record = await self._repo.get_attachment(attachment_id)
        deleted = await self._repo.delete_attachment(attachment_id)
        if deleted and record:
            blob_name = record.get("gcs_blob") or record.get("storage_path")
            if blob_name:
                try:
                    delete_blob(str(blob_name))
                except Exception:  # pragma: no cover - best-effort cleanup
                    logger.warning(
                        "Failed to remove attachment blob %s", attachment_id, exc_info=True
                    )
        return deleted

    async def resolve(self, attachment_id: str) -> AttachmentRecord:
        """Direct downloads are no longer supported."""

        raise AttachmentError("Attachments must be accessed via signed URLs")

    async def _persist_bytes(
        self,
        *,
        session_id: str,
        attachment_id: str,
        data: bytes,
        mime_type: str,
        filename_hint: str,
    ) -> AttachmentRecord:
        await self._repo.ensure_session(session_id)

        blob_name = make_blob_name(session_id, attachment_id, filename_hint)
        upload_bytes(blob_name, data, content_type=mime_type)

        expires_delta = self._retention
        signed_url = sign_get_url(blob_name, expires_delta=expires_delta)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        expires_at: datetime | None
        if expires_delta.total_seconds() > 0:
            expires_at = now + expires_delta
        else:
            expires_at = None

        signed_url_expires_at = expires_at or now

        metadata: dict[str, Any] = {
            "mime_type": mime_type,
            "size_bytes": len(data),
        }
        if filename_hint:
            metadata["filename"] = filename_hint

        record = await self._repo.add_attachment(
            attachment_id=attachment_id,
            session_id=session_id,
            storage_path=blob_name,
            gcs_blob=blob_name,
            mime_type=mime_type,
            size_bytes=len(data),
            display_url=signed_url,
            delivery_url=signed_url,
            metadata=metadata or None,
            expires_at=expires_at,
            signed_url=signed_url,
            signed_url_expires_at=signed_url_expires_at,
        )

        logger.info(
            "Stored attachment %s (%s, %d bytes) for session %s",
            attachment_id,
            mime_type,
            len(data),
            session_id,
        )
        return record

    async def _read_upload(self, upload: UploadFile) -> bytes:
        chunk_size = 1024 * 1024  # 1 MiB
        size = 0
        chunks: list[bytes] = []
        try:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                size += len(chunk)
                if size > self._max_size_bytes:
                    raise AttachmentTooLarge(
                        f"Attachment exceeded {self._max_size_bytes} bytes limit"
                    )
                chunks.append(chunk)
        finally:
            await upload.close()
        return b"".join(chunks)


__all__ = [
    "AttachmentService",
    "AttachmentError",
    "UnsupportedAttachmentType",
    "AttachmentTooLarge",
    "AttachmentNotFound",
]
