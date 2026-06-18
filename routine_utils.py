from typing import Dict, List, Tuple

from models import Event, Task
from parser_utils import hhmm_to_minutes, minutes_to_hhmm

ROUTINE_CATEGORY = "Routine"


def _to_min(value, default):
    if isinstance(value, int):
        return value
    parsed = hhmm_to_minutes(str(value or ""))
    return default if parsed is None else parsed


def routine_specs_from_settings(settings: Dict) -> List[Dict]:
    wake_min = int(settings.get("wake_min", 360))
    sleep_min = int(settings.get("sleep_min", 1380))
    specs = []

    if settings.get("morning_ramp_enabled", True):
        duration = max(15, min(int(settings.get("morning_ramp_min", 60)), 180))
        specs.append({
            "title": "Morning routine",
            "start_min": wake_min,
            "duration_min": duration,
            "priority": "Medium",
            "preferred_time": "Morning",
            "notes": "Automatic wake-up, hygiene, preparation, and breakfast-prep buffer.",
        })

    meal_defaults = [
        ("Breakfast", "breakfast_enabled", "breakfast_time", "07:30", "breakfast_duration_min", 30, "Morning"),
        ("Lunch", "lunch_enabled", "lunch_time", "13:00", "lunch_duration_min", 45, "Afternoon"),
        ("Dinner", "dinner_enabled", "dinner_time", "19:00", "dinner_duration_min", 60, "Evening"),
    ]
    for title, enabled_key, time_key, default_time, duration_key, default_duration, preferred_time in meal_defaults:
        if not settings.get(enabled_key, False):
            continue
        start = _to_min(settings.get(time_key), _to_min(default_time, 0))
        duration = max(15, min(int(settings.get(duration_key, default_duration)), 180))
        specs.append({
            "title": title,
            "start_min": start,
            "duration_min": duration,
            "priority": "Medium",
            "preferred_time": preferred_time,
            "notes": f"Automatic daily {title.lower()} block selected in plan settings.",
        })

    if settings.get("wind_down_enabled", False):
        duration = max(15, min(int(settings.get("wind_down_min", 30)), 120))
        specs.append({
            "title": "Evening wind-down",
            "start_min": max(wake_min, sleep_min - duration),
            "duration_min": duration,
            "priority": "Medium",
            "preferred_time": "Evening",
            "notes": "Automatic low-stimulation wind-down before sleep.",
        })

    valid = []
    for spec in specs:
        start = int(spec["start_min"])
        end = start + int(spec["duration_min"])
        if 0 <= start < end <= 1440:
            spec["end_min"] = end
            spec["start"] = minutes_to_hhmm(start)
            spec["end"] = minutes_to_hhmm(end)
            spec["days"] = list(range(7))
            valid.append(spec)
    return valid


def routine_anchor_payload(settings: Dict) -> List[Dict]:
    return [
        {
            "title": spec["title"],
            "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
            "start": spec["start"],
            "end": spec["end"],
            "duration_min": spec["duration_min"],
            "hard_constraint": True,
        }
        for spec in routine_specs_from_settings(settings)
    ]


def inject_routine_blocks(tasks: List[Task], events: List[Event], settings: Dict) -> Tuple[List[Task], List[Event]]:
    specs = routine_specs_from_settings(settings)
    titles = {spec["title"] for spec in specs}

    clean_tasks = [task for task in tasks if task.category != ROUTINE_CATEGORY and task.title not in titles]
    clean_events = [event for event in events if event.category != ROUTINE_CATEGORY and event.source_task not in titles]

    for spec in specs:
        task = Task(
            title=spec["title"],
            duration_min=int(spec["duration_min"]),
            priority=spec["priority"],
            task_type="Recurring",
            sessions_per_week=7,
            fixed_start=spec["start"],
            preferred_time=spec["preferred_time"],
            energy="Low",
            location="Home",
            splittable=False,
            min_block_min=int(spec["duration_min"]),
            max_block_min=int(spec["duration_min"]),
            can_overlap=False,
            notes=spec["notes"],
            category=ROUTINE_CATEGORY,
            confidence=1.0,
            duration_is_estimated=False,
            assumptions="Added from sidebar routine settings.",
        )
        clean_tasks.append(task)
        for day in range(7):
            clean_events.append(Event(
                title=spec["title"],
                day_index=day,
                start_min=int(spec["start_min"]),
                end_min=int(spec["end_min"]),
                priority=spec["priority"],
                source_task=spec["title"],
                notes=spec["notes"],
                explanation="Automatically reserved from the user's daily routine settings.",
                category=ROUTINE_CATEGORY,
            ))

    clean_events.sort(key=lambda event: (event.day_index, event.start_min, event.end_min, event.title))
    return clean_tasks, clean_events
