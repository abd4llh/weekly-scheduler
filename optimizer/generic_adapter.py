from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Dict, Iterable, Sequence, Tuple

from domain import CalendarEvent, EventSource, PlanRequest, PlanningTask, TimeWindow
from models import DAY_TO_INDEX, Event as LegacyEvent, Task as LegacyTask
from parser_utils import hhmm_to_minutes
from routine_utils import routine_requirements_from_settings

PRIORITY_TO_SCORE = {"Critical": 100, "High": 80, "Medium": 50, "Low": 25, "Optional": 10}
ROUTINE_SEQUENCE_RANK = {"morning routine": 10, "breakfast": 20, "lunch": 50, "dinner": 80, "evening wind-down": 95}
MEAL_TITLES = {"breakfast", "lunch", "dinner"}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "task"


def _day_index(day: str):
    return DAY_TO_INDEX.get(str(day or "").strip().lower())


def _at_day(week_start: datetime, day_index: int, minute_of_day: int) -> datetime:
    target = week_start + timedelta(days=day_index)
    return datetime.combine(target.date(), time(minute_of_day // 60, minute_of_day % 60), tzinfo=week_start.tzinfo)


def _clamp_target(start_min: int, end_min: int, target_min: int, duration_min: int) -> int:
    return min(max(target_min, start_min), max(start_min, end_min - duration_min))


def _preferred_windows(
    task: LegacyTask,
    wake_min: int,
    sleep_min: int,
    default_flexible_start_min: int,
) -> Tuple[TimeWindow, ...]:
    preferred = str(task.preferred_time or "Any")
    day = _day_index(task.required_day or task.fixed_day)
    weekdays = (day,) if day is not None else (None,)
    duration = max(5, int(task.duration_min))
    settings = {
        "Morning": (max(wake_min, 420), min(720, sleep_min), max(510, wake_min + 120)),
        "Workday": (max(540, wake_min), min(1020, sleep_min), 600),
        "Afternoon": (max(780, wake_min), min(1080, sleep_min), 870),
        "Evening": (max(1080, wake_min), min(1320, sleep_min), 1140),
        "Any": (wake_min, sleep_min, default_flexible_start_min),
    }
    if preferred == "Weekend":
        return tuple(
            TimeWindow(
                wake_min,
                sleep_min,
                weekday=d,
                preferred_start_min=_clamp_target(wake_min, sleep_min, max(600, default_flexible_start_min), duration),
                outside_penalty=16,
            )
            for d in (5, 6)
        )
    if preferred not in settings:
        return ()
    start, end, target = settings[preferred]
    if end <= start:
        return ()
    target = _clamp_target(start, end, target, duration)
    outside_penalty = 0 if preferred == "Any" else 16
    return tuple(
        TimeWindow(
            start,
            end,
            weekday=d,
            preferred_start_min=target,
            outside_penalty=outside_penalty,
        )
        for d in weekdays
    )


def _routine_windows(settings: Dict | None):
    if not settings:
        return {}
    output = {}
    breakfast_target = hhmm_to_minutes(str(settings.get("breakfast_preferred_time", "")))
    for requirement in routine_requirements_from_settings(settings):
        title = requirement["title"]
        duration = int(requirement["duration_min"])
        start = int(requirement["window_start_min"])
        end = int(requirement["window_end_min"])
        preferred = _clamp_target(start, end, int(requirement["preferred_start_min"]), duration)
        if title == "Morning routine" and settings.get("breakfast_enabled") and breakfast_target is not None:
            preferred = _clamp_target(start, end, breakfast_target - duration, duration)
        output[title.lower()] = tuple(
            TimeWindow(
                start,
                end,
                weekday=d,
                weight=4,
                preferred_start_min=preferred,
                outside_penalty=24,
                prefer_later_fallback=title.lower() in MEAL_TITLES,
            )
            for d in range(7)
        )
    return output


def _normalize_location(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "Any").strip()).lower() or "any"


def _distribution_flags(task: LegacyTask, recurring: bool, sessions):
    if sessions is None:
        return False, False, False
    preference = str(task.session_distribution or "Any")
    require = preference == "Require different days" or (recurring and preference != "Prefer same day")
    return require, preference == "Prefer different days", preference == "Prefer same day"


def _sequence_rank(task: LegacyTask):
    title = task.title.strip().lower()
    if title in ROUTINE_SEQUENCE_RANK:
        return ROUTINE_SEQUENCE_RANK[title]
    return {"Morning": 30, "Afternoon": 60, "Evening": 85}.get(task.preferred_time)


def legacy_tasks_to_plan_request(
    tasks: Sequence[LegacyTask],
    week_start: datetime,
    existing_events: Iterable[LegacyEvent] = (),
    *,
    wake_min: int = 360,
    sleep_min: int = 1380,
    slot_minutes: int = 15,
    protect_weekend: bool = False,
    transition_min: int = 0,
    preferred_daily_flexible_min: int = 480,
    max_daily_flexible_min: int = 600,
    preferred_daily_total_min: int = 600,
    preferred_daily_focus_min: int = 240,
    late_focus_start_min: int = 1140,
    default_flexible_start_min: int = 540,
    default_travel_min: int = 20,
    compact_gap_min: int = 30,
    travel_time_overrides: Tuple[Tuple[str, str, int], ...] = (),
    timezone: str = "Europe/Berlin",
    routine_settings: Dict | None = None,
) -> PlanRequest:
    if any((week_start.hour, week_start.minute, week_start.second, week_start.microsecond)):
        week_start = datetime.combine(week_start.date(), time.min, tzinfo=week_start.tzinfo)

    ids, counts = [], {}
    for task in tasks:
        base = _slug(task.title)
        counts[base] = counts.get(base, 0) + 1
        ids.append(base if counts[base] == 1 else f"{base}-{counts[base]}")
    title_to_id = {task.title.strip().lower(): task_id for task, task_id in zip(tasks, ids)}
    routine_windows = _routine_windows(routine_settings)
    canonical = []

    for task, task_id in zip(tasks, ids):
        session_count = max(1, int(task.sessions_per_week or 1))
        recurring = task.task_type == "Recurring"
        multi = task.task_type == "Multi-session"
        total_duration = int(task.duration_min) * session_count if recurring else int(task.duration_min)

        fixed_start = fixed_end = None
        fixed_day = _day_index(task.fixed_day)
        fixed_minute = hhmm_to_minutes(task.fixed_start)
        if task.task_type == "Fixed" and fixed_day is not None and fixed_minute is not None:
            fixed_start = _at_day(week_start, fixed_day, fixed_minute)
            fixed_end = fixed_start + timedelta(minutes=total_duration)

        required_day = _day_index(task.required_day or (task.fixed_day if task.task_type != "Fixed" else ""))
        required_days = (required_day,) if required_day is not None else ((5, 6) if task.preferred_time == "Weekend" else ())
        earliest_day = _day_index(task.earliest_day)
        earliest_start = _at_day(week_start, earliest_day, wake_min) if earliest_day is not None else None
        deadline_day = _day_index(task.deadline_day)
        deadline = None
        if deadline_day is not None:
            minute = hhmm_to_minutes(task.deadline_time)
            deadline = _at_day(week_start, deadline_day, minute if minute is not None else sleep_min)

        dependency = title_to_id.get(str(task.depends_on or "").strip().lower())
        requested_sessions = session_count if recurring or (multi and session_count > 1) else None
        distinct, prefer_distinct, prefer_same = _distribution_flags(task, recurring, requested_sessions)
        title_key = task.title.strip().lower()
        windows = routine_windows.get(
            title_key,
            _preferred_windows(task, wake_min, sleep_min, default_flexible_start_min),
        )
        hard_earliest = windows[0].start_min if title_key in MEAL_TITLES and windows else None
        demanding = task.cognitive_load == "High" or task.physical_load == "High"
        recovery = max(int(task.recovery_min or 0), int(transition_min) if demanding or task.task_type == "Fixed" else 0)

        canonical.append(PlanningTask(
            id=task_id,
            title=task.title,
            total_duration_min=total_duration,
            priority=PRIORITY_TO_SCORE.get(task.priority, 50),
            earliest_start=earliest_start,
            deadline=deadline,
            fixed_start=fixed_start,
            fixed_end=fixed_end,
            required_weekdays=required_days,
            preferred_windows=windows,
            hard_earliest_min_of_day=hard_earliest,
            dependencies=(dependency,) if dependency else (),
            min_block_min=max(slot_minutes, int(task.min_block_min or slot_minutes)),
            max_block_min=max(slot_minutes, int(task.max_block_min or total_duration)),
            sessions_required=requested_sessions,
            distinct_session_days=distinct,
            prefer_distinct_session_days=prefer_distinct,
            prefer_same_day_sessions=prefer_same,
            splittable=bool(task.splittable or task.task_type in {"Recurring", "Multi-session"}),
            energy=str(task.energy or "medium").lower(),
            location=_normalize_location(task.location),
            locked=task.task_type == "Fixed",
            daily_sequence_rank=_sequence_rank(task),
            sequence_group="morning" if title_key in {"morning routine", "breakfast"} or task.preferred_time == "Morning" else "",
            transition_after_min=recovery,
            counts_toward_daily_limit=task.category != "Routine" and task.task_type != "Fixed",
            counts_toward_total_burden=True,
            counts_as_focused_work=task.cognitive_load == "High",
        ))

    busy = tuple(CalendarEvent(
        id=f"legacy-event-{index}",
        title=event.title,
        start=_at_day(week_start, int(event.day_index), int(event.start_min)),
        end=_at_day(week_start, int(event.day_index), int(event.end_min)),
        locked=True,
        busy=True,
        source=EventSource.IMPORTED,
        location=_normalize_location(event.location),
        counts_toward_total_burden=True,
    ) for index, event in enumerate(existing_events))

    return PlanRequest(
        horizon_start=week_start,
        horizon_end=week_start + timedelta(days=7),
        tasks=tuple(canonical),
        existing_events=busy,
        slot_minutes=slot_minutes,
        wake_min=wake_min,
        sleep_min=sleep_min,
        protect_weekend=protect_weekend,
        transition_min=transition_min,
        preferred_daily_flexible_min=preferred_daily_flexible_min,
        max_daily_flexible_min=max_daily_flexible_min,
        preferred_daily_total_min=preferred_daily_total_min,
        preferred_daily_focus_min=preferred_daily_focus_min,
        late_focus_start_min=late_focus_start_min,
        default_travel_min=default_travel_min,
        compact_gap_min=compact_gap_min,
        travel_time_overrides=travel_time_overrides,
        timezone=timezone,
    )
