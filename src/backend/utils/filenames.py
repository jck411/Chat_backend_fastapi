"""Filename normalization utilities for attachment storage."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def slugify_filename(name: str | None, *, max_length: int = 60) -> str:
    """Return a filesystem-friendly slug derived from the original filename.

    Parameters
    ----------
    name:
        Original filename (may be None or empty).
    max_length:
        Maximum length of the resulting slug (excluding extension). Must be positive.

    Returns
    -------
    str
        Lowercase slug comprised of ASCII letters, numbers, and hyphens. Empty when no
        reasonable slug can be produced.
    """

    if not name:
        return ""

    stem = Path(name).stem
    if not stem:
        return ""

    slug = _NON_ALNUM.sub("-", stem).strip("-")
    if not slug:
        return ""

    slug = slug.lower()
    if max_length > 0 and len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug


def build_storage_name(
    attachment_id: str,
    extension: str,
    original_filename: Optional[str],
) -> str:
    """Construct the stored filename for an attachment.

    The resulting name always starts with the unique ``attachment_id`` so lookups remain
    stable even if the slug changes. When a slug can be derived from the original file
    name, it is appended after a ``__`` separator for readability.
    """

    ext = extension or ""
    slug = slugify_filename(original_filename)
    if slug:
        return f"{attachment_id}__{slug}{ext}"
    return f"{attachment_id}{ext}"


__all__ = ["build_storage_name", "slugify_filename"]

