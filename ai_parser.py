import json
import re
from openai import OpenAI
from models import Task, CATEGORIES, DAY_NAMES

PRIORITIES = ["Critical", "High", "Medium", "Low", "Optional"]
TASK_TYPES = ["Fixed", "Flexible", "Recurring", "Multi-session"]
PREFERRED_TIMES = ["Morning", "Workday", "Afternoon", "Evening", "Weekend", "Any"]
ENERGIES = ["High", "Medium", "Low", "Physical", "Creative"]
LOCATIONS = ["Lab", "Home", "Gym", "Any"]

SYSTEM_PROMPT = """
You are an AI task-understanding and planning-constraint extraction agent for a public weekly scheduling app.

Input may be bullets, notes, paragraphs, or mixed text. Extract ALL actionable tasks and convert them into structured scheduling objects. Do not rely only on keywords. Interpret the meaning of each task in context.

Every distinct action or routine mentioned by the user should become its own task. Do not omit daily routines, fixed appointments, social events, household tasks, or repeated habits.

Return ONLY valid JSON:
{
  "tasks": [
    {
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
      "notes": "original text or paraphrase",
      "confidence": 0.85,
      "duration_is_estimated": true,
      "assumptions": "brief assumptions made",
      "needs_clarification": false,
      "clarification_question": ""
    }
  ],
  "warnings": []
}

Scheduling ontology:
- Fixed: exact day and exact time are provided.
- Recurring: repeats daily, weekly, several times per week, every morning/evening, etc.
- Multi-session: total work should be split into multiple useful blocks.
- Flexible: one task that can be scheduled once wherever it fits.

Planning constraints:
- required_day: use when the user specifies a day but not an exact time, e.g. "Sunday afternoon", "Tuesday morning", "on Friday".
- earliest_day: use when a task should not begin before a day, e.g. "after Wednesday", "once supplies arrive Tuesday".
- deadline_day/deadline_time: use for constraints like "before Friday evening", "by Thursday", "before 18:00". Use deadline_time as HH:MM when possible.
- depends_on: use when a task logically depends on another task being done first, e.g. varnishing/packing after painting, editing after drafting, delivery after preparation. Put the clean title of the prerequisite task.
- phase: rough order group. Use 1 for main/prep work, 2 for follow-up/finishing, 3 for delivery/export/review. Keep 0 if no order is implied.

Important duration semantics:
- For Recurring tasks, duration_min MUST mean duration per occurrence/session.
- For Multi-session tasks, duration_min MUST mean total duration needed for the week, not duration per session.
- sessions_per_week is a count, not a duration multiplier.
- If the user says "10 hours of work this week", use duration_min=600 and task_type="Multi-session".
- If the user says "5 hours of writing this week", use duration_min=300 and task_type="Multi-session".

General principles:
- If exact day and time are provided, use task_type="Fixed".
- If a day is provided without an exact time, do NOT invent a fixed_start. Use required_day and preferred_time instead.
- If a deadline is provided without an exact event time, use deadline_day/deadline_time instead of task_type="Fixed".
- If a task repeats, use task_type="Recurring" and sessions_per_week > 1.
- If wording says "twice this week", use task_type="Recurring" and sessions_per_week=2.
- If wording says "three times this week" or "three times a week", use task_type="Recurring" and sessions_per_week=3.
- If total work is large and can be split, use task_type="Multi-session".
- If duration is missing, estimate conservatively and set duration_is_estimated=true.
- If duration is explicit, set duration_is_estimated=false.
- For exercise with no explicit duration, estimate 60-120 minutes, not 180+ minutes.
- For daily cooking or meal preparation with no explicit duration, estimate 45-90 minutes and set preferred_time="Evening" unless the user says otherwise.
- For relationship or social calls with no explicit duration, estimate 30-90 minutes and set preferred_time="Evening" unless the user says otherwise.
- For relationship/social calls, can_overlap should be false unless the user explicitly says the call can happen alongside another activity.
- Fixed social events should be treated as non-overlapping fixed events.
- Choose a realistic minimum block size based on the task context.
- Tasks requiring setup/context should not have tiny blocks.
- Admin micro-tasks can have 10-30 minute blocks.
- Only set can_overlap=true when the text or task nature clearly supports low-attention overlap.
- If you are unsure, set confidence lower and write an assumption.
- If missing information prevents good scheduling, set needs_clarification=true and ask a concise clarification question.
"""

REPAIR_PROMPT = """
You previously returned structured tasks, but the validator found consistency problems. Repair the JSON while preserving the user's intent. Do not add new tasks unless one was clearly missed. Return ONLY valid JSON with the same shape: {"tasks": [...], "warnings": [...]}.
"""

def _pick(value, allowed, default):
    return value if value in allowed else default

def _clean_day(value):
    value = str(value or "").strip()
    return value if value in DAY_NAMES else ""

def _clean_hhmm(value):
    value = str(value or "").strip()
    return value if re.match(r"^\d{2}:\d{2}$", value) else ""

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

def _has_explicit_time(text):
    return bool(re.search(r"\b\d{1,2}:\d{2}\b", text))

def _infer_repeat_count(text):
    repeat_patterns = [
        (7, ["every day", "daily", "each day"]),
        (2, ["twice", "two times", "2 times"]),
        (3, ["three times", "3 times"]),
        (4, ["four times", "4 times"]),
        (5, ["five times", "5 times"]),
    ]
    for count, patterns in repeat_patterns:
        if any(pattern in text for pattern in patterns):
            return count
    return None

def _postprocess_task(task):
    text = f"{task.title} {task.notes} {task.assumptions}".lower()

    repeat_count = _infer_repeat_count(text)
    if repeat_count and task.task_type in ["Flexible", "Multi-session"]:
        task.task_type = "Recurring"
        task.sessions_per_week = repeat_count
        task.splittable = False
    elif repeat_count and task.task_type == "Recurring":
        task.sessions_per_week = repeat_count

    if task.task_type == "Recurring" and task.duration_is_estimated:
        if task.category == "Health" or task.energy == "Physical":
            if task.duration_min > 120:
                task.duration_min = 90
            elif task.duration_min < 45:
                task.duration_min = 60
        elif task.category == "Home":
            if task.duration_min > 120:
                task.duration_min = 75
            elif task.duration_min < 30:
                task.duration_min = 45
        elif task.category in ["Relationship", "Social"]:
            if task.duration_min > 120:
                task.duration_min = 60
            elif task.duration_min < 20:
                task.duration_min = 30

    if task.task_type == "Recurring" and task.category in ["Home", "Relationship", "Social"] and task.preferred_time == "Any":
        task.preferred_time = "Evening"

    if task.category in ["Relationship", "Social"] and task.can_overlap:
        overlap_words = ["overlap", "alongside", "at the same time", "while", "simultaneously", "passive", "low attention"]
        if not any(word in text for word in overlap_words):
            task.can_overlap = False

    soft_day_words = ["morning", "afternoon", "evening", "before", "by", "sometime"]
    if task.task_type == "Fixed" and task.fixed_day and not task.fixed_start:
        task.required_day = task.fixed_day
        task.fixed_day = ""
        task.task_type = "Flexible"
    if task.task_type == "Fixed" and task.fixed_day and task.fixed_start and any(word in text for word in soft_day_words):
        if not _has_explicit_time(text):
            if "before" in text or " by " in f" {text} ":
                task.deadline_day = task.fixed_day
                if not task.deadline_time:
                    task.deadline_time = "18:00" if "evening" in text else "23:00"
            else:
                task.required_day = task.fixed_day
            task.fixed_day = ""
            task.fixed_start = ""
            task.task_type = "Flexible"

    task.min_block_min = min(int(task.min_block_min), int(task.duration_min))
    task.max_block_min = min(max(int(task.max_block_min), int(task.min_block_min)), int(task.duration_min))
    return task

def _infer_dependencies(tasks):
    if not tasks:
        return tasks
    candidates = []
    for task in tasks:
        text = f"{task.title} {task.notes}".lower()
        if any(word in text for word in ["main", "draft", "prepare", "paint", "painting", "write", "build", "create"]):
            candidates.append(task)

    for task in tasks:
        if task.depends_on:
            continue
        text = f"{task.title} {task.notes}".lower()
        is_followup = any(word in text for word in ["varnish", "photograph", "packing", "pack", "ship", "deliver", "submit", "finalize", "review", "edit", "proofread"])
        if not is_followup:
            continue
        best = None
        for candidate in candidates:
            if candidate.title == task.title:
                continue
            candidate_text = f"{candidate.title} {candidate.notes}".lower()
            shared_domain = any(word in text and word in candidate_text for word in ["painting", "paper", "report", "portrait", "commission", "artwork", "draft"])
            if shared_domain or ("painting" in text and "paint" in candidate_text):
                best = candidate
                break
        if best:
            task.depends_on = best.title
            if task.phase == 0:
                task.phase = max(getattr(best, "phase", 1) + 1, 2)
    return tasks

def _postprocess_tasks(tasks):
    tasks = [_postprocess_task(task) for task in tasks]
    tasks = _infer_dependencies(tasks)
    return tasks

def _task_from_dict(item):
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

def _tasks_to_payload(tasks, warnings=None):
    return {"tasks": [t.__dict__ for t in tasks], "warnings": warnings or []}

def validate_ai_tasks(tasks):
    issues = []
    titles = {t.title for t in tasks}
    for i, t in enumerate(tasks):
        label = f"Task {i + 1} ({t.title})"
        if t.fixed_day and t.fixed_start and t.task_type != "Fixed":
            issues.append(f"{label}: has fixed_day and fixed_start but task_type is {t.task_type}; usually this should be Fixed.")
        if t.task_type == "Fixed" and (not t.fixed_day or not t.fixed_start):
            issues.append(f"{label}: task_type is Fixed but fixed_day or fixed_start is missing.")
        if t.sessions_per_week > 1 and t.task_type == "Flexible":
            issues.append(f"{label}: sessions_per_week > 1 but task_type is Flexible; usually Recurring or Multi-session is more consistent.")
        if t.task_type == "Recurring" and t.sessions_per_week <= 1:
            issues.append(f"{label}: task_type is Recurring but sessions_per_week is not greater than 1.")
        if t.min_block_min > t.max_block_min:
            issues.append(f"{label}: min_block_min is greater than max_block_min.")
        if t.max_block_min > t.duration_min and t.task_type != "Recurring":
            issues.append(f"{label}: max_block_min is greater than duration_min for a non-recurring task.")
        if t.task_type == "Recurring" and t.duration_is_estimated and t.duration_min > 180:
            issues.append(f"{label}: estimated recurring duration is unusually long; use a per-session duration, not weekly total.")
        if t.depends_on and t.depends_on not in titles:
            issues.append(f"{label}: depends_on references '{t.depends_on}', which does not exactly match another task title.")
        if t.can_overlap and not t.assumptions:
            issues.append(f"{label}: can_overlap is true but no assumption/explanation is provided.")
        if t.confidence < 0.45 and not t.needs_clarification:
            issues.append(f"{label}: confidence is low but needs_clarification is false.")
    return issues

def _call_ai(client, model, messages):
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=messages,
    )
    return json.loads(response.choices[0].message.content)

def parse_tasks_with_ai(raw_text: str, api_key: str, model: str = "gpt-4.1-mini", repair_passes: int = 1, user_defaults=None):
    if not api_key:
        raise ValueError("Missing OpenAI API key.")

    user_message = f"Raw task text:\n{raw_text}"

    client = OpenAI(api_key=api_key)
    data = _call_ai(client, model, [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_message}])
    tasks = _postprocess_tasks([_task_from_dict(item) for item in data.get("tasks", [])])
    warnings = list(data.get("warnings", []))
    issues = validate_ai_tasks(tasks)

    for _ in range(repair_passes):
        if not issues:
            break
        repair_input = {"original_user_text": raw_text, "current_json": _tasks_to_payload(tasks, warnings), "validation_issues": issues}
        data = _call_ai(client, model, [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "system", "content": REPAIR_PROMPT}, {"role": "user", "content": json.dumps(repair_input, ensure_ascii=False)}])
        tasks = _postprocess_tasks([_task_from_dict(item) for item in data.get("tasks", [])])
        warnings = list(data.get("warnings", []))
        issues = validate_ai_tasks(tasks)

    if issues:
        warnings.extend(issues)
    return tasks, warnings
