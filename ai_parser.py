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
You are an AI task-understanding agent for a public weekly scheduling app.

Input may be bullets, notes, paragraphs, or mixed text. Extract ALL actionable tasks and convert them into structured scheduling objects. Do not rely only on keywords. Interpret the meaning of each task in context.

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

Important duration semantics:
- For Multi-session tasks, duration_min MUST mean the TOTAL duration needed for the week, not duration per session.
- sessions_per_week for a Multi-session task is only a suggested number of pieces and must not multiply the total duration.
- If the user says "10 hours of work this week", use duration_min=600, not 600 per session.
- If the user says "5 hours of writing this week", use duration_min=300, not 300 per session.

General principles:
- If exact day and time are provided, use task_type="Fixed".
- If a task repeats, use task_type="Recurring" and sessions_per_week > 1.
- If total work is large and can be split, use task_type="Multi-session".
- If duration is missing, estimate conservatively and set duration_is_estimated=true.
- If duration is explicit, set duration_is_estimated=false.
- For exercise with no explicit duration, estimate 60-120 minutes, not 180+ minutes.
- For daily cooking or meal preparation with no explicit duration, estimate 45-90 minutes and prefer Evening unless the user says otherwise.
- For relationship or social calls with no explicit duration, estimate 30-90 minutes and prefer Evening unless the user says otherwise.
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

def _task_from_dict(item):
    duration = _to_int(item.get("duration_min"), 60, 1, 1440)
    min_block = _to_int(item.get("min_block_min"), min(30, duration), 1, duration)
    max_block = _to_int(item.get("max_block_min"), max(min_block, min(duration, 180)), min_block, 1440)

    fixed_day = item.get("fixed_day") or ""
    if fixed_day not in DAY_NAMES:
        fixed_day = ""

    fixed_start = str(item.get("fixed_start") or "").strip()
    if fixed_start and not re.match(r"^\d{2}:\d{2}$", fixed_start):
        fixed_start = ""

    return Task(
        title=str(item.get("title") or "Untitled task")[:120],
        duration_min=duration,
        priority=_pick(item.get("priority"), PRIORITIES, "Medium"),
        task_type=_pick(item.get("task_type"), TASK_TYPES, "Flexible"),
        sessions_per_week=_to_int(item.get("sessions_per_week"), 1, 1, 7),
        fixed_day=fixed_day,
        fixed_start=fixed_start,
        preferred_time=_pick(item.get("preferred_time"), PREFERRED_TIMES, "Any"),
        energy=_pick(item.get("energy"), ENERGIES, "Medium"),
        location=_pick(item.get("location"), LOCATIONS, "Any"),
        splittable=_to_bool(item.get("splittable"), duration > 90),
        min_block_min=min_block,
        max_block_min=max_block,
        can_overlap=_to_bool(item.get("can_overlap"), False),
        notes=str(item.get("notes") or item.get("title") or ""),
        category=_pick(item.get("category"), CATEGORIES, "Other"),
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
    tasks = [_task_from_dict(item) for item in data.get("tasks", [])]
    warnings = list(data.get("warnings", []))
    issues = validate_ai_tasks(tasks)

    for _ in range(repair_passes):
        if not issues:
            break
        repair_input = {"original_user_text": raw_text, "current_json": _tasks_to_payload(tasks, warnings), "validation_issues": issues}
        data = _call_ai(client, model, [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "system", "content": REPAIR_PROMPT}, {"role": "user", "content": json.dumps(repair_input, ensure_ascii=False)}])
        tasks = [_task_from_dict(item) for item in data.get("tasks", [])]
        warnings = list(data.get("warnings", []))
        issues = validate_ai_tasks(tasks)

    if issues:
        warnings.extend(issues)
    return tasks, warnings
