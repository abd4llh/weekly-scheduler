from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Iterable, Sequence, Tuple

from domain import CalendarEvent, EventSource, PlanRequest, PlanningTask, TimeWindow
from models import DAY_TO_INDEX, Event as LegacyEvent, Task as LegacyTask
from parser_utils import hhmm_to_minutes


PRIORITY_TO_SCORE = {
    "Critical": 100,
    "High": 80,
    "Medium": 50,
    "Low": 25,
    "Optional": 10,
}


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or "task"


def _day_index(day: str) -> int | None:
    return DAY_TO_INDEX.get(str(day or "").strip().lower())


def _at_day(week_start: datetime, day_index: int, minute_of_day: int) -> datetime:
    target = week_start + timedelta(days=day_index)
    return datetime.combine(
        target.date(),
        time(minute_of_day // 60, minute_of_day % 60),
        tzinfo=week_start.tzinfo,
    )


def _preferred_windows(task: LegacyTask, wake_min: int, sleep_min: int) -> Tuple[TimeWindow, ...]:
    preferred = str(task.preferred_time or "Any")
    required_day = _day_index(task.required_day or task.fixed_day)
    weekdays = (required_day,) if required_day is not None else (None,)

    if preferred == "Morning":
        bounds = (wake_min, min(12 * 60, sleep_min))
    elif preferred == "Workday":
        bounds = (max(9 * 60, wake_min), min(17 * 60, sleep_min))
    elif preferred == "Afternoon":
        bounds = (max(12 * 60, wake_min), min(18 * 60, sleep_min))
    elif preferred == "Evening":
        bounds = (max(17 * 60, wake_min), min(22 * 60, sleep_min))
    elif preferred == "Weekend":
        return tuple(
            TimeWindow(wake_min, sleep_min, weekday=weekday)
            for weekday in (5, 6)
        )
    else:
        return ()

    if bounds[1] <= bounds[0]:
        return ()
    return tuple(TimeWindow(bounds[0], bounds[1], weekday=weekday) for weekday in weekdays)


def legacy_tasks_to_plan_request(
    tasks: Sequence[LegacyTask],
    week_start: datetime,
    existing_events: Iterable[LegacyEvent] = (),
    *,
    wake_min: int = 6 * 60,
    sleep_min: int = 23 * 60,
    slot_minutes: int = 15,
    protect_weekend: bool = False,
    transition_min: int = 0,
    timezone: str = "Europe/Berlin",
) -> PlanRequest:
    """Convert the current app's Task/Event objects into the v0.12 domain model."""

    if week_start.hour or week_start.minute or week_start.second or week_start.microsecond:
        week_start = datetime.combine(week_start.date(), time.min, tzinfo=week_start.tzinfo)

    ids = []
    counts = {}
    for task in tasks:
        base = _slug(task.title)
        counts[base] = counts.get(base, 0) + 1
        ids.append(base if counts[base] == 1 else f"{base}-{counts[base]}")

    title_to_id = {
        task.title.strip().lower(): task_id
        for task, task_id in zip(tasks, ids)
    }

    canonical_tasks = []
    for task, task_id in zip(tasks, ids):
        sessions = max(1, int(task.sessions_per_week or 1))
        is_recurring = task.task_type == "Recurring"
        total_duration = int(task.duration_min) * sessions if is_recurring else int(task.duration_min)

        fixed_start = None
        fixed_end = None
        fixed_day = _day_index(task.fixed_day)
        fixed_minute = hhmm_to_minutes(task.fixed_start)
        if task.task_type == "Fixed" and fixed_day is not None and fixed_minute is not None:
            fixed_start = _at_day(week_start, fixed_day, fixed_minute)
            fixed_end = fixed_start + timedelta(minutes=total_duration)

        required_days = ()
        required_day = _day_index(task.required_day or (task.fixed_day if task.task_type != "Fixed" else ""))
        if required_day is not None:
            required_days = (required_day,)
        elif task.preferred_time == "Weekend":
            required_days = (5, 6)

        earliest_start = None
        earliest_day = _day_index(task.earliest_day)
        if earliest_day is not None:
            earliest_start = _at_day(week_start, earliest_day, wake_min)

        deadline = None
        deadline_day = _day_index(task.deadline_day)
        if deadline_day is not None:
            deadline_minute = hhmm_to_minutes(task.deadline_time)
            deadline = _at_day(
                week_start,
                deadline_day,
                deadline_minute if deadline_minute is not None else sleep_min,
            )

        dependencies = ()
        if str(task.depends_on or "").strip():
            dependency_id = title_to_id.get(str(task.depends_on).strip().lower())
            if dependency_id:
                dependencies = (dependency_id,)

        canonical_tasks.append(
            PlanningTask(
                id=task_id,
                title=task.title,
                total_duration_min=total_duration,
                priority=PRIORITY_TO_SCORE.get(task.priority, 50),
                earliest_start=earliest_start,
                deadline=deadline,
                fixed_start=fixed_start,
                fixed_end=fixed_end,
                required_weekdays=required_days,
                preferred_windows=_preferred_windows(task, wake_min, sleep_min),
                dependencies=dependencies,
                min_block_min=max(slot_minutes, int(task.min_block_min or slot_minutes)),
                max_block_min=max(slot_minutes, int(task.max_block_min or total_duration)),
                sessions_required=sessions if is_recurring else None,
                splittable=bool(task.splittable or task.task_type in {"Recurring", "Multi-session"}),
                energy=str(task.energy or "medium").lower(),
                location=str(task.location or "any").lower(),
                locked=task.task_type == "Fixed",
            )
        )

    busy_events = tuple(
        CalendarEvent(
            id=f"legacy-event-{index}",
            title=event.title,
            start=_at_day(week_start, int(event.day_index), int(event.start_min)),
            end=_at_day(week_start, int(event.day_index), int(event.end_min)),
            locked=True,
            busy=True,
            source=EventSource.IMPORTED,
        )
        for index, event in enumerate(existing_events)
    )

    return PlanRequest(
        horizon_start=week_start,
        horizon_end=week_start + timedelta(days=7),
        tasks=tuple(canonical_tasks),
        existing_events=busy_events,
        slot_minutes=slot_minutes,
        wake_min=wake_min,
        sleep_min=sleep_min,
        protect_weekend=protect_weekend,
        transition_min=transition_min,
        timezone=timezone,
    )
