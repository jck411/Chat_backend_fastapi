"""Unified datetime parsing and timezone normalization utilities.

This module consolidates all datetime parsing, conversion, and formatting logic
used throughout the backend for consistency and maintainability.
"""

from __future__ import annotations

import datetime
from typing import Optional

from backend.services.time_context import EASTERN_TIMEZONE


class _FallbackParser:
    """Fallback datetime parser when python-dateutil is not available."""

    @staticmethod
    def parse(timestr: str) -> datetime.datetime:
        return datetime.datetime.fromisoformat(timestr.replace("Z", "+00:00"))


try:  # Prefer python-dateutil when available for robust parsing.
    from dateutil import parser as _dateutil_parser  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    _dateutil_parser = None


def _parse(timestr: str) -> datetime.datetime:
    """Parse a datetime string using dateutil if available, otherwise fromisoformat."""
    if _dateutil_parser is not None:
        return _dateutil_parser.parse(timestr)  # type: ignore[no-any-return]
    return _FallbackParser.parse(timestr)


def parse_rfc3339_datetime(value: Optional[str]) -> Optional[datetime.datetime]:
    """Best-effort conversion of an RFC3339 string to an aware datetime in UTC.

    Args:
        value: RFC3339 or ISO 8601 datetime string

    Returns:
        Timezone-aware datetime in UTC, or None if parsing fails
    """
    if not value:
        return None

    try:
        parsed = _parse(value)
    except Exception:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    else:
        parsed = parsed.astimezone(datetime.timezone.utc)

    return parsed


def normalize_rfc3339(dt_value: datetime.datetime) -> str:
    """Return an RFC3339 string in canonical UTC form with 'Z' suffix.

    Args:
        dt_value: Datetime to normalize

    Returns:
        RFC3339 string in UTC ending with 'Z' (e.g., '2025-11-17T13:42:00Z')
    """
    normalized = dt_value.astimezone(datetime.timezone.utc).isoformat()
    if normalized.endswith("+00:00"):
        normalized = normalized[:-6] + "Z"
    return normalized


def parse_time_string(time_str: Optional[str]) -> Optional[str]:
    """Convert keywords like 'today' or 'tomorrow' to RFC3339 timestamps.

    Keywords and date-only strings are rendered as UTC midnight (T00:00:00Z)
    to ensure consistent behavior across different system timezones.

    Supported keywords:
    - today, tomorrow, yesterday
    - next_week, next_month, next_year

    Args:
        time_str: Keyword, date string (YYYY-MM-DD), or ISO datetime string

    Returns:
        RFC3339 timestamp string in UTC, or original string if not parseable
    """
    if not time_str:
        return None

    lowered = time_str.lower()
    today = datetime.date.today()

    if lowered == "today":
        date_obj = today
    elif lowered == "tomorrow":
        date_obj = today + datetime.timedelta(days=1)
    elif lowered == "yesterday":
        date_obj = today - datetime.timedelta(days=1)
    elif lowered == "next_week":
        date_obj = today + datetime.timedelta(days=7)
    elif lowered == "next_month":
        next_month = (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        date_obj = next_month
    elif lowered == "next_year":
        date_obj = today.replace(year=today.year + 1)
    else:
        try:
            date_obj = datetime.date.fromisoformat(time_str)
        except ValueError:
            try:
                dt = datetime.datetime.fromisoformat(time_str)
            except ValueError:
                return time_str
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
                # Extract date in UTC
                date_obj = dt.date()
            else:
                # Extract date in the original timezone before converting to UTC
                # This ensures "today" in EST stays "today" even after UTC conversion
                date_obj = dt.date()
                # Now build UTC midnight for that date
                utc_midnight = datetime.datetime(
                    date_obj.year,
                    date_obj.month,
                    date_obj.day,
                    0,
                    0,
                    0,
                    tzinfo=datetime.timezone.utc,
                )
                return utc_midnight.isoformat().replace("+00:00", "Z")

    # Build a midnight datetime in UTC for consistent behavior
    utc_midnight = datetime.datetime(
        date_obj.year,
        date_obj.month,
        date_obj.day,
        0,
        0,
        0,
        tzinfo=datetime.timezone.utc,
    )
    return utc_midnight.isoformat().replace("+00:00", "Z")


def parse_iso_time_string(time_str: Optional[str]) -> Optional[str]:
    """Normalize ISO-like date/time strings to RFC3339 (UTC) strings.

    This function handles:
    - ISO date-only strings (YYYY-MM-DD) → YYYY-MM-DDT00:00:00Z
    - Naive datetime strings → adds 'Z' suffix
    - Timezone-aware datetime strings → converts to UTC

    Args:
        time_str: ISO date or datetime string

    Returns:
        RFC3339 timestamp string in UTC, or original string if not parseable
    """
    if not time_str:
        return None

    # ISO date-only
    try:
        if len(time_str) == 10 and time_str[4] == "-" and time_str[7] == "-":
            # YYYY-MM-DD
            datetime.date.fromisoformat(time_str)
            return f"{time_str}T00:00:00Z"
    except Exception:
        pass

    # Datetime with no timezone → treat as UTC
    if "T" in time_str and (
        "+" not in time_str and "-" not in time_str[10:] and "Z" not in time_str
    ):
        return time_str + "Z"

    # If timezone is present (and not Z), convert to UTC
    if "T" in time_str and (
        "+" in time_str or ("-" in time_str[10:] and "Z" not in time_str)
    ):
        try:
            # Parse the datetime with timezone and convert to UTC
            dt = datetime.datetime.fromisoformat(time_str)
            if dt.tzinfo is not None:
                dt_utc = dt.astimezone(datetime.timezone.utc)
                return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    return time_str


def normalize_db_timestamp(value: str | None) -> str | None:
    """Convert SQLite timestamp strings to ISO8601 in UTC.

    Args:
        value: SQLite timestamp string

    Returns:
        ISO8601 timestamp in UTC, or original value if parsing fails
    """
    if value is None:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    else:
        parsed = parsed.astimezone(datetime.timezone.utc)
    return parsed.isoformat()


def parse_db_timestamp(value: str | None) -> datetime.datetime | None:
    """Parse a timestamp stored in SQLite and normalize to UTC.

    Args:
        value: SQLite timestamp string

    Returns:
        Timezone-aware datetime in UTC, or None if parsing fails
    """
    if value is None:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    else:
        parsed = parsed.astimezone(datetime.timezone.utc)
    return parsed


def format_timestamp_for_client(value: str | None) -> tuple[str | None, str | None]:
    """Return EDT and UTC ISO strings for a stored timestamp.

    Args:
        value: ISO timestamp string

    Returns:
        Tuple of (edt_iso, utc_iso) or (None, None) if parsing fails
    """
    if value is None:
        return None, None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    else:
        parsed = parsed.astimezone(datetime.timezone.utc)

    edt_iso = parsed.astimezone(EASTERN_TIMEZONE).isoformat()
    return edt_iso, parsed.isoformat()


__all__ = [
    "parse_rfc3339_datetime",
    "normalize_rfc3339",
    "parse_time_string",
    "parse_iso_time_string",
    "normalize_db_timestamp",
    "parse_db_timestamp",
    "format_timestamp_for_client",
]
