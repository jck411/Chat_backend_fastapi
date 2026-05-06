"""Tests for the unified datetime_utils module."""

from __future__ import annotations

import datetime

import pytest

from backend.utils.datetime_utils import (
    format_timestamp_for_client,
    normalize_db_timestamp,
    normalize_rfc3339,
    parse_db_timestamp,
    parse_iso_time_string,
    parse_rfc3339_datetime,
    parse_time_string,
)


class TestParseRFC3339Datetime:
    """Tests for parse_rfc3339_datetime function."""

    def test_none_input(self):
        """None input returns None."""
        assert parse_rfc3339_datetime(None) is None

    def test_empty_string(self):
        """Empty string returns None."""
        assert parse_rfc3339_datetime("") is None

    def test_rfc3339_with_z_suffix(self):
        """Parse RFC3339 with Z suffix."""
        result = parse_rfc3339_datetime("2025-11-17T13:42:00Z")
        assert result is not None
        assert result.tzinfo == datetime.timezone.utc
        assert result.year == 2025
        assert result.month == 11
        assert result.day == 17

    def test_rfc3339_with_offset(self):
        """Parse RFC3339 with timezone offset."""
        result = parse_rfc3339_datetime("2025-11-17T13:42:00-05:00")
        assert result is not None
        assert result.tzinfo == datetime.timezone.utc
        # Convert -05:00 to UTC: 13:42 - (-05:00) = 18:42
        assert result.hour == 18

    def test_naive_datetime(self):
        """Naive datetime is assumed to be UTC."""
        result = parse_rfc3339_datetime("2025-11-17T13:42:00")
        assert result is not None
        assert result.tzinfo == datetime.timezone.utc

    def test_invalid_string(self):
        """Invalid string returns None."""
        assert parse_rfc3339_datetime("invalid") is None


class TestNormalizeRFC3339:
    """Tests for normalize_rfc3339 function."""

    def test_utc_datetime_to_z_suffix(self):
        """UTC datetime converts to Z suffix."""
        dt = datetime.datetime(2025, 11, 17, 13, 42, 0, tzinfo=datetime.timezone.utc)
        result = normalize_rfc3339(dt)
        assert result.endswith("Z")
        assert result == "2025-11-17T13:42:00Z"

    def test_offset_datetime_converts_to_utc(self):
        """Offset datetime converts to UTC with Z suffix."""
        # EST is UTC-5
        est = datetime.timezone(datetime.timedelta(hours=-5))
        dt = datetime.datetime(2025, 11, 17, 13, 42, 0, tzinfo=est)
        result = normalize_rfc3339(dt)
        assert result.endswith("Z")
        # 13:42 EST = 18:42 UTC
        assert "18:42:00Z" in result


class TestParseTimeString:
    """Tests for parse_time_string function."""

    def test_none_input(self):
        """None input returns None."""
        assert parse_time_string(None) is None

    def test_empty_string(self):
        """Empty string returns None."""
        assert parse_time_string("") is None

    def test_today_keyword(self):
        """'today' keyword returns midnight UTC."""
        result = parse_time_string("today")
        assert result is not None
        assert result.endswith("T00:00:00Z")

    def test_tomorrow_keyword(self):
        """'tomorrow' keyword returns midnight UTC."""
        result = parse_time_string("tomorrow")
        assert result is not None
        assert result.endswith("T00:00:00Z")

    def test_yesterday_keyword(self):
        """'yesterday' keyword returns midnight UTC."""
        result = parse_time_string("yesterday")
        assert result is not None
        assert result.endswith("T00:00:00Z")

    def test_next_week_keyword(self):
        """'next_week' keyword returns midnight UTC."""
        result = parse_time_string("next_week")
        assert result is not None
        assert result.endswith("T00:00:00Z")

    def test_next_month_keyword(self):
        """'next_month' keyword returns first day of next month."""
        result = parse_time_string("next_month")
        assert result is not None
        assert result.endswith("T00:00:00Z")
        assert result.endswith("-01T00:00:00Z")

    def test_next_year_keyword(self):
        """'next_year' keyword returns same date next year."""
        result = parse_time_string("next_year")
        assert result is not None
        assert result.endswith("T00:00:00Z")

    def test_iso_date_string(self):
        """ISO date string (YYYY-MM-DD) returns midnight UTC."""
        result = parse_time_string("2025-11-17")
        assert result == "2025-11-17T00:00:00Z"

    def test_naive_datetime_string(self):
        """Naive datetime string returns date at midnight UTC."""
        result = parse_time_string("2025-11-17T13:42:00")
        assert result == "2025-11-17T00:00:00Z"

    def test_timezone_aware_datetime(self):
        """Timezone-aware datetime extracts date in original timezone."""
        result = parse_time_string("2025-11-17T23:59:59-05:00")
        # Should extract 2025-11-17 in EST, then return midnight UTC for that date
        assert result == "2025-11-17T00:00:00Z"

    def test_invalid_string_returns_original(self):
        """Invalid string returns original value."""
        result = parse_time_string("not a date")
        assert result == "not a date"


class TestParseISOTimeString:
    """Tests for parse_iso_time_string function."""

    def test_none_input(self):
        """None input returns None."""
        assert parse_iso_time_string(None) is None

    def test_empty_string(self):
        """Empty string returns None."""
        assert parse_iso_time_string("") is None

    def test_iso_date_only(self):
        """ISO date-only string gets T00:00:00Z appended."""
        result = parse_iso_time_string("2025-11-17")
        assert result == "2025-11-17T00:00:00Z"

    def test_naive_datetime(self):
        """Naive datetime gets Z suffix."""
        result = parse_iso_time_string("2025-11-17T13:42:00")
        assert result == "2025-11-17T13:42:00Z"

    def test_datetime_with_z_suffix(self):
        """Datetime with Z suffix is preserved."""
        value = "2025-11-17T13:42:00Z"
        result = parse_iso_time_string(value)
        assert result == value

    def test_datetime_with_offset(self):
        """Datetime with offset converts to UTC."""
        result = parse_iso_time_string("2025-11-17T13:42:00-05:00")
        assert result is not None
        assert result.endswith("Z")
        # 13:42 EST = 18:42 UTC
        assert "18:42:" in result

    def test_invalid_date_returns_original(self):
        """Invalid string returns original value."""
        value = "not a date"
        result = parse_iso_time_string(value)
        assert result == value


class TestNormalizeDBTimestamp:
    """Tests for normalize_db_timestamp function."""

    def test_none_input(self):
        """None input returns None."""
        assert normalize_db_timestamp(None) is None

    def test_iso_string_with_utc(self):
        """ISO string with UTC timezone is normalized."""
        result = normalize_db_timestamp("2025-11-17T13:42:00+00:00")
        assert result is not None
        assert "+00:00" in result or "Z" not in result  # Should be ISO format

    def test_naive_timestamp_assumes_utc(self):
        """Naive timestamp is assumed to be UTC."""
        result = normalize_db_timestamp("2025-11-17 13:42:00")
        assert result is not None
        assert "2025-11-17" in result

    def test_offset_timestamp_converts_to_utc(self):
        """Timestamp with offset converts to UTC."""
        result = normalize_db_timestamp("2025-11-17T13:42:00-05:00")
        assert result is not None
        # 13:42 EST = 18:42 UTC
        assert "18:42:" in result

    def test_invalid_timestamp_returns_original(self):
        """Invalid timestamp returns original value."""
        value = "invalid"
        result = normalize_db_timestamp(value)
        assert result == value


class TestParseDBTimestamp:
    """Tests for parse_db_timestamp function."""

    def test_none_input(self):
        """None input returns None."""
        assert parse_db_timestamp(None) is None

    def test_iso_string_to_datetime(self):
        """ISO string converts to datetime."""
        result = parse_db_timestamp("2025-11-17T13:42:00")
        assert result is not None
        assert isinstance(result, datetime.datetime)
        assert result.year == 2025
        assert result.month == 11
        assert result.day == 17
        assert result.hour == 13
        assert result.tzinfo == datetime.timezone.utc

    def test_offset_timestamp_converts_to_utc(self):
        """Timestamp with offset converts to UTC datetime."""
        result = parse_db_timestamp("2025-11-17T13:42:00-05:00")
        assert result is not None
        assert result.tzinfo == datetime.timezone.utc
        # 13:42 EST = 18:42 UTC
        assert result.hour == 18

    def test_invalid_timestamp_returns_none(self):
        """Invalid timestamp returns None."""
        assert parse_db_timestamp("invalid") is None


class TestFormatTimestampForClient:
    """Tests for format_timestamp_for_client function."""

    def test_none_input(self):
        """None input returns (None, None)."""
        edt, utc = format_timestamp_for_client(None)
        assert edt is None
        assert utc is None

    def test_utc_timestamp_formats_both(self):
        """UTC timestamp formats to both EDT and UTC."""
        edt, utc = format_timestamp_for_client("2025-11-17T13:42:00+00:00")
        assert edt is not None
        assert utc is not None
        # EDT should have offset, UTC should have +00:00
        assert "-0" in edt or "-04:00" in edt or "-05:00" in edt  # EDT or EST
        assert "+00:00" in utc

    def test_naive_timestamp_assumes_utc(self):
        """Naive timestamp is assumed UTC."""
        edt, utc = format_timestamp_for_client("2025-11-17T13:42:00")
        assert edt is not None
        assert utc is not None

    def test_invalid_timestamp_returns_none_tuple(self):
        """Invalid timestamp returns (None, None)."""
        edt, utc = format_timestamp_for_client("invalid")
        assert edt is None
        assert utc is None
