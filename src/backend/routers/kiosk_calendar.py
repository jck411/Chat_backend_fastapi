"""Kiosk Calendar API router.

Provides a lightweight endpoint to fetch Google Calendar events
for display on the kiosk frontend.
"""

import asyncio
import datetime
import logging
from typing import Annotated, Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.services.google_auth.auth import DEFAULT_USER_EMAIL, get_calendar_service
from backend.utils.datetime_utils import normalize_rfc3339, parse_rfc3339_datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kiosk/calendar", tags=["Kiosk Calendar"])

# Calendar IDs to query (same as calendar_server.py)
DEFAULT_CALENDAR_IDS = (
    "primary",
    "family08001023161820261147@group.calendar.google.com",
    "en.usa#holiday@group.v.calendar.google.com",
    "4b779996b31f84a4dc520b2f0255e437863f0c826f3249c05f5f13f020fe3ba6@group.calendar.google.com",
    "0d02885a194bb2bfab4573ac6188f079498c768aa22659656b248962d03af863@group.calendar.google.com",
)

CALENDAR_LABELS = {
    "primary": "Your Primary Calendar",
    "family08001023161820261147@group.calendar.google.com": "Family Calendar",
    "en.usa#holiday@group.v.calendar.google.com": "Holidays in United States",
    "4b779996b31f84a4dc520b2f0255e437863f0c826f3249c05f5f13f020fe3ba6@group.calendar.google.com": "Mom Work Schedule",
    "0d02885a194bb2bfab4573ac6188f079498c768aa22659656b248962d03af863@group.calendar.google.com": "Dad Work Schedule",
}


class CalendarEvent(BaseModel):
    """Calendar event model for kiosk display."""

    id: str
    summary: str
    start: str
    end: str
    is_all_day: bool
    calendar_id: str
    calendar_label: str
    html_link: Optional[str] = None
    location: Optional[str] = None


class KioskCalendarResponse(BaseModel):
    """Kiosk calendar API response."""

    events: list[CalendarEvent]
    fetched_at: str
    time_min: Optional[str] = None
    time_max: Optional[str] = None


def _get_calendar_label(calendar_id: str) -> str:
    """Get a human-friendly label for a calendar ID."""
    return CALENDAR_LABELS.get(calendar_id, calendar_id)


@router.get("", response_model=KioskCalendarResponse)
async def get_kiosk_calendar(
    days: Annotated[
        int,
        Query(description="Number of days to fetch (default 7)", ge=1, le=30),
    ] = 7,
    user_email: Annotated[
        str,
        Query(description="User email for calendar access"),
    ] = DEFAULT_USER_EMAIL,
) -> KioskCalendarResponse:
    """Get calendar events for the kiosk display.

    Returns events for today through the specified number of days.
    Optimized for kiosk display with minimal payload size.
    """
    # Calculate time window
    now = datetime.datetime.now(datetime.timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    time_min = normalize_rfc3339(today_start)
    time_max = normalize_rfc3339(today_start + datetime.timedelta(days=days))

    try:
        service = get_calendar_service(user_email)
    except ValueError as e:
        logger.error(f"Calendar auth error: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Calendar not authorized: {str(e)}. Connect Google Services in Settings.",
        )

    # Query parameters for calendar API
    params: dict[str, Any] = {
        "maxResults": 100,
        "orderBy": "startTime",
        "singleEvents": True,
        "timeMin": time_min,
        "timeMax": time_max,
    }

    async def fetch_calendar_events(cal_id: str) -> list[dict[str, Any]]:
        """Fetch events from a single calendar."""
        try:
            request = service.events().list(calendarId=cal_id, **params)
            result = await asyncio.to_thread(request.execute)
            return result.get("items", [])
        except Exception as e:
            logger.warning(f"Failed to fetch calendar {cal_id}: {e}")
            return []

    # Fetch calendars sequentially to avoid SSL issues with parallel requests
    results: list[list[dict[str, Any]]] = []
    for cal_id in DEFAULT_CALENDAR_IDS:
        events = await fetch_calendar_events(cal_id)
        results.append(events)

    # Process events
    events: list[CalendarEvent] = []
    for cal_id, cal_events in zip(DEFAULT_CALENDAR_IDS, results):
        for event in cal_events:
            start = event.get("start", {})
            end = event.get("end", {})
            is_all_day = "date" in start

            event_start = start.get("date", start.get("dateTime", "")) or ""
            event_end = end.get("date", end.get("dateTime", "")) or event_start

            events.append(
                CalendarEvent(
                    id=event.get("id", ""),
                    summary=event.get("summary", "(No title)"),
                    start=event_start,
                    end=event_end,
                    is_all_day=is_all_day,
                    calendar_id=cal_id,
                    calendar_label=_get_calendar_label(cal_id),
                    html_link=event.get("htmlLink"),
                    location=event.get("location"),
                )
            )

    # Sort events by start time
    def sort_key(e: CalendarEvent) -> datetime.datetime:
        try:
            parsed = parse_rfc3339_datetime(e.start)
            if parsed:
                return parsed
            # For date-only strings, parse as date
            return datetime.datetime.fromisoformat(e.start).replace(
                tzinfo=datetime.timezone.utc
            )
        except Exception:
            return datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)

    events.sort(key=sort_key)

    return KioskCalendarResponse(
        events=events,
        fetched_at=now.isoformat(),
        time_min=time_min,
        time_max=time_max,
    )


__all__ = ["router"]
