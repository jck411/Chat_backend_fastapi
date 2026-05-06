"""SQLite-backed repository for alarms."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import aiosqlite


class AlarmStatus(str, Enum):
    """Possible states for an alarm."""

    PENDING = "pending"
    FIRING = "firing"
    ACKNOWLEDGED = "acknowledged"
    SNOOZED = "snoozed"
    CANCELLED = "cancelled"


@dataclass
class Alarm:
    """Represents an alarm record."""

    alarm_id: str
    alarm_time: datetime
    label: str
    status: AlarmStatus
    created_at: datetime
    fired_at: datetime | None = None
    acknowledged_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "alarm_id": self.alarm_id,
            "alarm_time": self.alarm_time.isoformat(),
            "label": self.label,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "fired_at": self.fired_at.isoformat() if self.fired_at else None,
            "acknowledged_at": (
                self.acknowledged_at.isoformat() if self.acknowledged_at else None
            ),
        }


class AlarmRepository:
    """Persist and retrieve alarms from SQLite."""

    def __init__(self, database_path: Path):
        self._path = database_path
        self._connection: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the SQLite connection and ensure tables exist."""
        if self._connection is not None:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL;")
        await self._create_schema()

    async def _create_schema(self) -> None:
        assert self._connection is not None
        await self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS alarms (
                alarm_id TEXT PRIMARY KEY,
                alarm_time TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT 'Alarm',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                fired_at TEXT,
                acknowledged_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_alarms_status ON alarms(status);
            CREATE INDEX IF NOT EXISTS idx_alarms_alarm_time ON alarms(alarm_time);
            """
        )
        await self._connection.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    def _row_to_alarm(self, row: aiosqlite.Row) -> Alarm:
        """Convert a database row to an Alarm object."""
        return Alarm(
            alarm_id=row["alarm_id"],
            alarm_time=datetime.fromisoformat(row["alarm_time"]),
            label=row["label"],
            status=AlarmStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            fired_at=(
                datetime.fromisoformat(row["fired_at"]) if row["fired_at"] else None
            ),
            acknowledged_at=(
                datetime.fromisoformat(row["acknowledged_at"])
                if row["acknowledged_at"]
                else None
            ),
        )

    async def create_alarm(
        self,
        alarm_time: datetime,
        label: str = "Alarm",
    ) -> Alarm:
        """Create a new pending alarm."""
        assert self._connection is not None

        alarm_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # Ensure alarm_time has timezone
        if alarm_time.tzinfo is None:
            alarm_time = alarm_time.replace(tzinfo=timezone.utc)

        await self._connection.execute(
            """
            INSERT INTO alarms (alarm_id, alarm_time, label, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                alarm_id,
                alarm_time.isoformat(),
                label,
                AlarmStatus.PENDING.value,
                now.isoformat(),
            ),
        )
        await self._connection.commit()

        return Alarm(
            alarm_id=alarm_id,
            alarm_time=alarm_time,
            label=label,
            status=AlarmStatus.PENDING,
            created_at=now,
        )

    async def get_alarm(self, alarm_id: str) -> Alarm | None:
        """Retrieve a single alarm by ID."""
        assert self._connection is not None

        cursor = await self._connection.execute(
            "SELECT * FROM alarms WHERE alarm_id = ?",
            (alarm_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()

        if row is None:
            return None
        return self._row_to_alarm(row)

    async def get_pending_alarms(self) -> list[Alarm]:
        """Retrieve all pending alarms, ordered by alarm time."""
        assert self._connection is not None

        cursor = await self._connection.execute(
            """
            SELECT * FROM alarms
            WHERE status = ?
            ORDER BY alarm_time ASC
            """,
            (AlarmStatus.PENDING.value,),
        )
        rows = await cursor.fetchall()
        await cursor.close()

        return [self._row_to_alarm(row) for row in rows]

    async def get_firing_alarms(self) -> list[Alarm]:
        """Retrieve all currently firing alarms."""
        assert self._connection is not None

        cursor = await self._connection.execute(
            """
            SELECT * FROM alarms
            WHERE status = ?
            ORDER BY alarm_time ASC
            """,
            (AlarmStatus.FIRING.value,),
        )
        rows = await cursor.fetchall()
        await cursor.close()

        return [self._row_to_alarm(row) for row in rows]

    async def mark_firing(self, alarm_id: str) -> bool:
        """Mark an alarm as firing."""
        assert self._connection is not None

        now = datetime.now(timezone.utc)
        cursor = await self._connection.execute(
            """
            UPDATE alarms
            SET status = ?, fired_at = ?
            WHERE alarm_id = ? AND status = ?
            """,
            (
                AlarmStatus.FIRING.value,
                now.isoformat(),
                alarm_id,
                AlarmStatus.PENDING.value,
            ),
        )
        updated = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return bool(updated)

    async def mark_acknowledged(self, alarm_id: str) -> bool:
        """Mark an alarm as acknowledged."""
        assert self._connection is not None

        now = datetime.now(timezone.utc)
        cursor = await self._connection.execute(
            """
            UPDATE alarms
            SET status = ?, acknowledged_at = ?
            WHERE alarm_id = ? AND status = ?
            """,
            (
                AlarmStatus.ACKNOWLEDGED.value,
                now.isoformat(),
                alarm_id,
                AlarmStatus.FIRING.value,
            ),
        )
        updated = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return bool(updated)

    async def mark_snoozed(self, alarm_id: str) -> bool:
        """Mark an alarm as snoozed (caller should create a new pending alarm)."""
        assert self._connection is not None

        cursor = await self._connection.execute(
            """
            UPDATE alarms
            SET status = ?
            WHERE alarm_id = ? AND status = ?
            """,
            (
                AlarmStatus.SNOOZED.value,
                alarm_id,
                AlarmStatus.FIRING.value,
            ),
        )
        updated = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return bool(updated)

    async def cancel_alarm(self, alarm_id: str) -> bool:
        """Cancel a pending alarm."""
        assert self._connection is not None

        cursor = await self._connection.execute(
            """
            UPDATE alarms
            SET status = ?
            WHERE alarm_id = ? AND status = ?
            """,
            (
                AlarmStatus.CANCELLED.value,
                alarm_id,
                AlarmStatus.PENDING.value,
            ),
        )
        updated = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return bool(updated)

    async def delete_old_alarms(self, days: int = 7) -> int:
        """Delete acknowledged/cancelled alarms older than specified days."""
        assert self._connection is not None

        cutoff = datetime.now(timezone.utc)
        from datetime import timedelta

        cutoff = cutoff - timedelta(days=days)

        cursor = await self._connection.execute(
            """
            DELETE FROM alarms
            WHERE status IN (?, ?)
              AND created_at < ?
            """,
            (
                AlarmStatus.ACKNOWLEDGED.value,
                AlarmStatus.CANCELLED.value,
                cutoff.isoformat(),
            ),
        )
        deleted = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return deleted


__all__ = ["AlarmRepository", "Alarm", "AlarmStatus"]
