import json
import re
from dataclasses import asdict, replace
from datetime import datetime
from typing import List, Optional

from models import DAY_NAMES, DAY_TO_INDEX, Task

DEFAULT_TASKS = ""


def minutes_to_hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def hhmm_to_minutes(s: str) -> Optional[int]:
    if not isinstance(s, str) or not s.strip():
        return None
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if 0 <= h <= 23 and 0 <= mi <= 59 else None


def _bump_priority(priority: str) -> str:
    if priority == "High":
        return "Critical"
    if priority == "Medium":
        return "High"
    return priority


def adapt_tasks_for_mood(tasks: List[Task], mood: str) -> List[Task]:
    """Apply generic mood/energy adjustments to the task table.

    This function intentionally uses general task metadata, not person-specific or
    task-name-specific rules.
    """
    adjusted = []
    for task in tasks:
        t = replace(task)

        if mood == "Productive":
            if t.energy == "High" or t.category in ["Work", "Lab", "Writing", "Admin"]:
                t.priority = _bump_priority(t.priority)
                if t.preferred_time == "Any":
                    t.preferred_time = "Workday"

        elif mood == "Creative":
            if t.energy == "Creative" or t.category in ["Writing", "Learning", "Optional"]:
                t.priority = _bump_priority(t.priority)
                if t.preferred_time == "Any":
                    t.preferred_time = "Afternoon"

        elif mood == "Tired":
            if t.energy == "High":
                t.max_block_min = min(int(t.max_block_min), 90)
            if t.category in ["Home", "Admin"] and t.priority in ["Low", "Medium"]:
                t.priority = _bump_priority(t.priority)

        elif mood == "Physically energetic":
            if t.energy == "Physical" or t.category == "Health":
                t.priority = _bump_priority(t.priority)
                t.preferred_time = "Morning"

        elif mood == "Low motivation":
            t.max_block_min = min(int(t.max_block_min), 90)
            t.min_block_min = min(int(t.min_block_min), 30)
            if t.category in ["Admin", "Home"] and t.priority in ["Low", "Medium"]:
                t.priority = _bump_priority(t.priority)

        if t.max_block_min < t.min_block_min:
            t.max_block_min = t.min_block_min

        adjusted.append(t)

    return adjusted


def validate_tasks(tasks: List[Task], wake_min: int, sleep_min: int):
    issues = []
    fixed = []

    if wake_min >= sleep_min:
        issues.append({"level": "error", "task": "Settings", "message": "Wake time must be earlier than sleep target."})

    for task in tasks:
        if not str(task.title).strip():
            issues.append({"level": "error", "task": "Untitled", "message": "Task has no title."})

        if int(task.duration_min) <= 0:
            issues.append({"level": "error", "task": task.title, "message": "Duration must be greater than zero."})

        if int(task.min_block_min) <= 0 or int(task.max_block_min) <= 0:
            issues.append({"level": "error", "task": task.title, "message": "Block sizes must be positive."})

        if int(task.max_block_min) < int(task.min_block_min):
            issues.append({"level": "warning", "task": task.title, "message": "max_block_min is smaller than min_block_min."})

        if task.task_type == "Fixed":
            day = DAY_TO_INDEX.get(str(task.fixed_day).lower())
            start = hhmm_to_minutes(str(task.fixed_start))

            if day is None:
                issues.append({"level": "error", "task": task.title, "message": "Fixed task needs a valid day."})

            if start is None:
                issues.append({"level": "error", "task": task.title, "message": "Fixed task needs a valid time, e.g. 14:00."})
            elif day is not None:
                end = start + int(task.duration_min)
                if start < wake_min or end > sleep_min:
                    issues.append({
                        "level": "warning",
                        "task": task.title,
                        "message": f"Fixed event {minutes_to_hhmm(start)}–{minutes_to_hhmm(end)} is outside wake/sleep window.",
                    })
                if not task.can_overlap:
                    fixed.append((day, start, end, task.title))

        if task.task_type == "Recurring" and int(task.sessions_per_week) <= 1:
            issues.append({"level": "warning", "task": task.title, "message": "Recurring task has only one session per week."})

        if task.task_type == "Flexible" and int(task.sessions_per_week) > 1:
            issues.append({"level": "warning", "task": task.title, "message": "Flexible task has multiple sessions; recurring or multi-session may fit better."})

        if getattr(task, "needs_clarification", False):
            question = getattr(task, "clarification_question", "") or "This task needs clarification before scheduling."
            issues.append({"level": "warning", "task": task.title, "message": question})

    for i, a in enumerate(fixed):
        for b in fixed[i + 1:]:
            if a[0] == b[0] and max(a[1], b[1]) < min(a[2], b[2]):
                issues.append({
                    "level": "error",
                    "task": f"{a[3]} / {b[3]}",
                    "message": f"Fixed-event conflict on {DAY_NAMES[a[0]]}: {minutes_to_hhmm(a[1])}–{minutes_to_hhmm(a[2])} overlaps {minutes_to_hhmm(b[1])}–{minutes_to_hhmm(b[2])}.",
                })

    return issues


def tasks_to_json(tasks: List[Task]) -> str:
    return json.dumps(
        {
            "version": "0.8.1",
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "tasks": [asdict(task) for task in tasks],
        },
        indent=2,
        ensure_ascii=False,
    )


def tasks_from_json(data: str) -> List[Task]:
    payload = json.loads(data)
    rows = payload if isinstance(payload, list) else payload.get("tasks", [])
    fields = Task.__dataclass_fields__
    tasks = []
    for row in rows:
        clean = {key: row.get(key, field.default) for key, field in fields.items()}
        tasks.append(Task(**clean))
    return tasks
