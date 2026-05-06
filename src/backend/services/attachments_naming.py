"""Helpers for constructing attachment blob names."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_filename_pattern = re.compile(r"[^A-Za-z0-9._-]+")


def make_blob_name(session_id: str, attachment_id: str, original_filename: str) -> str:
    """Return a safe blob name scoped by session."""

    safe_name = _filename_pattern.sub("_", original_filename or "").strip("._-")
    if not safe_name:
        safe_name = "file"
    return str(PurePosixPath(session_id) / f"{attachment_id}__{safe_name}")


__all__ = ["make_blob_name"]
