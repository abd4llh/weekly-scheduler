import json
import re
from dataclasses import asdict
from typing import Dict, List, Tuple

from openai import OpenAI

from models import CATEGORIES, DAY_NAMES, DAY_TO_INDEX, Event, Task, UnscheduledTask
from parser_utils import hhmm_to_minutes, minutes_to_hhmm

PRIORITIES = ["Critical", "High", "Medium", "Low", "Optional"]
TASK_TYPES = ["Fixed", "Flexible", "Recurring", "Multi-session"]
PREFERRED_TIMES = ["Morning", "Workday", "Afternoon", "Evening", "Weekend", "Any"]
ENERGIES = ["High", "Medium", "Low", "Physical", "Creative"]
LOCATIONS = ["Lab", "Home", "Gym", "Any"]

AI_PLANNER_PROMPT = """
You are the planning brain of a public weekly scheduling app.

Users may write with grammar mistakes, spelling mistakes, missing punctuation, informal wording, transliteration, or mixed languages. They may switch language mid-sentence. Understand intent, not only exact keywords.

Create a complete weekly calendar from messy user text.

You receive:
1. The original raw user text. Trust this most.
2. Parsed task hints. Treat explicit durations, fixed times, recurrence counts, dependencies, and day/time constraints in these hints as anchors unless the raw text clearly contradicts them.
3. User settings such as wake time, sleep time, planning mode, and weekend protection.
4. Previous validation errors during repair passes, if any.

Return ONLY valid JSON with this shape:
{
  "tasks": [{
    "title": "short clean title",
    "duration_min": 60,
    "priority": "Critical|High|Medium|Low|Optional",
    "task_type": "Fixed|Flexible|Recurring|Multi-session",
    "sessions_per_week": 1,
    "fixed_day": "",
    "fixed_start": "",
    "preferred_time": "Morning|Workday|Afternoon|Evening|Weekend|Any",
    "energy": "High|Medium|Low|Physical|Creative",
    "location": "Lab|Home|Gym|Any",
    "splittable": true,
    "min_block_min": 30,
    "max_block_min": 180,
    "can_overlap": false,
    "category": "Work|Lab|Writing|Admin|Health|Home|Relationship|Social|Learning|Optional|Focus|Other",
    "required_day": "",
    "earliest_day": "",
    "deadline_day": "",
    "deadline_time": "",
    "depends_on": "",
    "phase": 0,
    "notes": "brief source/assumption",
    "confidence": 0.85,
    "duration_is_estimated": true,
    "assumptions": "brief assumptions made",
    "needs_clarification": false,
    "clarification_question": ""
  }],
  "events": [{
    "title": "calendar event title",
    "source_task": "exact task title from tasks",
    "day": "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday",
    "start": "HH:MM",
    "end": "HH:MM",
    "priority": "Critical|High|Medium|Low|Optional",
    "category": "Work|Lab|Writing|Admin|Health|Home|Relationship|Social|Learning|Optional|Focus|Other",
    "notes": "brief note",
    "explanation": "why this time was selected"
  }],
  "unscheduled": [{"title": "task title", "reason": "why it could not be scheduled"}],
  "warnings": []
}

Hard rules:
- Preserve fixed events exactly when the user gives an exact day and exact time.
- Do not overlap events unless a task can clearly overlap.
- Keep events inside wake/sleep time.
- Respect explicit total durations exactly. If the user says 12 hours, schedule exactly 12 hours total.
- Do not silently change task durations from the parsed task hints.
- For Recurring tasks, duration_min is per session; sessions_per_week is the count.
- For Multi-session tasks, duration_min is the total weekly duration; split it into useful blocks.
- If the user says "twice this week", create two sessions.
- If the user says "every morning", schedule one morning session per day.
- If the user says "Sunday afternoon", schedule on Sunday during the afternoon, not Sunday morning.
- If task B logically happens after task A, schedule B after A is complete.
- Do not invent unnecessary deadlines or dependencies.
- If the prompt contains multiple languages, preserve task meaning in concise English titles.

Time-of-day interpretation:
- Morning: 06:00-12:00
- Afternoon: 12:00-18:00
- Evening: 17:00-22:00
- Workday: Monday-Friday, normally 09:00-17:00
- Weekend: Saturday-Sunday

Use realistic defaults when duration is missing:
- Exercise/gym: 60-120 min.
- Cooking/meal prep: 45-90 min.
- Social/relationship call: 30-90 min.
- Admin/social-media update: 30-90 min.
- Quiet planning/rest: 60-120 min.
"""

REPAIR_PROMPT = """
The previous plan failed deterministic validation. Repair the JSON schedule. Keep the user's intent, preserve parsed task duration anchors, keep fixed events fixed, and satisfy the listed validation errors. Return ONLY valid JSON with the same shape.
"""


def _pick(value, allowed, default):
    return value if value in allowed else default


def _to_int(value, default, lo=None, hi=None):
    try:
        out = int(value)
    except Exception:
        out = default
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def _to_float(value, default=0.8):
    try:
        out = float(value)
    except Exception:
        out = default
    return max(0.0, min(1.0, out))


def _to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ["true", "yes", "1"]
    return default


def _clean_day(value):
    value = str(value or "").strip()
    return value if value in DAY_NAMES else ""


def _clean_hhmm(value):
    value = str(value or "").strip()
    return value if hhmm_to_minutes(value) is not None else ""


def _norm_tokens(text: str):
    words = re.findall(r"[a-zA-Z0-9]+", str(text).lower())
    stop = {"the", "and", "for", "with", "this", "that", "task", "work", "session", "sessions", "daily", "every", "main"}
    return {word for word in words if len(word) > 2 and word not in stop}


def _match_score(a: Task, b: Task) -> float:
    a_tokens = _norm_tokens(f"{a.title} {a.notes}")
    b_tokens = _norm_tokens(f"{b.title} {b.notes}")
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


def _best_task_match(anchor: Task, tasks: List[Task]):
    exact = [task for task in tasks if task.title.strip().lower() == anchor.title.strip().lower()]
    if exact:
        return exact[0], 1.0
    scored = sorted(((_match_score(anchor, task), task) for task in tasks), reverse=True, key=lambda item: item[0])
    if not scored:
        return None, 0.0
    score, task = scored[0]
    return task, score


def task_from_dict(item: Dict) -> Task:
    duration = _to_int(item.get("duration_min"), 60, 1, 1440)
    min_block = _to_int(item.get("min_block_min"), min(30, duration), 1, duration)
    max_block = _to_int(item.get("max_block_min"), max(min_block, min(duration, 180)), min_block, 1440)
    return Task(
        title=str(item.get("title") or "Untitled task")[:120],
        duration_min=duration,
        priority=_pick(item.get("priority"), PRIORITIES, "Medium"),
        task_type=_pick(item.get("task_type"), TASK_TYPES, "Flexible"),
        sessions_per_week=_to_int(item.get("sessions_per_week"), 1, 1, 7),
        fixed_day=_clean_day(item.get("fixed_day")),
        fixed_start=_clean_hhmm(item.get("fixed_start")),
        preferred_time=_pick(item.get("preferred_time"), PREFERRED_TIMES, "Any"),
        energy=_pick(item.get("energy"), ENERGIES, "Medium"),
        location=_pick(item.get("location"), LOCATIONS, "Any"),
        splittable=_to_bool(item.get("splittable"), duration > 90),
        min_block_min=min_block,
        max_block_min=max_block,
        can_overlap=_to_bool(item.get("can_overlap"), False),
        notes=str(item.get("notes") or item.get("title") or ""),
        category=_pick(item.get("category"), CATEGORIES, "Other"),
        required_day=_clean_day(item.get("required_day")),
        earliest_day=_clean_day(item.get("earliest_day")),
        deadline_day=_clean_day(item.get("deadline_day")),
        deadline_time=_clean_hhmm(item.get("deadline_time")),
        depends_on=str(item.get("depends_on") or "").strip(),
        phase=_to_int(item.get("phase"), 0, 0, 9),
        confidence=_to_float(item.get("confidence"), 0.8),
        duration_is_estimated=_to_bool(item.get("duration_is_estimated"), True),
        assumptions=str(item.get("assumptions") or ""),
        needs_clarification=_to_bool(item.get("needs_clarification"), False),
        clarification_question=str(item.get("clarification_question") or ""),
    )


def event_from_dict(item: Dict, task_by_title: Dict[str, Task]) -> Event:
    source = str(item.get("source_task") or item.get("title") or "").strip()
    task = task_by_title.get(source)
    title = str(item.get("title") or source or "Untitled event")[:120]
    day = DAY_TO_INDEX.get(str(item.get("day") or "").lower(), 0)
    start = hhmm_to_minutes(str(item.get("start") or ""))
    end = hhmm_to_minutes(str(item.get("end") or ""))
    if start is None:
        start = 9 * 60
    if end is None or end <= start:
        end = start + 60
    return Event(
        title=title,
        day_index=max(0, min(6, day)),
        start_min=start,
        end_min=end,
        priority=_pick(item.get("priority"), PRIORITIES, task.priority if task else "Medium"),
        source_task=source,
        notes=str(item.get("notes") or (task.notes if task else "")),
        explanation=str(item.get("explanation") or "AI-planned event validated by schedule checker."),
        category=_pick(item.get("category"), CATEGORIES, task.category if task else "Other"),
    )


def unscheduled_from_dict(item: Dict, task_by_title: Dict[str, Task]) -> UnscheduledTask:
    title = str(item.get("title") or "Unscheduled task")[:120]
    task = task_by_title.get(title)
    return UnscheduledTask(
        title=title,
        reason=str(item.get("reason") or "AI planner could not place this task."),
        task_type=task.task_type if task else "",
        priority=task.priority if task else "",
        duration_min=task.duration_min if task else 0,
        notes=task.notes if task else "",
        category=task.category if task else "Other",
    )


def expected_minutes(task: Task) -> int:
    if task.task_type == "Recurring":
        return int(task.duration_min) * int(task.sessions_per_week)
    return int(task.duration_min)


def time_pref_ok(task: Task, event: Event) -> bool:
    pref = task.preferred_time
    if pref == "Morning":
        return event.start_min < 12 * 60 and event.end_min <= 13 * 60
    if pref == "Afternoon":
        return event.start_min >= 12 * 60 and event.start_min < 18 * 60
    if pref == "Evening":
        return event.start_min >= 17 * 60 and event.start_min < 22 * 60
    if pref == "Workday":
        return event.day_index <= 4 and event.start_min >= 8 * 60 and event.start_min < 18 * 60
    if pref == "Weekend":
        return event.day_index in [5, 6]
    return True


def _events_by_source(events: List[Event]) -> Dict[str, List[Event]]:
    out: Dict[str, List[Event]] = {}
    for event in events:
        out.setdefault(event.source_task, []).append(event)
    return out


def _scheduled_minutes(events: List[Event]) -> int:
    return sum(event.end_min - event.start_min for event in events)


def validate_ai_plan(tasks: List[Task], events: List[Event], unscheduled: List[UnscheduledTask], wake_min: int, sleep_min: int, anchors: List[Task] = None) -> List[Dict]:
    issues = []
    task_by_title = {task.title: task for task in tasks}
    unscheduled_titles = {item.title for item in unscheduled}

    for event in events:
        if event.source_task not in task_by_title:
            issues.append({"level": "error", "task": event.title, "message": f"Event source_task '{event.source_task}' does not match any task title."})
        if event.start_min < wake_min or event.end_min > sleep_min:
            issues.append({"level": "error", "task": event.title, "message": "Event is outside wake/sleep limits."})
        if event.end_min <= event.start_min:
            issues.append({"level": "error", "task": event.title, "message": "Event end time must be after start time."})

    for day in range(7):
        day_events = sorted([event for event in events if event.day_index == day], key=lambda event: event.start_min)
        for a, b in zip(day_events, day_events[1:]):
            if max(a.start_min, b.start_min) < min(a.end_min, b.end_min):
                ta = task_by_title.get(a.source_task)
                tb = task_by_title.get(b.source_task)
                if not ((ta and ta.can_overlap) or (tb and tb.can_overlap)):
                    issues.append({"level": "error", "task": f"{a.title} / {b.title}", "message": f"Overlap on {DAY_NAMES[day]} {minutes_to_hhmm(max(a.start_min, b.start_min))}-{minutes_to_hhmm(min(a.end_min, b.end_min))}."})

    events_by_source = _events_by_source(events)

    for task in tasks:
        task_events = events_by_source.get(task.title, [])
        if not task_events and task.title not in unscheduled_titles:
            issues.append({"level": "error", "task": task.title, "message": "Task has no scheduled events and is not listed as unscheduled."})
            continue

        if task.task_type == "Fixed" and task_events:
            expected_day = DAY_TO_INDEX.get(task.fixed_day.lower()) if task.fixed_day else None
            expected_start = hhmm_to_minutes(task.fixed_start)
            first = sorted(task_events, key=lambda event: (event.day_index, event.start_min))[0]
            if expected_day is not None and first.day_index != expected_day:
                issues.append({"level": "error", "task": task.title, "message": "Fixed task was placed on the wrong day."})
            if expected_start is not None and first.start_min != expected_start:
                issues.append({"level": "error", "task": task.title, "message": "Fixed task was placed at the wrong start time."})

        if task.required_day and task_events:
            required = DAY_TO_INDEX.get(task.required_day.lower())
            if required is not None and any(event.day_index != required for event in task_events):
                issues.append({"level": "error", "task": task.title, "message": f"Task must be scheduled on {task.required_day}."})

        if task.required_day and task.preferred_time in ["Morning", "Afternoon", "Evening"]:
            if any(not time_pref_ok(task, event) for event in task_events):
                issues.append({"level": "error", "task": task.title, "message": f"Task says {task.required_day} {task.preferred_time.lower()}, but at least one event is outside that time window."})

        if task.task_type == "Recurring" and task_events and len(task_events) != int(task.sessions_per_week):
            issues.append({"level": "error", "task": task.title, "message": f"Recurring task needs {task.sessions_per_week} sessions but has {len(task_events)}."})

        scheduled = _scheduled_minutes(task_events)
        expected = expected_minutes(task)
        if task.title not in unscheduled_titles and scheduled != expected:
            issues.append({"level": "error", "task": task.title, "message": f"Scheduled {scheduled} minutes but expected {expected} minutes."})

        if task.depends_on and task_events:
            dependency_events = events_by_source.get(task.depends_on, [])
            if not dependency_events:
                issues.append({"level": "error", "task": task.title, "message": f"Dependency '{task.depends_on}' has no scheduled events."})
            else:
                dep_end = max(event.day_index * 1440 + event.end_min for event in dependency_events)
                task_start = min(event.day_index * 1440 + event.start_min for event in task_events)
                if task_start < dep_end:
                    issues.append({"level": "error", "task": task.title, "message": f"Task starts before dependency '{task.depends_on}' is complete."})

    if anchors:
        for anchor in anchors:
            matched, score = _best_task_match(anchor, tasks)
            if not matched or score < 0.25:
                issues.append({"level": "error", "task": anchor.title, "message": "Parsed task anchor was lost in the final AI plan."})
                continue
            anchor_events = events_by_source.get(matched.title, [])
            if matched.title not in unscheduled_titles:
                expected = expected_minutes(anchor)
                scheduled = _scheduled_minutes(anchor_events)
                if scheduled != expected:
                    issues.append({"level": "error", "task": matched.title, "message": f"Original task anchor expected {expected} minutes from the user's text, but final plan scheduled {scheduled} minutes."})
            if anchor.task_type == "Recurring" and anchor_events and len(anchor_events) != int(anchor.sessions_per_week):
                issues.append({"level": "error", "task": matched.title, "message": f"Original recurring anchor expected {anchor.sessions_per_week} sessions, but final plan has {len(anchor_events)}."})

    return issues


def _call_ai(client: OpenAI, model: str, messages: List[Dict]) -> Dict:
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=messages,
    )
    return json.loads(response.choices[0].message.content)


def _payload_from_data(data: Dict) -> Tuple[List[Task], List[Event], List[UnscheduledTask], List[str]]:
    tasks = [task_from_dict(item) for item in data.get("tasks", [])]
    task_by_title = {task.title: task for task in tasks}
    events = [event_from_dict(item, task_by_title) for item in data.get("events", [])]
    unscheduled = [unscheduled_from_dict(item, task_by_title) for item in data.get("unscheduled", [])]
    warnings = list(data.get("warnings", []))
    events.sort(key=lambda event: (event.day_index, event.start_min, event.end_min, event.title))
    return tasks, events, unscheduled, warnings


def plan_week_with_ai(raw_text: str, parsed_tasks: List[Task], api_key: str, model: str, settings: Dict, repair_passes: int = 2):
    if not api_key:
        raise ValueError("Missing OpenAI API key.")
    client = OpenAI(api_key=api_key)
    wake_min = int(settings.get("wake_min", 360))
    sleep_min = int(settings.get("sleep_min", 1380))

    user_payload = {
        "raw_user_text": raw_text,
        "parsed_task_hints": [asdict(task) for task in parsed_tasks],
        "duration_anchors": [{"title": task.title, "expected_minutes": expected_minutes(task), "task_type": task.task_type, "sessions_per_week": task.sessions_per_week} for task in parsed_tasks],
        "settings": settings,
    }
    messages = [
        {"role": "system", "content": AI_PLANNER_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    data = _call_ai(client, model, messages)
    tasks, events, unscheduled, warnings = _payload_from_data(data)
    issues = validate_ai_plan(tasks, events, unscheduled, wake_min, sleep_min, parsed_tasks)

    for _ in range(repair_passes):
        if not issues:
            break
        repair_payload = {
            "raw_user_text": raw_text,
            "settings": settings,
            "parsed_task_hints": [asdict(task) for task in parsed_tasks],
            "duration_anchors": [{"title": task.title, "expected_minutes": expected_minutes(task), "task_type": task.task_type, "sessions_per_week": task.sessions_per_week} for task in parsed_tasks],
            "current_plan": {
                "tasks": [asdict(task) for task in tasks],
                "events": [asdict(event) for event in events],
                "unscheduled": [asdict(item) for item in unscheduled],
                "warnings": warnings,
            },
            "validation_errors": issues,
        }
        messages = [
            {"role": "system", "content": AI_PLANNER_PROMPT},
            {"role": "system", "content": REPAIR_PROMPT},
            {"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False)},
        ]
        data = _call_ai(client, model, messages)
        tasks, events, unscheduled, warnings = _payload_from_data(data)
        issues = validate_ai_plan(tasks, events, unscheduled, wake_min, sleep_min, parsed_tasks)

    return tasks, events, unscheduled, issues, warnings
