"""Shared utilities for task scheduling and time normalization."""

from __future__ import annotations

import datetime
from typing import Optional

# Import datetime utilities from centralized location
from backend.utils.datetime_utils import (
    normalize_rfc3339,
    parse_rfc3339_datetime,
    parse_time_string,
)


def compute_task_window(
    time_min_rfc: Optional[str], time_max_rfc: Optional[str]
) -> tuple[Optional[datetime.datetime], datetime.datetime, Optional[datetime.datetime]]:
    """Determine the primary task window and overdue cutoff.

    Previous behavior limited overdue tasks to the last ~14 days, which caused
    older overdue items to be omitted from aggregated schedule views. To ensure
    overdue tasks are represented more completely while maintaining performance,
    widen the lookback window to a sensible default and bias further based on
    the provided start date when available.
    """

    now = datetime.datetime.now(datetime.timezone.utc)
    start_dt = parse_rfc3339_datetime(time_min_rfc)
    end_dt = parse_rfc3339_datetime(time_max_rfc)

    if end_dt is None:
        base = start_dt if start_dt and start_dt > now else now
        end_dt = base + datetime.timedelta(days=7)

    if end_dt < now:
        end_dt = now

    # Remove past-due lookback boundaries: include all historical overdue tasks.
    # Returning None signals callers to avoid setting a dueMin filter.
    past_due_cutoff: Optional[datetime.datetime] = None

    return start_dt, end_dt, past_due_cutoff


# Re-export for backwards compatibility
__all__ = ["parse_rfc3339_datetime", "normalize_rfc3339", "parse_time_string", "compute_task_window"]
