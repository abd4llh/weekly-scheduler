from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Dict, Iterable, Sequence, Tuple

from domain import CalendarEvent, EventSource, PlanRequest, PlanningTask, TimeWindow
from models import DAY_TO_INDEX, Event as LegacyEvent, Task as LegacyTask
from parser_utils import hhmm_to_minutes
from routine_utils import routine_requirements_from_settings


PRIORITY_TO_SCORE = {
    "Critical": 100,
    "High": 80,
    "Medium": 50,
    "Low": 25,
    "Optional": 10,
}

ROUTINE_SEQUENCE_RANK = {
    "morning routine": 10,
    "breakfast": 20,
    "lunch": 50,
    "dinner": 80,
    "evening wind-down": 95,
}

DEFAULT_TRAVEL_OVERRIDES = (
    ("home", "studio", 10),
    ("studio", "store", 20),
    ("home", "store", 20),
    ("home", "gym", 20),
    ("studio", "gym", 25),
    ("lab", "home", 30),
    ("lab", "studio", 25),
    ("office", "home", 30),
    ("office", "studio", 20),
    ("outside", "home", 20),
    ("outside", "studio", 20),
    ("outside", "store", 15),
)


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


def _clamp_target(start_min: int, end_min: int, target_min: int, duration_min: int) -> int:
    latest_start = max(start_min, end_min - duration_min)
    return min(max(target_min, start_min), latest_start)


def _preferred_windows(
    task: LegacyTask,
    wake_min: int,
    sleep_min: int,
) -> Tuple[TimeWindow, ...]:
    preferred = str(task.preferred_time or "Any")
    required_day = _day_index(task.required_day or task.fixed_day)
    weekdays = (required_day,) if required_day is not None else (None,)
    duration = max(5, int(task.duration_min))

    if preferred == "Morning":
        start, end = max(wake_min, 7 * 60), min(12 * 60, sleep_min)
        target = _clamp_target(start, end, max(8 * 60 + 30, wake_min + 120), duration)
    elif preferred == "Workday":
        start, end = max(9 * 60, wake_min), min(17 * 60, sleep_min)
        target = _clamp_target(start, end, 10 * 60, duration)
    elif preferred == "Afternoon":
        start, end = max(13 * 60, wake_min), min(18 * 60, sleep_min)
        target = _clamp_target(start, end, 14 * 60 + 30, duration)
    elif preferred == "Evening":
        start, end = max(18 * 60, wake_min), min(22 * 60, sleep_min)
        target = _clamp_target(start, end, 19 * 60, duration)
    elif preferred == "Weekend":
        return tuple(
            TimeWindow(
                wake_min,
                sleep_min,
                weekday=weekday,
                preferred_start_min=_clamp_target(wake_min, sleep_min, 10 * 60, duration),
                outside_penalty=16,
            )
            for weekday in (5, 6)
        )
    else:
        return ()

    if end <= start:
        return ()
    return tuple(
        TimeWindow(
            start,
            end,
            weekday=weekday,
            preferred_start_min=target,
            outside_penalty=16,
        )
        for weekday in weekdays
    )


def _routine_windows(settings: Dict | None) -> Dict[str, Tuple[TimeWindow, ...]]:
    if not settings:
        return {}
    output = {}
    meal_titles = {"Breakfast", "Lunch", "Dinner"}
    breakfast_target = hhmm_to_minutes(str(settings.get("breakfast_preferred_time", "")))
    for requirement in routine_requirements_from_settings(settings):
        title = requirement["title"]
        duration = int(requirement["duration_min"])
        start = int(requirement["window_start_min"])
        end = int(requirement["window_end_min"])
        preferred = _clamp_target(
            start,
            end,
            int(requirement["preferred_start_min"]),
            duration,
        )
        if (
            title == "Morning routine"
            and settings.get("breakfast_enabled", False)
            and breakfast_target is not None
        ):
            preferred = _clamp_target(
                start,
                end,
                int(breakfast_target) - duration,
                duration,
            )
        output[title.strip().lower()] = tuple(
            TimeWindow(
                start,
                end,
                weekday=day,
                weight=4,
                preferred_start_min=preferred,
                outside_penalty=24,
                prefer_later_fallback=title in meal_titles,
            )
            for day in range(7)
        )
    return output


def _daily_sequence_rank(task: LegacyTask) -> int | None:
    title = task.title.strip().lower()
    if title in ROUTINE_SEQUENCE_RANK:
        return ROUTINE_SEQUENCE_RANK[title]
    if task.preferred_time == "Morning":
        return 30
    if task.preferred_time == "Afternoon":
        return 60
    if task.preferred_time == "Evening":
        return 85
    return None


def _sequence_group(task: LegacyTask) -> str:
    title = task.title.strip().lower()
    if title in {"morning routine", "breakfast"} or task.preferred_time == "Morning":
        return "morning"
    return ""


def _transition_after(task: LegacyTask, transition_min: int) -> int:
    if transition_min <= 0 or task.category == "Routine":
        return 0
    if task.task_type == "Fixed":
        return transition_min
    if str(task.energy or "").lower() in {"high", "physical", "creative"}:
        return transition_min
    return 0


def _effective_location(task: LegacyTask) -> str:
    explicit = str(task.location or "any").strip().lower()
    aliases = {
        "laboratory": "lab",
        "house": "home",
        "outdoors": "outside",
        "shop": "store",
    }
    explicit = aliases.get(explicit, explicit)
    if explicit not in {"", "any"}:
        return explicit

    text = f"{task.title} {task.notes}".lower()
    if any(word in text for word in ["art supply", "supply store", "grocery", "groceries", "shop", "store"]):
        return "store"
    if any(word in text for word in ["studio", "painting", "paint ", "watercolor", "sketch", "varnish", "artwork"]):
        return "studio"
    if any(word in text for word in ["laboratory", " lab ", "experiment", "sensor"]):
        return "lab"
    if any(word in text for word in ["gym", "workout", "exercise", "training"]):
        return "gym"
    if any(word in text for word in ["office", "workplace"]):
        return "office"
    if any(word in text for word in ["market", "doctor", "appointment", "client meeting", "meet with"]):
        return "outside"
    if task.category == "Routine" or any(word in text for word in ["cook", "laundry", "house", "home"]):
        return "home"
    return "any"


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
    preferred_daily_flexible_min: int = 8 * 60,
    max_daily_flexible_min: int = 10 * 60,
    default_travel_min: int = 20,
    compact_gap_min: int = 30,
    travel_time_overrides: Tuple[Tuple[str, str, int], ...] = DEFAULT_TRAVEL_OVERRIDES,
    timezone: str = "Europe/Berlin",
    routine_settings: Dict | None = None,
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
    routine_windows = _routine_windows(routine_settings)

    canonical_tasks = []
    for task, task_id in zip(tasks, ids):
        sessions = max(1, int(task.sessions_per_week or 1))
        is_recurring = task.task_type == "Recurring"
        is_multi_session = task.task_type == "Multi-session"
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

        preferred_windows = routine_windows.get(
            task.title.strip().lower(),
            _preferred_windows(task, wake_min, sleep_min),
        )

        requested_sessions = sessions if (is_recurring or (is_multi_session and sessions > 1)) else None
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
                preferred_windows=preferred_windows,
                dependencies=dependencies,
                min_block_min=max(slot_minutes, int(task.min_block_min or slot_minutes)),
                max_block_min=max(slot_minutes, int(task.max_block_min or total_duration)),
                sessions_required=requested_sessions,
                distinct_session_days=is_recurring,
                prefer_distinct_session_days=is_multi_session and requested_sessions is not None,
                splittable=bool(task.splittable or task.task_type in {"Recurring", "Multi-session"}),
                energy=str(task.energy or "medium").lower(),
                location=_effective_location(task),
                locked=task.task_type == "Fixed",
                daily_sequence_rank=_daily_sequence_rank(task),
                sequence_group=_sequence_group(task),
                transition_after_min=_transition_after(task, transition_min),
                counts_toward_daily_limit=task.category != "Routine",
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
        preferred_daily_flexible_min=preferred_daily_flexible_min,
        max_daily_flexible_min=max_daily_flexible_min,
        default_travel_min=default_travel_min,
        compact_gap_min=compact_gap_min,
        travel_time_overrides=travel_time_overrides,
        timezone=timezone,
    )
