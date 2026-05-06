"""Background cleanup helpers for attachment retention."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..repository import ChatRepository
from .gcs import delete_blob, is_gcs_available

logger = logging.getLogger(__name__)


async def cleanup_expired_attachments(
    repository: ChatRepository,
    *,
    now: datetime | None = None,
) -> int:
    """Delete expired attachment records and associated blobs."""

    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expired = await repository.find_expired_attachments(now=reference)
    removed = 0

    # Check if GCS is available before attempting deletions
    try:
        gcs_available = is_gcs_available()
    except Exception:  # pragma: no cover - defensive fallback
        logger.debug("Failed to determine GCS availability", exc_info=True)
        gcs_available = False
    if not gcs_available and expired:
        logger.warning(
            "GCS credentials not available. Skipping blob deletion for %d expired attachment(s). "
            "Database records will still be cleaned up.",
            len(expired),
        )

    for record in expired:
        attachment_id = record.get("attachment_id")
        if not isinstance(attachment_id, str) or not attachment_id:
            continue
        blob_name = record.get("gcs_blob") or record.get("storage_path")
        if blob_name:
            try:
                delete_blob(str(blob_name))
            except Exception:  # pragma: no cover - best effort cleanup
                logger.warning(
                    "Failed to delete blob %s for attachment %s",
                    blob_name,
                    attachment_id,
                    exc_info=True,
                )
        if await repository.delete_attachment(attachment_id):
            removed += 1

    if removed:
        logger.info("Cleaned up %d expired attachment(s)", removed)
    return removed


__all__ = ["cleanup_expired_attachments"]
