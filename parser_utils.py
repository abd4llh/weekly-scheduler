import json, re
from dataclasses import asdict
from datetime import datetime
from typing import List, Optional
from models import DAY_NAMES, DAY_SHORT, DAY_TO_INDEX, Task

DEFAULT_TASKS = """• Prepare paper with Federico (didn’t even start)
• Finish extra experiments for the paper with Federico (around 10 hours)
• Prepare paper with Giorgio (didn’t even start)
• Finish extra experiments for the paper with Giorgio (around 10 hours)
• Send the modified paper to Shirong to check (send an email, about 20 minutes)
• Organize 4 different cabinets in the house (1 hour per cabinet)
• Finish inkjet printing experiment (about 10 hours)
• Gym (2 hours, 3 times a week, morning preferred)
• Study German (30 minutes every day)
• Clean my house (every week, arrange it throughout the week with different tasks every day)
• Laundry (on the weekend every week)
• Groceries (on Saturday usually)
• Prepare grocery list (30 minutes on Saturday morning)
• Raspberry Pi personal project
• Personal development courses on Udemy
• Cooking (2 hours every day)
• Book lab devices (10 minutes usually on Thursday morning when I arrive at the office)
• Talking with Israa, my wife (can be done alongside other tasks that don’t require high mental usage)
"""

def minutes_to_hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"

def hhmm_to_minutes(s: str) -> Optional[int]:
    if not isinstance(s, str) or not s.strip(): return None
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", s)
    if not m: return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if 0 <= h <= 23 and 0 <= mi <= 59 else None

def parse_day(text: str) -> str:
    t = text.lower()
    for d in DAY_NAMES + DAY_SHORT:
        if re.search(rf"\b{re.escape(d.lower())}\b", t):
            return DAY_NAMES[DAY_TO_INDEX[d.lower()]]
    return ""

def parse_time(text: str) -> str:
    t = text.lower()
    m = re.search(r"\b(?:at\s*)?([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if m: return f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.search(r"\b(?:at\s*)?(1[0-2]|0?[1-9])\s*(am|pm)\b", t)
    if not m: return ""
    h = int(m.group(1)); ap = m.group(2)
    if ap == "pm" and h != 12: h += 12
    if ap == "am" and h == 12: h = 0
    return f"{h:02d}:00"

def parse_duration(text: str) -> int:
    t = text.lower()
    rng = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\s*(?:-|–|to|until)\s*([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if rng:
        a = int(rng.group(1))*60 + int(rng.group(2)); b = int(rng.group(3))*60 + int(rng.group(4))
        if b > a: return b - a
    h = re.search(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h)\b", t)
    if h: return int(round(float(h.group(1))*60))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(minutes?|mins?|min|m)\b", t)
    if m: return int(round(float(m.group(1))))
    if "send" in t and "email" in t: return 20
    if "grocery list" in t: return 30
    if "book lab" in t: return 10
    if "laundry" in t: return 60
    if "groceries" in t: return 120
    if "gym" in t: return 120
    if "german" in t: return 30
    if "cooking" in t: return 120
    if "cabinet" in t: return 60
    return 60

def parse_sessions(text: str) -> int:
    t = text.lower()
    if "every day" in t or "daily" in t: return 7
    m = re.search(r"(\d+)\s*times?\s*(?:a|per)?\s*week", t)
    return int(m.group(1)) if m else (3 if "3 times" in t else 1)

def clean_title(line: str) -> str:
    s = re.sub(r"^\s*[-•*]\s*", "", line.strip())
    s = re.sub(r"\([^)]*\)", "", s)
    day = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)"
    t24 = r"(?:[01]?\d|2[0-3]):[0-5]\d"; t12 = r"(?:1[0-2]|0?[1-9])\s*(?:am|pm)"
    for pat in [rf"\b(?:on\s+|at\s+)?{day}\s+(?:at\s+)?(?:{t24}|{t12})\b", rf"\b(?:on\s+|at\s+)?{day}\b", rf"\b(?:from\s+)?{t24}\s*(?:-|–|to|until)\s*{t24}\b", rf"\b(?:at\s+)?(?:{t24}|{t12})\b"]:
        s = re.sub(pat, "", s, flags=re.I)
    s = re.sub(r"\b(?:at|on|from|to|until)\s*$", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" ,-")[:120] or "Untitled task"

def infer_priority(t: str) -> str:
    x = t.lower()
    if any(k in x for k in ["deadline", "urgent", "critical", "must"]): return "Critical"
    if any(k in x for k in ["experiment", "paper", "shirong", "lab device"]): return "High"
    if any(k in x for k in ["gym", "german", "wife", "israa", "cooking", "groceries", "doctor", "meeting"]): return "Medium"
    if any(k in x for k in ["raspberry", "udemy", "personal development"]): return "Optional"
    return "Medium"

def infer_energy(t: str) -> str:
    x = t.lower()
    if any(k in x for k in ["paper", "write", "prepare", "analysis", "data", "experiment", "lab"]): return "High"
    if "gym" in x: return "Physical"
    if any(k in x for k in ["clean", "laundry", "groceries", "cabinet", "cooking", "doctor", "meeting"]): return "Low"
    if any(k in x for k in ["raspberry", "udemy"]): return "Creative"
    return "Medium"

def infer_location(t: str) -> str:
    x = t.lower()
    if any(k in x for k in ["experiment", "lab", "device", "inkjet"]): return "Lab"
    if "gym" in x: return "Gym"
    if any(k in x for k in ["cabinet", "house", "room", "laundry", "cooking"]): return "Home"
    return "Any"

def infer_pref(t: str) -> str:
    x = t.lower()
    if "morning" in x: return "Morning"
    if "afternoon" in x: return "Afternoon"
    if "evening" in x or "night" in x: return "Evening"
    if "saturday" in x or "sunday" in x or "weekend" in x: return "Weekend"
    if any(k in x for k in ["experiment", "lab", "paper", "shirong"]): return "Workday"
    if any(k in x for k in ["wife", "israa", "cooking"]): return "Evening"
    return "Any"

def parse_tasks(raw: str) -> List[Task]:
    tasks = []
    for line in raw.splitlines():
        if not line.strip(): continue
        low = line.lower(); dur = parse_duration(line); sess = parse_sessions(line)
        day = parse_day(line); start = parse_time(line)
        typ = "Fixed" if day and start else ("Recurring" if any(k in low for k in ["every day", "daily", "times a week", "per week", "weekly"]) else ("Multi-session" if dur >= 180 else "Flexible"))
        pref = infer_pref(line); loc = infer_location(line); energy = infer_energy(line); pr = infer_priority(line)
        overlap = any(k in low for k in ["wife", "israa", "alongside", "overlap"])
        if "book lab device" in low or "book lab devices" in low: day, start, typ, dur, loc = "Thursday", start or "09:30", "Fixed", 10, "Lab"
        if "grocery list" in low: day, start, typ, dur, loc = "Saturday", start or "09:30", "Fixed", 30, "Home"
        if ("groceries" in low or "grocery shopping" in low) and "grocery list" not in low: day, start, typ, dur = "Saturday", start or "10:00", "Fixed", 120
        if "laundry" in low: day, start, typ, dur, loc = "Sunday", start or "09:45", "Fixed", 60, "Home"
        if "talking with" in low or "israa" in low or "wife" in low: typ, sess, pref, dur, overlap = "Recurring", 7, "Evening", max(dur, 150), True
        if "cooking" in low: typ, sess, pref, dur, overlap, loc = "Recurring", 7, "Evening", 120, True, "Home"
        if "german" in low: typ, sess, pref, dur = "Recurring", 7, "Morning", 30
        if "gym" in low: typ, sess, pref, dur, loc, energy = "Recurring", 3, "Morning", 120, "Gym", "Physical"
        if "cabinet" in low: typ, sess, dur, loc, energy, pref = "Multi-session", 4, 60, "Home", "Low", "Weekend"
        if "clean my house" in low or "clean house" in low: typ, sess, dur, loc, energy, pref = "Recurring", 5, 20, "Home", "Low", "Evening"
        if "raspberry" in low or "udemy" in low: pr = "Optional"
        mn = min(30, dur); mx = 180 if dur >= 180 else max(mn, dur)
        tasks.append(Task(clean_title(line), dur, pr, typ, sess, day, start, pref, energy, loc, dur > 90 or sess > 1, mn, mx, overlap, line))
    return tasks

def validate_tasks(tasks: List[Task], wake_min: int, sleep_min: int):
    issues, fixed = [], []
    if wake_min >= sleep_min: issues.append({"level":"error","task":"Settings","message":"Wake time must be earlier than sleep target."})
    for t in tasks:
        if not t.title.strip(): issues.append({"level":"error","task":"Untitled","message":"Task has no title."})
        if int(t.duration_min) <= 0: issues.append({"level":"error","task":t.title,"message":"Duration must be greater than zero."})
        if t.task_type == "Fixed":
            day = DAY_TO_INDEX.get(str(t.fixed_day).lower()); start = hhmm_to_minutes(str(t.fixed_start))
            if day is None: issues.append({"level":"error","task":t.title,"message":"Fixed task needs a valid day."})
            if start is None: issues.append({"level":"error","task":t.title,"message":"Fixed task needs a valid time, e.g. 14:00."})
            elif day is not None:
                end = start + int(t.duration_min)
                if start < wake_min or end > sleep_min: issues.append({"level":"warning","task":t.title,"message":f"Fixed event {minutes_to_hhmm(start)}–{minutes_to_hhmm(end)} is outside wake/sleep window."})
                if not t.can_overlap: fixed.append((day, start, end, t.title))
        if t.task_type in ["Flexible", "Multi-session"] and int(t.max_block_min) < int(t.min_block_min):
            issues.append({"level":"warning","task":t.title,"message":"max_block_min is smaller than min_block_min."})
    for i, a in enumerate(fixed):
        for b in fixed[i+1:]:
            if a[0] == b[0] and max(a[1], b[1]) < min(a[2], b[2]):
                issues.append({"level":"error","task":f"{a[3]} / {b[3]}","message":f"Fixed-event conflict on {DAY_NAMES[a[0]]}: {minutes_to_hhmm(a[1])}–{minutes_to_hhmm(a[2])} overlaps {minutes_to_hhmm(b[1])}–{minutes_to_hhmm(b[2])}."})
    return issues

def tasks_to_json(tasks: List[Task]) -> str:
    return json.dumps({"version":"0.4.0","exported_at":datetime.utcnow().isoformat()+"Z","tasks":[asdict(t) for t in tasks]}, indent=2, ensure_ascii=False)

def tasks_from_json(data: str) -> List[Task]:
    payload = json.loads(data); rows = payload if isinstance(payload, list) else payload.get("tasks", [])
    return [Task(**{k: row.get(k, Task.__dataclass_fields__[k].default) for k in Task.__dataclass_fields__}) for row in rows]

def adapt_tasks_for_mood(tasks: List[Task], mood: str) -> List[Task]:
    out=[]
    for row in tasks:
        t=Task(**asdict(row))
        if mood == "Productive" and t.energy == "High": t.priority = "Critical" if t.priority == "High" else t.priority; t.preferred_time="Workday"
        elif mood == "Creative" and ("paper" in t.title.lower() or t.energy == "Creative"): t.priority="High"; t.preferred_time="Workday"
        elif mood == "Tired":
            if t.energy == "High": t.max_block_min=min(t.max_block_min,90)
            if t.energy == "Low" and t.priority in ["Medium","Low"]: t.priority="High"
        elif mood == "Physically energetic" and (t.energy == "Physical" or t.location in ["Home","Gym"]): t.priority="High"; t.preferred_time="Morning"
        elif mood == "Low motivation": t.max_block_min=min(t.max_block_min,90); t.min_block_min=min(t.min_block_min,30)
        out.append(t)
    return out
