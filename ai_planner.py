import json
import re
from dataclasses import asdict
from typing import Dict, List, Tuple

from openai import OpenAI

from models import CATEGORIES, DAY_NAMES, DAY_TO_INDEX, Event, Task, UnscheduledTask
from parser_utils import hhmm_to_minutes, minutes_to_hhmm
from routine_utils import inject_routine_blocks, routine_anchor_payload

PRIORITIES = ["Critical", "High", "Medium", "Low", "Optional"]
TASK_TYPES = ["Fixed", "Flexible", "Recurring", "Multi-session"]
PREFERRED_TIMES = ["Morning", "Workday", "Afternoon", "Evening", "Weekend", "Any"]
ENERGIES = ["High", "Medium", "Low", "Physical", "Creative"]
LOCATIONS = ["Lab", "Home", "Gym", "Any"]

AI_PLANNER_PROMPT = """
You are the planning brain of a public weekly scheduling app.
Users may write with grammar mistakes, spelling mistakes, informal wording, transliteration, or mixed languages. Understand intent rather than relying on exact keywords.

Create a complete, realistic weekly calendar from the user's text.

You receive:
- raw_user_text
- parsed_task_hints and duration_anchors
- routine_anchors selected in the sidebar
- settings, including wake/sleep times, planning mode, and transition time

The application inserts routine_anchors deterministically after your response. Do NOT output tasks or events for those automatic routine anchors. Instead, keep their exact time windows free.

Return ONLY valid JSON:
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
- Preserve exact fixed events.
- Never overlap routine_anchors or other events unless overlap is explicitly allowed.
- Keep events inside wake/sleep time.
- Respect explicit total durations exactly.
- Do not silently alter parsed duration anchors or recurrence counts.
- Recurring duration_min is per session. Multi-session duration_min is the total weekly duration.
- Respect required days, time-of-day wording, deadlines, and dependencies.
- Keep follow-up work after its prerequisite is complete.
- Do not invent deadlines or dependencies.
- Use concise English titles even when the input is multilingual.

Human daily rhythm:
- Do not place study, writing, creative deep work, lab work, or administrative work immediately after waking.
- Respect the automatic morning-routine block. After it, use breakfast first when breakfast is enabled.
- A natural default sequence is: wake/routine -> breakfast -> demanding work or exercise -> lunch -> medium/administrative/errand tasks -> dinner -> social or low-energy tasks -> wind-down.
- Place cooking or meal preparation before the relevant meal when practical.
- Prefer high-focus work after the morning routine or breakfast, not at wake-up time.
- Avoid placing several demanding blocks back-to-back. Use the configured transition_min between long, high-energy, or location-changing tasks when possible.
- Avoid more than 180 minutes of uninterrupted demanding work.
- Do not fill every free minute. Preserve realistic breathing room.

Time-of-day interpretation:
- Morning: 06:00-12:00
- Afternoon: 12:00-18:00
- Evening: 17:00-22:00
- Workday: Monday-Friday, normally 09:00-17:00
- Weekend: Saturday-Sunday
"""

REPAIR_PROMPT = """
The previous plan failed deterministic validation. Repair the JSON schedule while preserving user intent, duration anchors, fixed events, routine anchors, and realistic daily rhythm. Return ONLY valid JSON with the same shape.
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
    return int(task.duration_min) * int(task.sessions_per_week) if task.task_type == "Recurring" else int(task.duration_min)


def time_pref_ok(task: Task, event: Event) -> bool:
    pref = task.preferred_time
    if pref == "Morning":
        return event.start_min < 12 * 60 and event.end_min <= 13 * 60
    if pref == "Afternoon":
        return 12 * 60 <= event.start_min < 18 * 60
    if pref == "Evening":
        return 17 * 60 <= event.start_min < 22 * 60
    if pref == "Workday":
        return event.day_index <= 4 and 8 * 60 <= event.start_min < 18 * 60
    if pref == "Weekend":
        return event.day_index in [5, 6]
    return True


def _events_by_source(events: List[Event]) -> Dict[str, List[Event]]:
    out = {}
    for event in events:
        out.setdefault(event.source_task, []).append(event)
    return out


def _scheduled_minutes(events: List[Event]) -> int:
    return sum(event.end_min - event.start_min for event in events)


def apply_duration_anchors(tasks: List[Task], events: List[Event], anchors: List[Task]):
    events = list(events)
    for anchor in anchors or []:
        matched, score = _best_task_match(anchor, tasks)
        if not matched or score < 0.25:
            continue
        matched.duration_min = anchor.duration_min
        matched.task_type = anchor.task_type
        matched.sessions_per_week = anchor.sessions_per_week
        if anchor.fixed_day:
            matched.fixed_day = anchor.fixed_day
        if anchor.fixed_start:
            matched.fixed_start = anchor.fixed_start
        if anchor.required_day:
            matched.required_day = anchor.required_day
        if anchor.preferred_time != "Any":
            matched.preferred_time = anchor.preferred_time
        if anchor.depends_on:
            matched.depends_on = anchor.depends_on

        task_events = [event for event in events if event.source_task == matched.title]
        surplus = _scheduled_minutes(task_events) - expected_minutes(anchor)
        if surplus <= 0 or matched.task_type == "Fixed":
            continue
        for event in sorted(task_events, key=lambda e: (e.day_index, e.start_min), reverse=True):
            if surplus <= 0:
                break
            duration = event.end_min - event.start_min
            if surplus >= duration:
                events.remove(event)
                surplus -= duration
            else:
                event.end_min -= surplus
                surplus = 0
    events.sort(key=lambda event: (event.day_index, event.start_min, event.end_min, event.title))
    return tasks, events


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

        if task.task_type != "Fixed" and not task.fixed_start and task.required_day and task.preferred_time in ["Morning", "Afternoon", "Evening"]:
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

    for anchor in anchors or []:
        matched, score = _best_task_match(anchor, tasks)
        if not matched or score < 0.25:
            issues.append({"level": "error", "task": anchor.title, "message": "Parsed task anchor was lost in the final AI plan."})
            continue
        anchor_events = events_by_source.get(matched.title, [])
        if matched.title not in unscheduled_titles:
            expected = expected_minutes(anchor)
            scheduled = _scheduled_minutes(anchor_events)
            if scheduled != expected:
                issues.append({"level": "error", "task": matched.title, "message": f"Original task anchor expected {expected} minutes, but final plan scheduled {scheduled} minutes."})
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


def _duration_anchors(parsed_tasks: List[Task]):
    return [
        {"title": task.title, "expected_minutes": expected_minutes(task), "task_type": task.task_type, "sessions_per_week": task.sessions_per_week}
        for task in parsed_tasks
    ]


def _prepare_plan(data: Dict, parsed_tasks: List[Task], settings: Dict):
    tasks, events, unscheduled, warnings = _payload_from_data(data)
    tasks, events = apply_duration_anchors(tasks, events, parsed_tasks)
    tasks, events = inject_routine_blocks(tasks, events, settings)
    return tasks, events, unscheduled, warnings


def plan_week_with_ai(raw_text: str, parsed_tasks: List[Task], api_key: str, model: str, settings: Dict, repair_passes: int = 2):
    if not api_key:
        raise ValueError("Missing OpenAI API key.")
    client = OpenAI(api_key=api_key)
    wake_min = int(settings.get("wake_min", 360))
    sleep_min = int(settings.get("sleep_min", 1380))
    routines = routine_anchor_payload(settings)

    user_payload = {
        "raw_user_text": raw_text,
        "parsed_task_hints": [asdict(task) for task in parsed_tasks],
        "duration_anchors": _duration_anchors(parsed_tasks),
        "routine_anchors": routines,
        "settings": settings,
    }
    messages = [
        {"role": "system", "content": AI_PLANNER_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    data = _call_ai(client, model, messages)
    tasks, events, unscheduled, warnings = _prepare_plan(data, parsed_tasks, settings)
    issues = validate_ai_plan(tasks, events, unscheduled, wake_min, sleep_min, parsed_tasks)

    for _ in range(repair_passes):
        if not issues:
            break
        repair_payload = {
            "raw_user_text": raw_text,
            "settings": settings,
            "parsed_task_hints": [asdict(task) for task in parsed_tasks],
            "duration_anchors": _duration_anchors(parsed_tasks),
            "routine_anchors": routines,
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
        tasks, events, unscheduled, warnings = _prepare_plan(data, parsed_tasks, settings)
        issues = validate_ai_plan(tasks, events, unscheduled, wake_min, sleep_min, parsed_tasks)

    return tasks, events, unscheduled, issues, warnings
