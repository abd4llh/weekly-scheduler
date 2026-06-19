import json
import re

from openai import OpenAI

from models import CATEGORIES, COGNITIVE_LOADS, DAY_NAMES, PHYSICAL_LOADS, SESSION_DISTRIBUTIONS, Task

PRIORITIES = ["Critical", "High", "Medium", "Low", "Optional"]
TASK_TYPES = ["Fixed", "Flexible", "Recurring", "Multi-session"]
PREFERRED_TIMES = ["Morning", "Workday", "Afternoon", "Evening", "Weekend", "Any"]
ENERGIES = ["High", "Medium", "Low", "Physical", "Creative"]
NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
}
TIME_QUANTITY_NOUNS = {
    "minute", "minutes", "hour", "hours", "day", "days", "week", "weeks",
    "month", "months", "year", "years",
}
DAY_PATTERN = "|".join(day.lower() for day in DAY_NAMES)

SYSTEM_PROMPT = """
You extract structured tasks for a general-purpose weekly scheduler. Input may be paragraphs, bullets, grammar mistakes, mixed languages, or specialist terminology. Extract every actionable task and return only JSON.

Schema:
{"tasks":[{"title":"","duration_min":60,"priority":"Medium","task_type":"Flexible","sessions_per_week":1,"fixed_day":"","fixed_start":"","preferred_time":"Any","energy":"Medium","location":"Any","cognitive_load":"Medium","physical_load":"Low","session_distribution":"Any","recovery_min":0,"splittable":true,"min_block_min":30,"max_block_min":180,"can_overlap":false,"category":"Other","required_day":"","earliest_day":"","deadline_day":"","deadline_time":"","depends_on":"","phase":0,"notes":"","confidence":0.8,"duration_is_estimated":true,"assumptions":"","needs_clarification":false,"clarification_question":""}],"warnings":[]}

Allowed values:
- priority: Critical, High, Medium, Low, Optional
- task_type: Fixed, Flexible, Recurring, Multi-session
- preferred_time: Morning, Workday, Afternoon, Evening, Weekend, Any
- energy: High, Medium, Low, Physical, Creative
- cognitive_load and physical_load: Low, Medium, High
- session_distribution: Any, Prefer different days, Require different days, Prefer same day
- category: Work, Lab, Writing, Admin, Health, Home, Relationship, Social, Learning, Optional, Focus, Other

Rules:
- Fixed requires an exact day and clock time. A daypart is not an exact time.
- Recurring duration is per occurrence. Multi-session duration is the total across all sessions.
- Every explicit count of repeated occurrences, trips, calls, appointments, sessions, or deliverable units must be preserved in sessions_per_week.
- Example: "two trips" means sessions_per_week=2. If no duration is given, estimate duration per trip and use Recurring.
- Example: "prepare three sections in six hours total" means Multi-session, sessions_per_week=3, duration_min=360 total.
- A quantity does not automatically require different days. Use the session_distribution field independently.
- Use required_day for a day without an exact time, earliest_day for a true not-before rule, and deadline fields only for real deadlines.
- "Before Friday" means completion strictly before Friday begins: deadline_day="Friday", deadline_time="00:00". "By Friday" may include Friday unless a time is stated.
- Morning is about 07:00-12:00, Workday 09:00-17:00, Afternoon 13:00-18:00, Evening 18:00-22:00.
- depends_on must exactly match another extracted title and should be used when a later action cannot start until the earlier action is complete.
- location is a concise stable place label such as Home, Office, Campus, Client site, Store, Remote, or Any. Use the same label for the same place. Do not invent a location when unknown.
- cognitive_load describes concentration and decision effort, independent of profession. physical_load describes physical effort.
- Require different days only when spacing is explicit or intrinsic. Prefer different days when spacing helps learning, recovery, or progress. Prefer same day when batching saves setup, travel, or context switching. Otherwise use Any.
- recovery_min is a task-specific cooldown, cleanup, or reset period after the task. Travel is handled from location labels. Use 0 when none is implied.
- Do not make profession-specific assumptions or infer behavior from isolated keywords.
- Keep can_overlap false unless simultaneous execution is explicitly allowed and realistic.
- Estimate missing durations conservatively and mark duration_is_estimated true.
"""

REPAIR_PROMPT = """
Repair the JSON from validator feedback without adding profession-specific assumptions. Preserve every explicit occurrence or deliverable count in sessions_per_week. Use Recurring with per-occurrence duration for repeated activities and Multi-session with total duration for a divisible workload. Preserve strict-before deadlines as 00:00 at the beginning of the named day. Return only the same JSON shape.
"""


def _pick(value, allowed, default):
    return value if value in allowed else default


def _clean_day(value):
    value = str(value or "").strip()
    return value if value in DAY_NAMES else ""


def _clean_hhmm(value):
    value = str(value or "").strip()
    return value if re.match(r"^\d{2}:\d{2}$", value) else ""


def _clean_location(value):
    value = re.sub(r"\s+", " ", str(value or "Any").strip())
    return (value or "Any")[:60]


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


def _task_text(task):
    return " ".join(str(value or "") for value in [task.title, task.notes, task.assumptions]).lower()


def _explicit_non_time_quantity(text):
    number_pattern = r"\d+|" + "|".join(NUMBER_WORDS)
    pattern = re.compile(
        rf"\b(?P<count>{number_pattern})\s+(?:(?:[a-z][a-z-]*)\s+){{0,3}}(?P<noun>[a-z][a-z-]*)\b",
        re.IGNORECASE,
    )
    for match in pattern.finditer(str(text or "")):
        raw = match.group("count").lower()
        count = int(raw) if raw.isdigit() else NUMBER_WORDS.get(raw)
        noun = match.group("noun").lower()
        if count is None or count <= 1 or count > 14:
            continue
        if noun in TIME_QUANTITY_NOUNS:
            continue
        return count
    return None


def _apply_strict_before_deadline(task):
    if not task.deadline_day:
        return task
    text = _task_text(task)
    day = task.deadline_day.lower()
    match = re.search(
        rf"\bbefore\s+({DAY_PATTERN})\b(?!\s+(?:morning|afternoon|evening|night|noon|midnight|\d{{1,2}}(?::\d{{2}})?))",
        text,
    )
    if match and match.group(1) == day:
        task.deadline_time = "00:00"
    return task


def _postprocess_task(task):
    if task.task_type == "Fixed" and (not task.fixed_day or not task.fixed_start):
        if task.fixed_day and not task.fixed_start:
            task.required_day = task.fixed_day
        task.fixed_day = ""
        task.fixed_start = ""
        task.task_type = "Flexible"
    if task.sessions_per_week <= 1:
        task.session_distribution = "Any"
    task.min_block_min = min(max(1, int(task.min_block_min)), int(task.duration_min))
    task.max_block_min = min(max(int(task.max_block_min), int(task.min_block_min)), int(task.duration_min))
    task.recovery_min = max(0, min(180, int(task.recovery_min)))
    task.location = _clean_location(task.location)
    return _apply_strict_before_deadline(task)


def _task_from_dict(item):
    duration = _to_int(item.get("duration_min"), 60, 1, 1440)
    min_block = _to_int(item.get("min_block_min"), min(30, duration), 1, duration)
    max_block = _to_int(item.get("max_block_min"), max(min_block, min(duration, 180)), min_block, duration)
    return Task(
        title=str(item.get("title") or "Untitled task")[:120],
        duration_min=duration,
        priority=_pick(item.get("priority"), PRIORITIES, "Medium"),
        task_type=_pick(item.get("task_type"), TASK_TYPES, "Flexible"),
        sessions_per_week=_to_int(item.get("sessions_per_week"), 1, 1, 14),
        fixed_day=_clean_day(item.get("fixed_day")),
        fixed_start=_clean_hhmm(item.get("fixed_start")),
        preferred_time=_pick(item.get("preferred_time"), PREFERRED_TIMES, "Any"),
        energy=_pick(item.get("energy"), ENERGIES, "Medium"),
        location=_clean_location(item.get("location")),
        cognitive_load=_pick(item.get("cognitive_load"), COGNITIVE_LOADS, "Medium"),
        physical_load=_pick(item.get("physical_load"), PHYSICAL_LOADS, "Low"),
        session_distribution=_pick(item.get("session_distribution"), SESSION_DISTRIBUTIONS, "Any"),
        recovery_min=_to_int(item.get("recovery_min"), 0, 0, 180),
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


def _postprocess_tasks(tasks):
    return [_postprocess_task(task) for task in tasks]


def _tasks_to_payload(tasks, warnings=None):
    return {"tasks": [task.__dict__ for task in tasks], "warnings": warnings or []}


def validate_ai_tasks(tasks):
    issues = []
    titles = {task.title for task in tasks}
    for index, task in enumerate(tasks):
        label = f"Task {index + 1} ({task.title})"
        if task.task_type == "Fixed" and (not task.fixed_day or not task.fixed_start):
            issues.append(f"{label}: Fixed task is missing an exact day or time.")
        if task.sessions_per_week > 1 and task.task_type not in {"Recurring", "Multi-session"}:
            issues.append(f"{label}: multiple sessions require Recurring or Multi-session.")
        if task.task_type == "Recurring" and task.sessions_per_week <= 1:
            issues.append(f"{label}: Recurring task requires more than one occurrence.")
        explicit_count = _explicit_non_time_quantity(_task_text(task))
        if explicit_count and task.sessions_per_week != explicit_count:
            issues.append(
                f"{label}: the text contains an explicit count of {explicit_count}, but sessions_per_week is {task.sessions_per_week}. Preserve the count and choose Recurring or Multi-session with the correct duration semantics."
            )
        if task.min_block_min > task.max_block_min:
            issues.append(f"{label}: minimum block exceeds maximum block.")
        if task.depends_on and task.depends_on not in titles:
            issues.append(f"{label}: depends_on does not exactly match another task title.")
        if task.session_distribution != "Any" and task.sessions_per_week <= 1:
            issues.append(f"{label}: distribution is set for a single-session task.")
        if task.can_overlap and not task.assumptions:
            issues.append(f"{label}: overlap is enabled without an explanation.")
        if task.confidence < 0.45 and not task.needs_clarification:
            issues.append(f"{label}: low confidence should request clarification.")
    return issues


def _call_ai(client, model, messages):
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=messages,
    )
    return json.loads(response.choices[0].message.content)


def parse_tasks_with_ai(raw_text, api_key, model="gpt-4.1-mini", repair_passes=2, user_defaults=None):
    if not api_key:
        raise ValueError("Missing OpenAI API key.")
    client = OpenAI(api_key=api_key)
    data = _call_ai(client, model, [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Raw task text:\n{raw_text}"},
    ])
    tasks = _postprocess_tasks([_task_from_dict(item) for item in data.get("tasks", [])])
    warnings = list(data.get("warnings", []))
    issues = validate_ai_tasks(tasks)
    for _ in range(repair_passes):
        if not issues:
            break
        repair_input = {"original_user_text": raw_text, "current_json": _tasks_to_payload(tasks, warnings), "validation_issues": issues}
        data = _call_ai(client, model, [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": REPAIR_PROMPT},
            {"role": "user", "content": json.dumps(repair_input, ensure_ascii=False)},
        ])
        tasks = _postprocess_tasks([_task_from_dict(item) for item in data.get("tasks", [])])
        warnings = list(data.get("warnings", []))
        issues = validate_ai_tasks(tasks)
    if issues:
        warnings.extend(issues)
    return tasks, warnings
