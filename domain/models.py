from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple


class TaskStatus(str, Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class EventSource(str, Enum):
    OPTIMIZER = "optimizer"
    USER = "user"
    IMPORTED = "imported"
    FIXED_TASK = "fixed_task"
    REPLANNER = "replanner"


@dataclass(frozen=True)
class TimeWindow:
    """A weighted preferred time window in minutes after midnight.

    ``weekday`` follows Python's convention: Monday=0 ... Sunday=6. When it is
    ``None``, the preference applies every day. ``preferred_start_min`` gives
    the optimizer an ideal point inside the window instead of treating every
    legal time equally. ``prefer_later_fallback`` makes a time after a blocked
    window preferable to an equally distant time before it.
    """

    start_min: int
    end_min: int
    weekday: Optional[int] = None
    weight: int = 1
    preferred_start_min: Optional[int] = None
    outside_penalty: int = 12
    prefer_later_fallback: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.start_min < self.end_min <= 24 * 60:
            raise ValueError("TimeWindow must satisfy 0 <= start < end <= 1440.")
        if self.weekday is not None and self.weekday not in range(7):
            raise ValueError("weekday must be between 0 and 6.")
        if self.weight < 0 or self.outside_penalty < 0:
            raise ValueError("Time-window penalty weights must be non-negative.")
        if self.preferred_start_min is not None:
            if not self.start_min <= self.preferred_start_min < self.end_min:
                raise ValueError("preferred_start_min must be inside the time window.")


@dataclass(frozen=True)
class Project:
    id: str
    title: str
    description: str = ""
    deadline: Optional[datetime] = None
    priority: int = 50
    status: ProjectStatus = ProjectStatus.ACTIVE
    estimated_total_min: int = 0
    remaining_min: int = 0

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.title.strip():
            raise ValueError("Project id and title are required.")
        if not 0 <= self.priority <= 100:
            raise ValueError("Project priority must be between 0 and 100.")
        if self.estimated_total_min < 0 or self.remaining_min < 0:
            raise ValueError("Project durations must be non-negative.")


@dataclass(frozen=True)
class PlanningTask:
    id: str
    title: str
    total_duration_min: int
    project_id: Optional[str] = None
    priority: int = 50
    status: TaskStatus = TaskStatus.PLANNED
    earliest_start: Optional[datetime] = None
    deadline: Optional[datetime] = None
    fixed_start: Optional[datetime] = None
    fixed_end: Optional[datetime] = None
    required_weekdays: Tuple[int, ...] = ()
    preferred_windows: Tuple[TimeWindow, ...] = ()
    dependencies: Tuple[str, ...] = ()
    min_block_min: int = 30
    max_block_min: int = 180
    sessions_required: Optional[int] = None
    distinct_session_days: bool = False
    prefer_distinct_session_days: bool = False
    splittable: bool = True
    energy: str = "medium"
    location: str = "any"
    locked: bool = False
    daily_sequence_rank: Optional[int] = None
    sequence_group: str = ""
    transition_after_min: int = 0
    counts_toward_daily_limit: bool = True

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.title.strip():
            raise ValueError("Task id and title are required.")
        if self.total_duration_min <= 0:
            raise ValueError("Task duration must be positive.")
        if not 0 <= self.priority <= 100:
            raise ValueError("Task priority must be between 0 and 100.")
        if self.min_block_min <= 0 or self.max_block_min <= 0:
            raise ValueError("Block durations must be positive.")
        if self.min_block_min > self.max_block_min:
            raise ValueError("min_block_min cannot exceed max_block_min.")
        if self.sessions_required is not None and self.sessions_required <= 0:
            raise ValueError("sessions_required must be positive when provided.")
        if self.distinct_session_days and self.sessions_required is None:
            raise ValueError("distinct_session_days requires sessions_required.")
        if self.prefer_distinct_session_days and self.sessions_required is None:
            raise ValueError("prefer_distinct_session_days requires sessions_required.")
        if any(day not in range(7) for day in self.required_weekdays):
            raise ValueError("required_weekdays values must be between 0 and 6.")
        if (self.fixed_start is None) != (self.fixed_end is None):
            raise ValueError("fixed_start and fixed_end must be supplied together.")
        if self.fixed_start and self.fixed_end:
            if self.fixed_end <= self.fixed_start:
                raise ValueError("fixed_end must be after fixed_start.")
            fixed_minutes = int((self.fixed_end - self.fixed_start).total_seconds() // 60)
            if fixed_minutes != self.total_duration_min:
                raise ValueError("Fixed task duration must match total_duration_min.")
        if self.earliest_start and self.deadline and self.deadline <= self.earliest_start:
            raise ValueError("deadline must be after earliest_start.")
        if self.daily_sequence_rank is not None and self.daily_sequence_rank < 0:
            raise ValueError("daily_sequence_rank cannot be negative.")
        if self.transition_after_min < 0:
            raise ValueError("transition_after_min cannot be negative.")


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    locked: bool = True
    busy: bool = True
    source: EventSource = EventSource.IMPORTED

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.title.strip():
            raise ValueError("Calendar event id and title are required.")
        if self.end <= self.start:
            raise ValueError("Calendar event end must be after start.")


@dataclass(frozen=True)
class ScheduledEvent:
    id: str
    task_id: str
    title: str
    start: datetime
    end: datetime
    locked: bool = False
    completed: bool = False
    skipped: bool = False
    source: EventSource = EventSource.OPTIMIZER
    revision_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.task_id.strip() or not self.title.strip():
            raise ValueError("Scheduled event id, task_id and title are required.")
        if self.end <= self.start:
            raise ValueError("Scheduled event end must be after start.")


@dataclass(frozen=True)
class PlanRequest:
    horizon_start: datetime
    horizon_end: datetime
    tasks: Tuple[PlanningTask, ...]
    existing_events: Tuple[CalendarEvent, ...] = ()
    projects: Tuple[Project, ...] = ()
    slot_minutes: int = 15
    wake_min: int = 6 * 60
    sleep_min: int = 23 * 60
    protect_weekend: bool = False
    transition_min: int = 0
    preferred_daily_flexible_min: int = 8 * 60
    max_daily_flexible_min: int = 10 * 60
    default_travel_min: int = 20
    compact_gap_min: int = 30
    travel_time_overrides: Tuple[Tuple[str, str, int], ...] = ()
    timezone: str = "Europe/Berlin"

    def __post_init__(self) -> None:
        if self.horizon_end <= self.horizon_start:
            raise ValueError("horizon_end must be after horizon_start.")
        if self.slot_minutes <= 0 or 60 % self.slot_minutes != 0:
            raise ValueError("slot_minutes must be a positive divisor of 60.")
        if not 0 <= self.wake_min < self.sleep_min <= 24 * 60:
            raise ValueError("Wake/sleep minutes must satisfy 0 <= wake < sleep <= 1440.")
        if self.transition_min < 0:
            raise ValueError("transition_min cannot be negative.")
        if self.preferred_daily_flexible_min < 0:
            raise ValueError("preferred_daily_flexible_min cannot be negative.")
        if self.max_daily_flexible_min <= 0:
            raise ValueError("max_daily_flexible_min must be positive.")
        if self.preferred_daily_flexible_min > self.max_daily_flexible_min:
            raise ValueError("Preferred daily load cannot exceed the hard daily maximum.")
        if self.default_travel_min < 0 or self.compact_gap_min < 0:
            raise ValueError("Travel and compact-gap settings cannot be negative.")
        for source, destination, minutes in self.travel_time_overrides:
            if not str(source).strip() or not str(destination).strip() or minutes < 0:
                raise ValueError("Invalid travel-time override.")
        task_ids = [task.id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Task ids must be unique within a plan request.")


@dataclass(frozen=True)
class PlanRevision:
    id: str
    created_at: datetime
    reason: str
    parent_revision_id: Optional[str] = None
    changed_event_ids: Tuple[str, ...] = ()
    objective_score: Optional[float] = None
    metadata: dict = field(default_factory=dict)
