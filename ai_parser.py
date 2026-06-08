import json
from openai import OpenAI
from models import Task, CATEGORIES, DAY_NAMES

PRIORITIES = ["Critical", "High", "Medium", "Low", "Optional"]
TASK_TYPES = ["Fixed", "Flexible", "Recurring", "Multi-session"]
PREFERRED_TIMES = ["Morning", "Workday", "Afternoon", "Evening", "Weekend", "Any"]
ENERGIES = ["High", "Medium", "Low", "Physical", "Creative"]
LOCATIONS = ["Lab", "Home", "Gym", "Any"]

SYSTEM_PROMPT = """
You are an expert task parser for a weekly scheduling app.
Convert messy task lists into structured scheduling tasks.

Return ONLY valid JSON:
{"tasks":[{"title":"","duration_min":60,"priority":"Medium","task_type":"Flexible","sessions_per_week":1,"fixed_day":"","fixed_start":"","preferred_time":"Any","energy":"Medium","location":"Any","splittable":true,"min_block_min":30,"max_block_min":180,"can_overlap":false,"category":"Other","notes":""}],"warnings":[]}

Rules:
- Fixed appointments need fixed_day and fixed_start in HH:MM.
- Emails, booking, short messages, and sending papers are Admin, Low energy, 10-30 min blocks.
- Lab experiments are Lab, High energy, min_block_min at least 90, max_block_min 180.
- Paper preparation/writing is Writing, High energy, min_block_min at least 90.
- Gym is Health, Physical, recurring if frequency is given.
- Cooking/laundry/groceries/cleaning/cabinets are Home.
- Talking with wife/partner is Relationship and can_overlap=true if text says it can be done alongside low mental load tasks.
- Raspberry Pi and Udemy/personal development are Optional unless the user says otherwise.
- "around 10 hours" means duration_min=600.
- "1 hour per cabinet" and 4 cabinets means duration_min=60, sessions_per_week=4, task_type="Multi-session".
- Do not invent deadlines.
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
    import re
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
    )

def parse_tasks_with_ai(raw_text: str, api_key: str, model: str = "gpt-4.1-mini"):
    if not api_key:
        raise ValueError("Missing OpenAI API key.")
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": raw_text},
        ],
    )
    data = json.loads(response.choices[0].message.content)
    tasks = [_task_from_dict(item) for item in data.get("tasks", [])]
    warnings = data.get("warnings", [])
    return tasks, warnings
