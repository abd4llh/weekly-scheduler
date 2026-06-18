from typing import Dict, List, Tuple

from models import Event, Task
from parser_utils import hhmm_to_minutes, minutes_to_hhmm

ROUTINE_CATEGORY = "Routine"


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
    """Build flexible daily routine requirements from sidebar settings.

    These are windows, not fixed calendar anchors. The AI chooses the exact time
    each day around fixed meetings and the logical flow of the day.
    """
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
            "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
            "duration_min": item["duration_min"],
            "window_start": item["window_start"],
            "window_end": item["window_end"],
            "preferred_start": item["preferred_start"],
            "priority": item["priority"],
            "hard_window": True,
            "fixed_time": False,
            "instruction": (
                "Schedule exactly once per day inside the window. Use preferred_start only when it fits; "
                "fixed and higher-priority events take precedence."
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
                f"Schedule once daily for {item['duration_min']} minutes within "
                f"{item['window_start']}–{item['window_end']}, preferably near {item['preferred_start']}."
            ),
        ))
    return clean


def validate_routine_requirements(events, settings: Dict) -> List[Dict]:
    issues = []
    requirements = routine_requirements_from_settings(settings)
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    for requirement in requirements:
        title = requirement["title"]
        for day in range(7):
            matches = [
                event for event in events
                if event.day_index == day
                and (
                    event.source_task.strip().lower() == title.lower()
                    or event.title.strip().lower() == title.lower()
                )
            ]
            if len(matches) != 1:
                issues.append({
                    "level": "error",
                    "task": title,
                    "message": f"{title} must appear exactly once on {day_names[day]}; found {len(matches)}.",
                })
                continue

            event = matches[0]
            actual_duration = event.end_min - event.start_min
            if actual_duration != int(requirement["duration_min"]):
                issues.append({
                    "level": "error",
                    "task": title,
                    "message": (
                        f"{title} must last {requirement['duration_min']} minutes; "
                        f"scheduled {actual_duration} minutes."
                    ),
                })
            if (
                event.start_min < int(requirement["window_start_min"])
                or event.end_min > int(requirement["window_end_min"])
            ):
                issues.append({
                    "level": "error",
                    "task": title,
                    "message": (
                        f"{title} must fit inside {requirement['window_start']}–{requirement['window_end']}; "
                        f"scheduled {minutes_to_hhmm(event.start_min)}–{minutes_to_hhmm(event.end_min)}."
                    ),
                })
    return issues


def place_routines_flexibly(tasks: List[Task], events: List[Event], settings: Dict) -> Tuple[List[Task], List[Event]]:
    """Fallback routine placement inside windows after all higher-priority events.

    The AI planner is the primary decision-maker. This helper is used only when
    the AI planning call fails and the deterministic scheduler is used instead.
    """
    tasks = normalize_routine_tasks(tasks, settings)
    events = [event for event in events if event.category != ROUTINE_CATEGORY]

    for requirement in routine_requirements_from_settings(settings):
        duration = int(requirement["duration_min"])
        window_start = int(requirement["window_start_min"])
        latest_start = int(requirement["window_end_min"]) - duration
        preferred = int(requirement["preferred_start_min"])
        candidate_starts = list(range(window_start, latest_start + 1, 15))
        candidate_starts.sort(key=lambda start: (abs(start - preferred), start))

        for day in range(7):
            busy = [(event.start_min, event.end_min) for event in events if event.day_index == day]
            chosen = None
            for start in candidate_starts:
                end = start + duration
                if all(end <= busy_start or start >= busy_end for busy_start, busy_end in busy):
                    chosen = start
                    break
            if chosen is None:
                continue

            events.append(Event(
                title=requirement["title"],
                day_index=day,
                start_min=chosen,
                end_min=chosen + duration,
                priority=requirement["priority"],
                source_task=requirement["title"],
                notes=requirement["notes"],
                explanation=(
                    f"Placed inside the flexible {requirement['window_start']}–{requirement['window_end']} "
                    f"window while avoiding fixed and higher-priority events."
                ),
                category=ROUTINE_CATEGORY,
            ))

    events.sort(key=lambda event: (event.day_index, event.start_min, event.end_min, event.title))
    return tasks, events
