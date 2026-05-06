"""Event-driven alarm scheduler using asyncio tasks."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from .alarm_repository import Alarm, AlarmRepository, AlarmStatus

if TYPE_CHECKING:
    from .voice_session import VoiceConnectionManager

logger = logging.getLogger(__name__)

# How long to wait before re-firing an unacknowledged alarm
REFIRE_DELAY_SECONDS = 60


class AlarmSchedulerService:
    """Manages alarm scheduling using asyncio tasks (fully event-driven)."""

    def __init__(
        self,
        repository: AlarmRepository,
        voice_manager: "VoiceConnectionManager | None" = None,
    ):
        self._repository = repository
        self._voice_manager = voice_manager
        self._scheduled_tasks: dict[str, asyncio.Task[None]] = {}
        self._refire_tasks: dict[str, asyncio.Task[None]] = {}
        self._shutdown = False

    def set_voice_manager(self, manager: "VoiceConnectionManager") -> None:
        """Set the voice connection manager (for late binding during app startup)."""
        self._voice_manager = manager

    async def initialize(self) -> None:
        """Load and schedule all pending/firing alarms from the database."""
        await self._repository.initialize()

        # Schedule all pending alarms
        pending = await self._repository.get_pending_alarms()
        for alarm in pending:
            self._schedule_alarm(alarm)
            logger.info(
                f"Restored pending alarm {alarm.alarm_id} for {alarm.alarm_time}"
            )

        # Re-fire any alarms that were firing when we shut down
        firing = await self._repository.get_firing_alarms()
        for alarm in firing:
            logger.info(f"Re-firing alarm {alarm.alarm_id} (was firing at shutdown)")
            asyncio.create_task(self._fire_alarm(alarm.alarm_id))

    async def shutdown(self) -> None:
        """Cancel all scheduled tasks."""
        self._shutdown = True

        # Cancel all scheduled alarm tasks
        for alarm_id, task in self._scheduled_tasks.items():
            if not task.done():
                task.cancel()
                logger.debug(f"Cancelled scheduled task for alarm {alarm_id}")

        # Cancel all re-fire tasks
        for alarm_id, task in self._refire_tasks.items():
            if not task.done():
                task.cancel()
                logger.debug(f"Cancelled refire task for alarm {alarm_id}")

        self._scheduled_tasks.clear()
        self._refire_tasks.clear()

        await self._repository.close()

    async def create_alarm(
        self,
        alarm_time: datetime,
        label: str = "Alarm",
    ) -> Alarm:
        """Create and schedule a new alarm."""
        alarm = await self._repository.create_alarm(alarm_time, label)
        self._schedule_alarm(alarm)
        logger.info(f"Created alarm {alarm.alarm_id} for {alarm_time} - '{label}'")
        return alarm

    def _schedule_alarm(self, alarm: Alarm) -> None:
        """Schedule an asyncio task to fire at the alarm time."""
        # Cancel existing task if any
        if alarm.alarm_id in self._scheduled_tasks:
            existing = self._scheduled_tasks[alarm.alarm_id]
            if not existing.done():
                existing.cancel()

        task = asyncio.create_task(
            self._wait_and_fire(alarm.alarm_id, alarm.alarm_time)
        )
        self._scheduled_tasks[alarm.alarm_id] = task

    async def _wait_and_fire(self, alarm_id: str, alarm_time: datetime) -> None:
        """Wait until the alarm time, then fire the alarm."""
        try:
            now = datetime.now(timezone.utc)

            # Ensure alarm_time has timezone for comparison
            if alarm_time.tzinfo is None:
                alarm_time = alarm_time.replace(tzinfo=timezone.utc)

            delay = (alarm_time - now).total_seconds()

            if delay > 0:
                logger.debug(f"Alarm {alarm_id} sleeping for {delay:.1f}s")
                await asyncio.sleep(delay)

            if self._shutdown:
                return

            await self._fire_alarm(alarm_id)

        except asyncio.CancelledError:
            logger.debug(f"Alarm task {alarm_id} was cancelled")
            raise

    async def _fire_alarm(self, alarm_id: str) -> None:
        """Fire an alarm - mark as firing but keep status for refire logic."""
        if self._shutdown:
            return

        alarm = await self._repository.get_alarm(alarm_id)
        if alarm is None:
            logger.warning(f"Alarm {alarm_id} not found when trying to fire")
            return

        # Only fire if pending (first fire) or firing (re-fire)
        if alarm.status == AlarmStatus.PENDING:
            await self._repository.mark_firing(alarm_id)
            logger.info(f"🔔 Alarm firing: {alarm_id} - '{alarm.label}'")
        elif alarm.status != AlarmStatus.FIRING:
            logger.debug(f"Alarm {alarm_id} not in pending/firing state, skipping")
            return

        # Send to all connected kiosk clients
        await self._notify_kiosk(alarm_id, alarm.label, alarm.alarm_time)

        # Schedule re-fire if not acknowledged
        self._schedule_refire(alarm_id)

    def _schedule_refire(self, alarm_id: str) -> None:
        """Schedule a task to re-fire the alarm if not acknowledged."""
        # Cancel existing refire task if any
        if alarm_id in self._refire_tasks:
            existing = self._refire_tasks[alarm_id]
            if not existing.done():
                existing.cancel()

        task = asyncio.create_task(self._refire_if_needed(alarm_id))
        self._refire_tasks[alarm_id] = task

    async def _refire_if_needed(self, alarm_id: str) -> None:
        """Check if alarm still needs attention and re-fire if so."""
        try:
            await asyncio.sleep(REFIRE_DELAY_SECONDS)

            if self._shutdown:
                return

            alarm = await self._repository.get_alarm(alarm_id)
            if alarm is None:
                return

            if alarm.status == AlarmStatus.FIRING:
                logger.info(f"🔔 Re-firing unacknowledged alarm: {alarm_id}")
                await self._notify_kiosk(alarm_id, alarm.label, alarm.alarm_time)
                # Schedule another re-fire
                self._schedule_refire(alarm_id)

        except asyncio.CancelledError:
            logger.debug(f"Refire task {alarm_id} was cancelled")
            raise

    async def _notify_kiosk(
        self, alarm_id: str, label: str, alarm_time: datetime
    ) -> None:
        """Send alarm notification to all connected kiosk clients."""
        if self._voice_manager is None:
            logger.warning("No voice manager available, cannot notify kiosk")
            return

        message = {
            "type": "alarm_trigger",
            "alarm_id": alarm_id,
            "label": label,
            "alarm_time": alarm_time.isoformat(),
        }

        # Broadcast to all connected clients (kiosks)
        await self._voice_manager.broadcast(message)
        logger.debug(f"Sent alarm_trigger to all clients: {alarm_id}")

    async def acknowledge_alarm(self, alarm_id: str) -> bool:
        """Acknowledge a firing alarm, stopping it."""
        # Cancel refire task
        if alarm_id in self._refire_tasks:
            task = self._refire_tasks.pop(alarm_id)
            if not task.done():
                task.cancel()

        success = await self._repository.mark_acknowledged(alarm_id)
        if success:
            logger.info(f"✓ Alarm acknowledged: {alarm_id}")
        return success

    async def snooze_alarm(
        self, alarm_id: str, snooze_minutes: int = 5
    ) -> Alarm | None:
        """Snooze a firing alarm, creating a new one for later."""
        # Cancel refire task
        if alarm_id in self._refire_tasks:
            task = self._refire_tasks.pop(alarm_id)
            if not task.done():
                task.cancel()

        # Get the original alarm for the label
        original = await self._repository.get_alarm(alarm_id)
        if original is None or original.status != AlarmStatus.FIRING:
            return None

        # Mark original as snoozed
        await self._repository.mark_snoozed(alarm_id)

        # Create new alarm for snooze time
        snooze_time = datetime.now(timezone.utc) + timedelta(minutes=snooze_minutes)
        new_alarm = await self.create_alarm(snooze_time, f"{original.label} (snoozed)")

        logger.info(f"💤 Alarm snoozed: {alarm_id} -> {new_alarm.alarm_id} ({snooze_minutes}m)")
        return new_alarm

    async def cancel_alarm(self, alarm_id: str) -> bool:
        """Cancel a pending alarm."""
        # Cancel scheduled task
        if alarm_id in self._scheduled_tasks:
            task = self._scheduled_tasks.pop(alarm_id)
            if not task.done():
                task.cancel()

        success = await self._repository.cancel_alarm(alarm_id)
        if success:
            logger.info(f"✗ Alarm cancelled: {alarm_id}")
        return success

    async def get_pending_alarms(self) -> list[Alarm]:
        """Get all pending alarms."""
        return await self._repository.get_pending_alarms()

    async def get_alarm(self, alarm_id: str) -> Alarm | None:
        """Get a specific alarm."""
        return await self._repository.get_alarm(alarm_id)


__all__ = ["AlarmSchedulerService"]
