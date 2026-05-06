"""REST API endpoints for alarm management."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/alarms", tags=["alarms"])


class CreateAlarmRequest(BaseModel):
    """Request body for creating an alarm."""

    alarm_time: datetime = Field(..., description="When the alarm should fire (ISO format)")
    label: str = Field(default="Alarm", description="Label/message for the alarm")


class AlarmResponse(BaseModel):
    """Response containing alarm details."""

    alarm_id: str
    alarm_time: str
    label: str
    status: str
    created_at: str
    fired_at: str | None = None
    acknowledged_at: str | None = None


class SnoozeRequest(BaseModel):
    """Request body for snoozing an alarm."""

    snooze_minutes: int = Field(default=5, ge=1, le=60)


@router.post("", response_model=AlarmResponse)
async def create_alarm(request: Request, body: CreateAlarmRequest) -> dict[str, Any]:
    """Create a new alarm."""
    scheduler = getattr(request.app.state, "alarm_scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Alarm service not available")

    alarm = await scheduler.create_alarm(body.alarm_time, body.label)
    return alarm.to_dict()


@router.get("", response_model=list[AlarmResponse])
async def list_pending_alarms(request: Request) -> list[dict[str, Any]]:
    """List all pending alarms."""
    scheduler = getattr(request.app.state, "alarm_scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Alarm service not available")

    alarms = await scheduler.get_pending_alarms()
    return [alarm.to_dict() for alarm in alarms]


@router.get("/{alarm_id}", response_model=AlarmResponse)
async def get_alarm(request: Request, alarm_id: str) -> dict[str, Any]:
    """Get a specific alarm."""
    scheduler = getattr(request.app.state, "alarm_scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Alarm service not available")

    alarm = await scheduler.get_alarm(alarm_id)
    if alarm is None:
        raise HTTPException(status_code=404, detail="Alarm not found")

    return alarm.to_dict()


@router.post("/{alarm_id}/acknowledge")
async def acknowledge_alarm(request: Request, alarm_id: str) -> dict[str, Any]:
    """Acknowledge a firing alarm."""
    scheduler = getattr(request.app.state, "alarm_scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Alarm service not available")

    success = await scheduler.acknowledge_alarm(alarm_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Could not acknowledge alarm (not firing or not found)",
        )

    return {"success": True, "alarm_id": alarm_id, "action": "acknowledged"}


@router.post("/{alarm_id}/snooze", response_model=AlarmResponse)
async def snooze_alarm(
    request: Request, alarm_id: str, body: SnoozeRequest
) -> dict[str, Any]:
    """Snooze a firing alarm."""
    scheduler = getattr(request.app.state, "alarm_scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Alarm service not available")

    new_alarm = await scheduler.snooze_alarm(alarm_id, body.snooze_minutes)
    if new_alarm is None:
        raise HTTPException(
            status_code=400,
            detail="Could not snooze alarm (not firing or not found)",
        )

    return new_alarm.to_dict()


@router.delete("/{alarm_id}")
async def cancel_alarm(request: Request, alarm_id: str) -> dict[str, Any]:
    """Cancel a pending alarm."""
    scheduler = getattr(request.app.state, "alarm_scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Alarm service not available")

    success = await scheduler.cancel_alarm(alarm_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Could not cancel alarm (not pending or not found)",
        )

    return {"success": True, "alarm_id": alarm_id, "action": "cancelled"}


__all__ = ["router"]
