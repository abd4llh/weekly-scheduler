from typing import Dict, List, Tuple

from models import DAY_NAMES, Event, Task, UnscheduledTask
from parser_utils import hhmm_to_minutes, minutes_to_hhmm

ROUTINE_CATEGORY = "Routine"
ROUTINE_FALLBACK_PREFIX = "Scheduled outside the preferred window because"


def _to_min(value, default):
    if isinstance(value, int):
        return value
    parsed = hhmm_to_minutes(str(value or ""))
    return default if parsed is None else parsed


def _clamp_window(start_min: int, end_min: int, wake_min: int, sleep_min: int):
    start = max(wake_min, min(start_min, sleep_min))
    end = max(start, min(end_min, sleep_min))
    return start, end


def routine_requirements_from_settings(settings: Dict) -> List[Dict]:
    """Build flexible daily routine preferences from sidebar settings."""
    wake_min = int(settings.get("wake_min", 360))
    sleep_min = int(settings.get("sleep_min", 1380))
    requirements = []

    if settings.get("morning_ramp_enabled", True):
        duration = max(15, min(int(settings.get("morning_ramp_min", 60)), 180))
        window_end = min(sleep_min, wake_min + max(duration, 120))
        requirements.append({
            "title": "Morning routine",
            "duration_min": duration,
            "window_start_min": wake_min,
            "window_end_min": window_end,
            "preferred_start_min": wake_min,
            "priority": "Medium",
            "preferred_time": "Morning",
            "notes": "Wake-up, hygiene, getting ready, and a gentle start to the day.",
        })

    meal_defaults = [
        (
            "Breakfast",
            "breakfast_enabled",
            "breakfast_window_start",
            "06:30",
            "breakfast_window_end",
            "09:30",
            "breakfast_preferred_time",
            "08:00",
            "breakfast_duration_min",
            30,
            "Morning",
        ),
        (
            "Lunch",
            "lunch_enabled",
            "lunch_window_start",
            "11:00",
            "lunch_window_end",
            "14:00",
            "lunch_preferred_time",
            "13:00",
            "lunch_duration_min",
            45,
            "Afternoon",
        ),
        (
            "Dinner",
            "dinner_enabled",
            "dinner_window_start",
            "18:00",
            "dinner_window_end",
            "21:00",
            "dinner_preferred_time",
            "19:00",
            "dinner_duration_min",
            60,
            "Evening",
        ),
    ]

    for (
        title,
        enabled_key,
        start_key,
        default_start,
        end_key,
        default_end,
        preferred_key,
        default_preferred,
        duration_key,
        default_duration,
        preferred_time,
    ) in meal_defaults:
        if not settings.get(enabled_key, False):
            continue

        start = _to_min(settings.get(start_key), _to_min(default_start, wake_min))
        end = _to_min(settings.get(end_key), _to_min(default_end, sleep_min))
        preferred = _to_min(settings.get(preferred_key), _to_min(default_preferred, start))
        duration = max(15, min(int(settings.get(duration_key, default_duration)), 180))

        start, end = _clamp_window(start, end, wake_min, sleep_min)
        if end - start < duration:
            end = min(sleep_min, start + duration)
        preferred = min(max(preferred, start), max(start, end - duration))

        requirements.append({
            "title": title,
            "duration_min": duration,
            "window_start_min": start,
            "window_end_min": end,
            "preferred_start_min": preferred,
            "priority": "Medium",
            "preferred_time": preferred_time,
            "notes": f"Flexible daily {title.lower()} routine selected in plan settings.",
        })

    if settings.get("wind_down_enabled", False):
        duration = max(15, min(int(settings.get("wind_down_min", 30)), 120))
        window_start = max(wake_min, sleep_min - max(120, duration))
        requirements.append({
            "title": "Evening wind-down",
            "duration_min": duration,
            "window_start_min": window_start,
            "window_end_min": sleep_min,
            "preferred_start_min": max(window_start, sleep_min - duration),
            "priority": "Medium",
            "preferred_time": "Evening",
            "notes": "Flexible low-stimulation wind-down before sleep.",
        })

    for requirement in requirements:
        requirement["window_start"] = minutes_to_hhmm(requirement["window_start_min"])
        requirement["window_end"] = minutes_to_hhmm(requirement["window_end_min"])
        requirement["preferred_start"] = minutes_to_hhmm(requirement["preferred_start_min"])
        requirement["days"] = list(range(7))
    return requirements


def routine_requirements_payload(settings: Dict) -> List[Dict]:
    return [
        {
            "title": item["title"],
            "days": DAY_NAMES,
            "duration_min": item["duration_min"],
            "window_start": item["window_start"],
            "window_end": item["window_end"],
            "preferred_start": item["preferred_start"],
            "priority": item["priority"],
            "preferred_window": True,
            "fixed_time": False,
            "allow_nearest_fallback": True,
            "instruction": (
                "Schedule exactly once per day inside the preferred window when possible. "
                "If the entire window is blocked by a fixed or higher-priority event, schedule it "
                "at the nearest free time after that event; use a time before the window only when "
                "nothing is available afterward."
            ),
        }
        for item in routine_requirements_from_settings(settings)
    ]


def normalize_routine_tasks(tasks: List[Task], settings: Dict) -> List[Task]:
    requirements = {item["title"]: item for item in routine_requirements_from_settings(settings)}
    clean = [task for task in tasks if task.category != ROUTINE_CATEGORY and task.title not in requirements]

    for title, item in requirements.items():
        clean.append(Task(
            title=title,
            duration_min=int(item["duration_min"]),
            priority=item["priority"],
            task_type="Recurring",
            sessions_per_week=7,
            preferred_time=item["preferred_time"],
            energy="Low",
            location="Home",
            splittable=False,
            min_block_min=int(item["duration_min"]),
            max_block_min=int(item["duration_min"]),
            can_overlap=False,
            notes=item["notes"],
            category=ROUTINE_CATEGORY,
            confidence=1.0,
            duration_is_estimated=False,
            assumptions=(
                f"Schedule once daily for {item['duration_min']} minutes, preferably within "
                f"{item['window_start']}–{item['window_end']} near {item['preferred_start']}; "
                "move to the nearest later slot if the full window is blocked."
            ),
        ))
    return clean


def _matches_routine(event: Event, title: str, day: int = None) -> bool:
    if day is not None and event.day_index != day:
        return False
    return (
        event.source_task.strip().lower() == title.lower()
        or event.title.strip().lower() == title.lower()
    )


def _slot_is_free(day: int, start: int, end: int, events: List[Event], buffer_min: int = 0) -> bool:
    for event in events:
        if event.day_index != day:
            continue
        busy_start = event.start_min - buffer_min
        busy_end = event.end_min + buffer_min
        if max(start, busy_start) < min(end, busy_end):
            return False
    return True


def _blocking_event_titles(day: int, start: int, end: int, events: List[Event]) -> List[str]:
    titles = []
    for event in events:
        if event.day_index != day:
            continue
        if max(start, event.start_min) < min(end, event.end_min):
            if event.title not in titles:
                titles.append(event.title)
    return titles


def _candidate_starts(start: int, latest_start: int, preferred: int = None, reverse: bool = False) -> List[int]:
    if latest_start < start:
        return []
    candidates = list(range(start, latest_start + 1, 15))
    if preferred is not None:
        candidates.sort(key=lambda value: (abs(value - preferred), value))
    elif reverse:
        candidates.reverse()
    return candidates


def _find_free_start(
    day: int,
    duration: int,
    events: List[Event],
    candidates: List[int],
    buffer_min: int = 0,
) -> int:
    for start in candidates:
        if _slot_is_free(day, start, start + duration, events, buffer_min):
            return start
    if buffer_min:
        for start in candidates:
            if _slot_is_free(day, start, start + duration, events, 0):
                return start
    return None


def _append_routine_event(
    events: List[Event],
    requirement: Dict,
    day: int,
    start: int,
    explanation: str,
):
    events.append(Event(
        title=requirement["title"],
        day_index=day,
        start_min=start,
        end_min=start + int(requirement["duration_min"]),
        priority=requirement["priority"],
        source_task=requirement["title"],
        notes=requirement["notes"],
        explanation=explanation,
        category=ROUTINE_CATEGORY,
    ))


def repair_routine_windows(
    tasks: List[Task],
    events: List[Event],
    unscheduled: List[UnscheduledTask],
    settings: Dict,
) -> Tuple[List[Task], List[Event], List[UnscheduledTask]]:
    """Prefer routine windows, but keep the routine using the nearest fallback.

    A blocked meal window does not delete the meal. The routine is placed at the
    nearest free time after the window/blocking event. A slot before the window
    is used only when no later slot exists before sleep.
    """
    tasks = normalize_routine_tasks(list(tasks), settings)
    events = list(events)
    requirements = routine_requirements_from_settings(settings)
    routine_titles = {item["title"] for item in requirements}
    wake_min = int(settings.get("wake_min", 360))
    sleep_min = int(settings.get("sleep_min", 1380))
    transition = max(0, min(int(settings.get("transition_min", 0)), 30))

    unscheduled = [
        item for item in unscheduled
        if not any(
            item.title == title or item.title.startswith(f"{title} (")
            for title in routine_titles
        )
    ]

    for requirement in requirements:
        title = requirement["title"]
        duration = int(requirement["duration_min"])
        window_start = int(requirement["window_start_min"])
        window_end = int(requirement["window_end_min"])
        preferred = int(requirement["preferred_start_min"])

        for day in range(7):
            matches = [event for event in events if _matches_routine(event, title, day)]
            other_events = [event for event in events if event not in matches]

            valid_matches = [
                event for event in matches
                if event.end_min - event.start_min == duration
                and event.start_min >= window_start
                and event.end_min <= window_end
                and _slot_is_free(day, event.start_min, event.end_min, other_events)
            ]

            if valid_matches:
                keep = min(valid_matches, key=lambda event: abs(event.start_min - preferred))
                events = [event for event in events if event not in matches or event is keep]
                continue

            events = [event for event in events if event not in matches]

            in_window = _candidate_starts(
                window_start,
                window_end - duration,
                preferred=preferred,
            )
            chosen = _find_free_start(day, duration, events, in_window)
            if chosen is not None:
                _append_routine_event(
                    events,
                    requirement,
                    day,
                    chosen,
                    (
                        f"Placed inside the preferred {requirement['window_start']}–"
                        f"{requirement['window_end']} window while preserving fixed and "
                        "higher-priority events."
                    ),
                )
                continue

            blockers = _blocking_event_titles(day, window_start, window_end, events)
            blocker_text = ", ".join(blockers) if blockers else "other scheduled commitments"

            after_candidates = _candidate_starts(window_end, sleep_min - duration)
            chosen = _find_free_start(
                day,
                duration,
                events,
                after_candidates,
                buffer_min=transition,
            )
            if chosen is not None:
                _append_routine_event(
                    events,
                    requirement,
                    day,
                    chosen,
                    (
                        f"{ROUTINE_FALLBACK_PREFIX} the preferred "
                        f"{requirement['window_start']}–{requirement['window_end']} window was "
                        f"blocked by {blocker_text}; placed at the nearest available later time."
                    ),
                )
                continue

            before_candidates = _candidate_starts(
                wake_min,
                window_start - duration,
                reverse=True,
            )
            chosen = _find_free_start(
                day,
                duration,
                events,
                before_candidates,
                buffer_min=transition,
            )
            if chosen is not None:
                _append_routine_event(
                    events,
                    requirement,
                    day,
                    chosen,
                    (
                        f"{ROUTINE_FALLBACK_PREFIX} the preferred "
                        f"{requirement['window_start']}–{requirement['window_end']} window and all "
                        "later time were unavailable; placed at the nearest available earlier time."
                    ),
                )
                continue

            unscheduled.append(UnscheduledTask(
                title=f"{title} ({DAY_NAMES[day]})",
                reason=(
                    f"No {duration}-minute slot exists inside or outside the preferred "
                    f"{requirement['window_start']}–{requirement['window_end']} window before sleep."
                ),
                task_type="Recurring",
                priority=requirement["priority"],
                duration_min=duration,
                notes=requirement["notes"],
                category=ROUTINE_CATEGORY,
            ))

        routine_task = next((task for task in tasks if task.title == title), None)
        if routine_task:
            routine_task.sessions_per_week = len([
                event for event in events if _matches_routine(event, title)
            ])

    events.sort(key=lambda event: (event.day_index, event.start_min, event.end_min, event.title))
    return tasks, events, unscheduled


def validate_routine_requirements(events, settings: Dict) -> List[Dict]:
    issues = []
    requirements = routine_requirements_from_settings(settings)
    sleep_min = int(settings.get("sleep_min", 1380))

    for requirement in requirements:
        title = requirement["title"]
        duration = int(requirement["duration_min"])
        window_start = int(requirement["window_start_min"])
        window_end = int(requirement["window_end_min"])

        for day in range(7):
            matches = [event for event in events if _matches_routine(event, title, day)]
            if len(matches) == 0:
                continue
            if len(matches) > 1:
                issues.append({
                    "level": "error",
                    "task": title,
                    "message": f"{title} appears more than once on {DAY_NAMES[day]}.",
                })
                continue

            event = matches[0]
            actual_duration = event.end_min - event.start_min
            if actual_duration != duration:
                issues.append({
                    "level": "error",
                    "task": title,
                    "message": (
                        f"{title} must last {duration} minutes; scheduled {actual_duration} minutes."
                    ),
                })

            inside_window = event.start_min >= window_start and event.end_min <= window_end
            if inside_window:
                continue

            other_events = [candidate for candidate in events if candidate is not event]
            in_window_candidates = _candidate_starts(
                window_start,
                window_end - duration,
                preferred=int(requirement["preferred_start_min"]),
            )
            free_in_window = _find_free_start(
                day,
                duration,
                other_events,
                in_window_candidates,
            )

            valid_later_fallback = (
                free_in_window is None
                and event.start_min >= window_end
                and event.end_min <= sleep_min
            )
            valid_earlier_fallback = (
                free_in_window is None
                and event.end_min <= window_start
            )

            if not (valid_later_fallback or valid_earlier_fallback):
                issues.append({
                    "level": "error",
                    "task": title,
                    "message": (
                        f"{title} is outside the preferred {requirement['window_start']}–"
                        f"{requirement['window_end']} window even though a valid preferred or "
                        "fallback placement was available."
                    ),
                })
    return issues


def place_routines_flexibly(tasks: List[Task], events: List[Event], settings: Dict) -> Tuple[List[Task], List[Event]]:
    """Fallback routine placement after higher-priority events."""
    tasks, events, _ = repair_routine_windows(tasks, events, [], settings)
    return tasks, events
