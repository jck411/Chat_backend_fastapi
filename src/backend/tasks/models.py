"""Domain models representing tasks and task-related metadata."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class TaskListInfo:
    """Metadata describing a Google Task list."""

    id: str
    title: str
    updated: Optional[str] = None


@dataclass(slots=True)
class Task:
    """Representation of a user task independent from scheduling."""

    title: str
    status: str
    list_title: str
    list_id: str
    id: str
    due: Optional[datetime.datetime] = None
    notes: Optional[str] = None
    updated: Optional[str] = None
    completed: Optional[str] = None
    web_link: Optional[str] = None
    parent: Optional[str] = None
    position: Optional[str] = None

    @property
    def is_scheduled(self) -> bool:
        """Return True when the task has an associated due datetime."""

        return self.due is not None


@dataclass(slots=True)
class ScheduledTask:
    """Scheduled task included in calendar views."""

    title: str
    due: datetime.datetime
    due_display: str
    status: str
    list_title: str
    list_id: str
    id: str
    notes: Optional[str] = None
    updated: Optional[str] = None
    completed: Optional[str] = None
    web_link: Optional[str] = None
    is_overdue: bool = False


@dataclass(slots=True)
class TaskSearchResult:
    """Result structure for keyword-based task searches."""

    title: str
    status: str
    list_title: str
    list_id: str
    id: str
    due: Optional[str] = None
    updated: Optional[str] = None
    completed: Optional[str] = None
    notes: Optional[str] = None
    web_link: Optional[str] = None
