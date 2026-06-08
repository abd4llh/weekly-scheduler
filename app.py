import re, uuid, json
from dataclasses import dataclass, asdict
from datetime import datetime, date, time, timedelta
from typing import List, Optional, Tuple
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_TO_INDEX = {d.lower(): i for i, d in enumerate(DAY_NAMES)} | {d.lower(): i for i, d in enumerate(DAY_SHORT)}
PRIORITY_SCORE = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Optional": 0}
ENERGY_SCORE = {"High": 3, "Medium": 2, "Physical": 2, "Creative": 2, "Low": 1}

@dataclass
class Task:
    title: str
    duration_min: int = 60
    priority: str = "Medium"
    task_type: str = "Flexible"      # Fixed, Flexible, Recurring, Multi-session
    sessions_per_week: int = 1
    fixed_day: str = ""
    fixed_start: str = ""
    preferred_time: str = "Any"      # Morning, Workday, Afternoon, Evening, Weekend, Any
    energy: str = "Medium"           # High, Medium, Low, Physical, Creative
    location: str = "Any"            # Lab, Home, Gym, Any
    splittable: bool = True
    min_block_min: int = 60
    max_block_min: int = 180
    can_overlap: bool = False
    notes: str = ""

@dataclass
class Event:
    title: str
    day_index: int
    start_min: int
    end_min: int
    priority: str = "Medium"
    source_task: str = ""
    notes: str = ""
    explanation: str = ""

@dataclass
class UnscheduledTask:
    title: str
    reason: str
    task_type: str = ""
    priority: str = ""
    duration_min: int = 0
    notes: str = ""


def minutes_to_hhmm(total_min: int) -> str:
    return f"{total_min // 60:02d}:{total_min % 60:02d}"

def hhmm_to_minutes(s: str) -> Optional[int]:
    if not isinstance(s, str) or not s.strip():
        return None
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", s)
    if not m:
        return None
    h, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= minute <= 59):
        return None
    return h * 60 + minute

def parse_duration_min(text: str) -> int:
    t = text.lower()
    h = re.search(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h)\b", t)
    if h: return int(round(float(h.group(1)) * 60))
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
    return 60

def infer_sessions(text: str) -> int:
    t = text.lower()
    if "every day" in t or "daily" in t: return 7
    m = re.search(r"(\d+)\s*times?\s*(?:a|per)?\s*week", t)
    if m: return int(m.group(1))
    if "3 times" in t: return 3
    return 1

def infer_fixed_day(text: str) -> str:
    t = text.lower()
    for d in DAY_NAMES + DAY_SHORT:
        if d.lower() in t:
            return DAY_NAMES[DAY_TO_INDEX[d.lower()]]
    return ""


def infer_fixed_start(text: str) -> str:
    """
    Extract explicit start times from natural text.

    Supported examples:
    - "Sunday 14:00"
    - "at Sunday 14:00"
    - "Sunday at 14:00"
    - "Sunday 2 pm"
    - "on Monday at 09:30"
    """
    t = text.lower()

    # 24-hour format: 14:00, 09:30, at 8:15
    m = re.search(r"\b(?:at\s*)?([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    # 12-hour format: 2 pm, 2pm, 11 AM
    m = re.search(r"\b(?:at\s*)?(1[0-2]|0?[1-9])\s*(am|pm)\b", t)
    if m:
        hour = int(m.group(1))
        suffix = m.group(2)
        if suffix == "pm" and hour != 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:00"

    return ""

def infer_priority(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["deadline", "urgent", "critical"]): return "Critical"
    if any(k in t for k in ["experiment", "paper", "shirong", "lab device"]): return "High"
    if any(k in t for k in ["gym", "german", "wife", "israa", "cooking", "groceries"]): return "Medium"
    if any(k in t for k in ["raspberry", "udemy", "personal development"]): return "Optional"
    return "Medium"

def infer_energy(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["paper", "write", "prepare", "analysis", "data", "experiment", "lab"]): return "High"
    if "gym" in t: return "Physical"
    if any(k in t for k in ["clean", "laundry", "groceries", "cabinet", "cooking"]): return "Low"
    if any(k in t for k in ["raspberry", "udemy"]): return "Creative"
    return "Medium"

def infer_location(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["experiment", "lab", "device", "inkjet"]): return "Lab"
    if "gym" in t: return "Gym"
    if any(k in t for k in ["cabinet", "house", "room", "laundry", "cooking"]): return "Home"
    return "Any"

def infer_preferred_time(text: str) -> str:
    t = text.lower()
    if "morning" in t: return "Morning"
    if "saturday" in t or "weekend" in t: return "Weekend"
    if any(k in t for k in ["experiment", "lab", "paper", "shirong"]): return "Workday"
    if any(k in t for k in ["wife", "israa", "cooking"]): return "Evening"
    return "Any"

def clean_title(line: str) -> str:
    s = re.sub(r"^\s*[-•*]\s*", "", line.strip())
    s = re.sub(r"\([^)]*\)", "", s)

    # Remove common fixed-time phrases from the display title.
    # Example: "go to the doctor at sunday 14:00" -> "go to the doctor"
    day_pattern = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)"
    time24 = r"(?:[01]?\d|2[0-3]):[0-5]\d"
    time12 = r"(?:1[0-2]|0?[1-9])\s*(?:am|pm)"

    s = re.sub(rf"\b(?:on\s+|at\s+)?{day_pattern}\s+(?:at\s+)?(?:{time24}|{time12})\b", "", s, flags=re.I)
    s = re.sub(rf"\b(?:on\s+|at\s+)?{day_pattern}\b", "", s, flags=re.I)
    s = re.sub(rf"\b(?:from\s+)?{time24}\s*(?:-|–|to|until)\s*{time24}\b", "", s, flags=re.I)
    s = re.sub(rf"\b(?:at\s+)?(?:{time24}|{time12})\b", "", s, flags=re.I)

    # Clean dangling prepositions and extra spacing.
    s = re.sub(r"\b(?:at|on)\s*$", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,-")
    return s[:120] or "Untitled task"

def infer_task_type(text: str, duration: int, sessions: int) -> str:
    t = text.lower()
    if any(k in t for k in ["every day", "daily", "times a week", "per week", "weekly"]): return "Recurring"
    if duration >= 180: return "Multi-session"
    if infer_fixed_day(text): return "Fixed"
    return "Flexible"

def parse_tasks(raw_text: str) -> List[Task]:
    tasks = []
    for line in raw_text.splitlines():
        if not line.strip(): continue
        low = line.lower()
        duration = parse_duration_min(line)
        sessions = infer_sessions(line)
        title = clean_title(line)
        fixed_day = infer_fixed_day(line)
        fixed_start = infer_fixed_start(line)
        task_type = infer_task_type(line, duration, sessions)
        preferred = infer_preferred_time(line)
        location = infer_location(line)
        energy = infer_energy(line)
        priority = infer_priority(line)
        can_overlap = any(k in low for k in ["wife", "israa", "alongside", "overlap"])

        # If the user gives an explicit day and time, it is a fixed calendar event.
        if fixed_day and fixed_start:
            task_type = "Fixed"

        # App-specific useful heuristics.
        if "book lab device" in low or "book lab devices" in low:
            fixed_day, fixed_start, task_type, duration = "Thursday", "09:30", "Fixed", 10
        if "grocery list" in low:
            fixed_day, fixed_start, task_type, duration = "Saturday", "09:30", "Fixed", 30
        if "groceries" in low and "grocery list" not in low:
            fixed_day, fixed_start, task_type, duration = "Saturday", "10:00", "Fixed", 120
        if "laundry" in low:
            fixed_day, fixed_start, task_type, duration = "Sunday", "09:45", "Fixed", 60
        if "talking with" in low or "israa" in low or "wife" in low:
            task_type, sessions, preferred, duration, can_overlap = "Recurring", 7, "Evening", max(duration, 150), True
        if "cooking" in low:
            task_type, sessions, preferred, duration, can_overlap = "Recurring", 7, "Evening", 120, True
        if "german" in low:
            task_type, sessions, preferred, duration = "Recurring", 7, "Morning", 30
        if "gym" in low:
            task_type, sessions, preferred, duration, location, energy = "Recurring", 3, "Morning", 120, "Gym", "Physical"
        if "cabinet" in low:
            task_type, sessions, duration, location, energy, preferred = "Multi-session", 4, 60, "Home", "Low", "Weekend"
        if "clean my house" in low or "clean house" in low:
            task_type, sessions, duration, location, energy, preferred = "Recurring", 5, 20, "Home", "Low", "Evening"
        if "raspberry" in low or "udemy" in low:
            priority = "Optional"

        tasks.append(Task(
            title=title, duration_min=duration, priority=priority, task_type=task_type,
            sessions_per_week=sessions, fixed_day=fixed_day, fixed_start=fixed_start,
            preferred_time=preferred, energy=energy, location=location,
            splittable=duration > 90 or sessions > 1, min_block_min=30 if duration <= 60 else 60,
            max_block_min=180 if duration >= 180 else duration, can_overlap=can_overlap, notes=line
        ))
    return tasks


def validate_tasks(tasks: List[Task], wake_min: int, sleep_min: int) -> List[dict]:
    """Return validation issues as dictionaries for easy display."""
    issues = []
    if wake_min >= sleep_min:
        issues.append({"level": "error", "task": "Schedule settings", "message": "Wake time must be earlier than sleep target."})
    fixed = []
    for t in tasks:
        if not str(t.title).strip():
            issues.append({"level": "error", "task": "Untitled", "message": "Task has no title."})
        if int(t.duration_min) <= 0:
            issues.append({"level": "error", "task": t.title, "message": "Duration must be greater than 0 minutes."})
        if t.task_type == "Fixed":
            day = DAY_TO_INDEX.get(str(t.fixed_day).lower())
            start = hhmm_to_minutes(str(t.fixed_start))
            if day is None:
                issues.append({"level": "error", "task": t.title, "message": "Fixed task needs a valid fixed_day."})
            if start is None:
                issues.append({"level": "error", "task": t.title, "message": "Fixed task needs a valid fixed_start such as 14:00."})
            elif day is not None:
                end = start + int(t.duration_min)
                if start < wake_min or end > sleep_min:
                    issues.append({"level": "warning", "task": t.title, "message": f"Fixed event {minutes_to_hhmm(start)}–{minutes_to_hhmm(end)} is outside the wake/sleep window."})
                if not t.can_overlap:
                    fixed.append((day, start, end, t.title))
        if int(t.duration_min) > 8 * 60 and t.task_type != "Multi-session" and t.splittable:
            issues.append({"level": "info", "task": t.title, "message": "Long task detected. Consider setting task_type to Multi-session."})
        if int(t.max_block_min) < int(t.min_block_min):
            issues.append({"level": "warning", "task": t.title, "message": "max_block_min is smaller than min_block_min."})
    for i in range(len(fixed)):
        d1, s1, e1, t1 = fixed[i]
        for j in range(i+1, len(fixed)):
            d2, s2, e2, t2 = fixed[j]
            if d1 == d2 and max(s1, s2) < min(e1, e2):
                issues.append({"level": "error", "task": f"{t1} / {t2}", "message": f"Fixed-event conflict on {DAY_NAMES[d1]}: {minutes_to_hhmm(s1)}–{minutes_to_hhmm(e1)} overlaps {minutes_to_hhmm(s2)}–{minutes_to_hhmm(e2)}."})
    return issues


def tasks_to_json(tasks: List[Task]) -> str:
    payload = {"version": "0.3-phase1", "exported_at": datetime.utcnow().isoformat() + "Z", "tasks": [asdict(t) for t in tasks]}
    return json.dumps(payload, indent=2, ensure_ascii=False)


def tasks_from_json(data: str) -> List[Task]:
    payload = json.loads(data)
    raw_tasks = payload if isinstance(payload, list) else payload.get("tasks", [])
    tasks = []
    for item in raw_tasks:
        kw = {f: item.get(f, Task.__dataclass_fields__[f].default) for f in Task.__dataclass_fields__}
        tasks.append(Task(**kw))
    return tasks

class Scheduler:
    def __init__(self, wake_min=360, sleep_min=1380, slot_min=15, protect_weekend=True):
        self.wake_min, self.sleep_min, self.slot_min = wake_min, sleep_min, slot_min
        self.protect_weekend = protect_weekend
        self.events: List[Event] = []
        self.unscheduled: List[UnscheduledTask] = []
        self.busy = {d: [] for d in range(7)}

    def add_unscheduled(self, task: Task, reason: str):
        self.unscheduled.append(UnscheduledTask(task.title, reason, task.task_type, task.priority, int(task.duration_min), task.notes))

    def conflicts(self, day, start, end):
        return [(s, e) for s, e in self.busy[day] if max(s, start) < min(e, end)]

    def is_free(self, day, start, end, allow_overlap=False):
        if start < self.wake_min or end > self.sleep_min or start >= end: return False
        if allow_overlap: return True
        return len(self.conflicts(day, start, end)) == 0

    def add_event(self, e: Event, allow_overlap=False):
        self.events.append(e)
        if not allow_overlap:
            self.busy[e.day_index].append((e.start_min, e.end_min))
            self.busy[e.day_index].sort()

    def candidate_windows(self, task: Task) -> List[Tuple[int, int, int]]:
        windows = []
        if task.preferred_time == "Morning":
            for d in range(7): windows.append((d, 375, 570))
        elif task.preferred_time == "Workday" or task.location == "Lab":
            for d in range(5): windows += [(d, 540, 720), (d, 780, 930), (d, 960, 1050)]
        elif task.preferred_time == "Evening":
            for d in range(7): windows.append((d, 1080, 1350))
        elif task.preferred_time == "Weekend":
            for d in [5, 6]: windows += [(d, 420, 720), (d, 840, 1140)]
        else:
            for d in range(5): windows += [(d, 540, 720), (d, 780, 1050), (d, 1080, 1200)]
            if not self.protect_weekend or task.priority in ["Critical", "High"]:
                for d in [5, 6]: windows += [(d, 540, 720), (d, 840, 1020)]
        if task.priority == "Optional":
            windows = [(4, 1080, 1200), (5, 960, 1140), (6, 840, 1020)]
        return windows

    def find_slot(self, task: Task, duration: int, preferred_days: Optional[List[int]] = None):
        windows = self.candidate_windows(task)
        if preferred_days:
            windows = [w for w in windows if w[0] in preferred_days] + [w for w in windows if w[0] not in preferred_days]
        for day, a, b in windows:
            start = a
            while start + duration <= b:
                if self.is_free(day, start, start + duration, task.can_overlap):
                    return day, start, start + duration
                start += self.slot_min
        return None

    def add_focus_guard(self):
        guards = []
        for d in range(5):
            guards.append((d, 360, 375, "Wake up / stabilize — no reels"))
            guards.append((d, 1350, 1380, "Shutdown / prepare tomorrow — no reels"))
        guards += [(5, 360, 375, "Wake up / stabilize — no reels"), (6, 420, 435, "Wake up / stabilize — no reels")]
        for d, s, e, title in guards:
            if self.is_free(d, s, e): self.add_event(Event(title, d, s, e, "High", "Focus Guard", "Protect vulnerable scrolling moments."))

    def schedule_fixed(self, task: Task):
        day = DAY_TO_INDEX.get(str(task.fixed_day).lower())
        start = hhmm_to_minutes(str(task.fixed_start))
        if day is None:
            self.add_unscheduled(task, "Fixed task has no valid day.")
            return False
        if start is None:
            self.add_unscheduled(task, "Fixed task has no valid start time, such as 14:00.")
            return False
        end = start + int(task.duration_min)
        if start < self.wake_min or end > self.sleep_min:
            self.add_unscheduled(task, f"Fixed time {minutes_to_hhmm(start)}–{minutes_to_hhmm(end)} is outside the wake/sleep window.")
            return False
        if not task.can_overlap and self.conflicts(day, start, end):
            conflict_txt = "; ".join(f"{minutes_to_hhmm(s)}–{minutes_to_hhmm(e)}" for s, e in self.conflicts(day, start, end))
            self.add_unscheduled(task, f"Fixed-event conflict with existing event(s): {conflict_txt}.")
            return False
        expl = f"Scheduled as a fixed event on {DAY_NAMES[day]} at {minutes_to_hhmm(start)} because the task specifies a fixed day/time."
        self.add_event(Event(task.title, day, start, end, task.priority, task.title, task.notes, expl), task.can_overlap)
        return True

    def schedule_recurring(self, task: Task):
        title = task.title.lower()
        if "gym" in title:
            for d in [1, 3, 5][:task.sessions_per_week]:
                if self.is_free(d, 390, 510): self.add_event(Event(task.title, d, 390, 510, task.priority, task.title, task.notes))
                else:
                    slot = self.find_slot(task, task.duration_min, [d])
                    if slot: self.add_event(Event(task.title, *slot, task.priority, task.title, task.notes))
            return
        if "german" in title:
            for d in range(7):
                s, e = (1050, 1080) if d in [1, 3, 5] else (375, 405)
                if self.is_free(d, s, e): self.add_event(Event(task.title, d, s, e, task.priority, task.title, task.notes))
                else:
                    slot = self.find_slot(task, task.duration_min, [d])
                    if slot: self.add_event(Event(task.title, *slot, task.priority, task.title, task.notes))
            return
        if "cooking" in title:
            for d in range(7):
                if self.is_free(d, 1080, 1200): self.add_event(Event(task.title, d, 1080, 1200, task.priority, task.title, task.notes))
                else:
                    slot = self.find_slot(task, task.duration_min, [d])
                    if slot: self.add_event(Event(task.title, *slot, task.priority, task.title, task.notes))
            return
        if "israa" in title or "wife" in title or "talking" in title:
            for d in range(7): self.add_event(Event(task.title, d, 1200, 1350, task.priority, task.title, task.notes), allow_overlap=True)
            return
        days = list(range(5)) if task.sessions_per_week == 5 else [0,2,4] if task.sessions_per_week == 3 else list(range(7))[:task.sessions_per_week]
        for d in days:
            slot = self.find_slot(task, task.duration_min, [d])
            if slot: self.add_event(Event(task.title, *slot, task.priority, task.title, task.notes), task.can_overlap)

    def schedule_flexible_or_multisession(self, task: Task):
        if task.task_type == "Multi-session" and "cabinet" in task.title.lower():
            blocks = [task.duration_min] * task.sessions_per_week
        elif task.task_type == "Multi-session":
            remaining, blocks = task.duration_min, []
            while remaining > 0:
                block = min(task.max_block_min, remaining)
                if block < task.min_block_min and blocks: blocks[-1] += block
                else: blocks.append(block)
                remaining -= block
        else:
            blocks = [task.duration_min]
        scheduled_any = False
        for block in blocks:
            slot = self.find_slot(task, block)
            if slot:
                d, s, e = slot
                expl = f"Placed on {DAY_NAMES[d]} {minutes_to_hhmm(s)}–{minutes_to_hhmm(e)} because it is {task.priority.lower()} priority and matches the {task.preferred_time.lower()} / {task.location.lower()} scheduling window."
                self.add_event(Event(task.title, d, s, e, task.priority, task.title, task.notes, expl), task.can_overlap)
                scheduled_any = True
            else:
                self.add_unscheduled(task, f"Could not fit a {block}-minute block into available compatible windows.")

    def schedule(self, tasks: List[Task], include_focus_guard=True):
        if include_focus_guard: self.add_focus_guard()
        for t in tasks:
            if t.task_type == "Fixed": self.schedule_fixed(t)
        for t in tasks:
            if t.task_type == "Recurring": self.schedule_recurring(t)
        rest = [t for t in tasks if t.task_type in ["Flexible", "Multi-session"]]
        rest.sort(key=lambda t: (PRIORITY_SCORE.get(t.priority, 2), ENERGY_SCORE.get(t.energy, 2), t.duration_min), reverse=True)
        for t in rest: self.schedule_flexible_or_multisession(t)
        self.events.sort(key=lambda e: (e.day_index, e.start_min, e.end_min))
        return self.events, self.unscheduled


def adapt_tasks_for_mood(tasks: List[Task], mood: str) -> List[Task]:
    out = []
    for t in tasks:
        task = Task(**asdict(t))
        if mood == "Productive" and task.energy == "High":
            task.priority = "Critical" if task.priority == "High" else task.priority
            task.preferred_time = "Workday"
        elif mood == "Creative" and ("paper" in task.title.lower() or task.energy == "Creative"):
            task.priority, task.preferred_time = "High", "Workday"
        elif mood == "Tired":
            if task.energy == "High": task.max_block_min = min(task.max_block_min, 90)
            if task.energy == "Low" and task.priority in ["Medium", "Low"]: task.priority = "High"
        elif mood == "Physically energetic" and (task.energy == "Physical" or task.location in ["Home", "Gym"]):
            task.priority, task.preferred_time = "High", "Morning"
        elif mood == "Low motivation":
            task.max_block_min, task.min_block_min = min(task.max_block_min, 90), min(task.min_block_min, 30)
        out.append(task)
    return out


def next_monday(today: date) -> date:
    delta = (7 - today.weekday()) % 7
    return today + timedelta(days=delta or 7)

def escape_ics_text(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

def fold_line(line: str, limit=75) -> str:
    parts = []
    while len(line) > limit:
        parts.append(line[:limit])
        line = " " + line[limit:]
    parts.append(line)
    return "\r\n".join(parts)

def events_to_ics(events: List[Event], week_start: date, calendar_name="Weekly Scheduler") -> str:
    tzid = "Europe/Berlin"
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Weekly Scheduler MVP//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_ics_text(calendar_name)}", "X-WR-TIMEZONE:Europe/Berlin",
        "BEGIN:VTIMEZONE", "TZID:Europe/Berlin", "X-LIC-LOCATION:Europe/Berlin",
        "BEGIN:DAYLIGHT", "TZOFFSETFROM:+0100", "TZOFFSETTO:+0200", "TZNAME:CEST", "DTSTART:19700329T020000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU", "END:DAYLIGHT",
        "BEGIN:STANDARD", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100", "TZNAME:CET", "DTSTART:19701025T030000", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD",
        "END:VTIMEZONE"
    ]
    for e in events:
        sd = week_start + timedelta(days=e.day_index)
        start_dt = datetime.combine(sd, time(e.start_min // 60, e.start_min % 60))
        end_dt = datetime.combine(sd, time(e.end_min // 60, e.end_min % 60))
        lines += [
            "BEGIN:VEVENT", f"UID:{uuid.uuid4()}@weekly-scheduler-mvp", f"DTSTAMP:{stamp}",
            f"DTSTART;TZID={tzid}:{start_dt.strftime('%Y%m%dT%H%M%S')}", f"DTEND;TZID={tzid}:{end_dt.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{escape_ics_text(e.title)}", f"DESCRIPTION:{escape_ics_text((e.notes or e.source_task) + ('\n\nWhy scheduled here: ' + e.explanation if e.explanation else ''))}"
        ]
        if any(k in e.title.lower() for k in ["experiment", "gym", "german", "send", "book lab"]):
            lines += ["BEGIN:VALARM", "TRIGGER:-PT10M", "ACTION:DISPLAY", f"DESCRIPTION:{escape_ics_text(e.title)}", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(l) for l in lines) + "\r\n"

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


# -----------------------------
# Google Calendar-style visual weekly view
# -----------------------------

PRIORITY_CLASS = {
    "Critical": "critical",
    "High": "high",
    "Medium": "medium",
    "Low": "low",
    "Optional": "optional",
}


def html_escape_simple(value):
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def render_calendar_html(events, week_start, start_hour=6, end_hour=23, px_per_hour=72):
    body_h = (end_hour - start_hour) * px_per_hour

    hour_marks = []
    for h in range(start_hour, end_hour + 1):
        top = (h - start_hour) * px_per_hour
        hour_marks.append(f'<div class="hour-line" style="top:{top}px"></div>')
        hour_marks.append(f'<div class="hour-label" style="top:{max(top-8,0)}px">{h:02d}:00</div>')

    headers = []
    for i, day in enumerate(DAY_SHORT):
        d = week_start + timedelta(days=i)
        headers.append(
            f'<div class="day-head"><div class="dow">{day}</div><div class="date-num">{d.day}</div></div>'
        )

    columns = []
    for i in range(7):
        blocks = []
        day_events = [e for e in events if e.day_index == i]
        for e in day_events:
            start = max(e.start_min, start_hour * 60)
            end = min(e.end_min, end_hour * 60)
            if end <= start:
                continue
            top = ((start / 60) - start_hour) * px_per_hour
            height = max(((end - start) / 60) * px_per_hour - 4, 20)
            klass = PRIORITY_CLASS.get(e.priority, "medium")
            title = html_escape_simple(e.title)
            notes = html_escape_simple(e.explanation or e.notes or '')
            time_txt = f"{minutes_to_hhmm(e.start_min)}–{minutes_to_hhmm(e.end_min)}"
            blocks.append(
                f'<div class="event {klass}" style="top:{top}px;height:{height}px">'
                f'<div class="event-title">{title}</div>'
                f'<div class="event-time">{time_txt}</div>'
                f'<div class="event-notes">{notes}</div>'
                f'</div>'
            )
        columns.append(f'<div class="day-col">{"".join(blocks)}</div>')

    week_end = week_start + timedelta(days=6)
    return f'''
    <style>
      body {{ margin:0; font-family: Inter, Roboto, Arial, sans-serif; color:#202124; }}
      .cal-shell {{ border:1px solid #dadce0; border-radius:18px; overflow:hidden; background:#fff; box-shadow:0 1px 2px rgba(60,64,67,.15), 0 2px 6px rgba(60,64,67,.08); }}
      .toolbar {{ display:flex; justify-content:space-between; align-items:center; gap:12px; padding:14px 18px; border-bottom:1px solid #dadce0; }}
      .title {{ font-size:19px; font-weight:700; letter-spacing:-.02em; }}
      .range {{ color:#5f6368; font-size:13px; margin-top:2px; }}
      .legend {{ display:flex; gap:10px; flex-wrap:wrap; color:#5f6368; font-size:12px; }}
      .dot {{ width:9px; height:9px; border-radius:999px; display:inline-block; margin-right:4px; }}
      .d-critical {{ background:#d93025; }} .d-high {{ background:#1a73e8; }} .d-medium {{ background:#188038; }} .d-low {{ background:#f9ab00; }} .d-optional {{ background:#9334e6; }}
      .scroll {{ overflow-x:auto; }}
      .week-head {{ min-width:960px; display:grid; grid-template-columns:76px repeat(7, 1fr); border-bottom:1px solid #dadce0; }}
      .tz {{ border-right:1px solid #dadce0; color:#5f6368; font-size:11px; display:flex; align-items:end; justify-content:center; padding-bottom:10px; }}
      .day-head {{ height:72px; text-align:center; border-right:1px solid #dadce0; padding-top:9px; }}
      .day-head:last-child {{ border-right:none; }}
      .dow {{ color:#5f6368; font-size:12px; text-transform:uppercase; letter-spacing:.06em; }}
      .date-num {{ margin:6px auto 0; width:36px; height:36px; line-height:36px; border-radius:999px; font-size:20px; font-weight:500; }}
      .day-head:first-of-type .date-num {{ background:#e8f0fe; color:#1967d2; }}
      .week-body {{ min-width:960px; display:grid; grid-template-columns:76px repeat(7, 1fr); height:{body_h}px; position:relative; }}
      .axis {{ position:relative; height:{body_h}px; border-right:1px solid #dadce0; background:#fff; }}
      .hour-line {{ position:absolute; left:0; right:0; height:1px; background:#eef0f3; }}
      .hour-label {{ position:absolute; right:8px; color:#5f6368; font-size:11px; }}
      .day-col {{ position:relative; height:{body_h}px; border-right:1px solid #dadce0; background:linear-gradient(to bottom, transparent {px_per_hour-1}px, #eef0f3 {px_per_hour-1}px, #eef0f3 {px_per_hour}px); background-size:100% {px_per_hour}px; }}
      .day-col:last-child {{ border-right:none; }}
      .event {{ position:absolute; left:5px; right:5px; border-radius:10px; padding:6px 7px; overflow:hidden; font-size:12px; line-height:1.23; box-shadow:0 1px 2px rgba(0,0,0,.12); border-left:4px solid rgba(0,0,0,.2); }}
      .event-title {{ font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
      .event-time {{ font-size:11px; opacity:.88; margin-top:2px; }}
      .event-notes {{ font-size:10px; opacity:.70; margin-top:4px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
      .critical {{ background:#fce8e6; color:#5f0f0a; border-left-color:#d93025; }}
      .high {{ background:#e8f0fe; color:#174ea6; border-left-color:#1a73e8; }}
      .medium {{ background:#e6f4ea; color:#0d652d; border-left-color:#188038; }}
      .low {{ background:#fef7e0; color:#7a4d00; border-left-color:#f9ab00; }}
      .optional {{ background:#f3e8fd; color:#681da8; border-left-color:#9334e6; }}
    </style>
    <div class="cal-shell">
      <div class="toolbar">
        <div><div class="title">Weekly Schedule</div><div class="range">{week_start.strftime('%d %b %Y')} – {week_end.strftime('%d %b %Y')}</div></div>
        <div class="legend">
          <span><i class="dot d-critical"></i>Critical</span><span><i class="dot d-high"></i>High</span><span><i class="dot d-medium"></i>Medium</span><span><i class="dot d-low"></i>Low</span><span><i class="dot d-optional"></i>Optional</span>
        </div>
      </div>
      <div class="scroll">
        <div class="week-head"><div class="tz">GMT+2</div>{''.join(headers)}</div>
        <div class="week-body"><div class="axis">{''.join(hour_marks)}</div>{''.join(columns)}</div>
      </div>
    </div>
    '''


# -----------------------------
# Streamlit UI
# -----------------------------

st.set_page_config(page_title="Weekly Scheduler", page_icon="🗓️", layout="wide", initial_sidebar_state="expanded")

st.markdown('''
<style>
.main > div { padding-top:1.25rem; }
.hero { padding:22px 24px; border:1px solid #dadce0; border-radius:22px; background:linear-gradient(135deg,#f8fafd 0%,#fff 45%,#f1f5ff 100%); margin-bottom:18px; }
.hero-title { font-size:34px; font-weight:760; letter-spacing:-.045em; color:#202124; margin-bottom:6px; }
.hero-sub { color:#5f6368; font-size:15px; max-width:920px; }
div[data-testid="stMetric"] { border:1px solid #dadce0; border-radius:16px; padding:10px 14px; background:#fff; }
</style>
<div class="hero"><div class="hero-title">Weekly Scheduler</div><div class="hero-sub">Phase 1: validation, unscheduled tasks, conflict detection, explanations, JSON save/load, and Google Calendar export.</div></div>
''', unsafe_allow_html=True)

with st.sidebar:
    st.header("Schedule settings")
    week_start = st.date_input("Week starts on", value=next_monday(date.today()))
    wake_time = st.time_input("Wake time", value=time(6, 0))
    sleep_time = st.time_input("Sleep target", value=time(23, 0))
    mood = st.selectbox("Mood / energy mode", ["Normal", "Productive", "Creative", "Tired", "Physically energetic", "Low motivation"])
    protect_weekend = st.checkbox("Protect weekend from heavy work", value=True)
    include_focus_guard = st.checkbox("Add Focus Guard / no-reels blocks", value=False)
    st.divider()
    st.caption("Calendar display")
    start_hour = st.slider("Start hour", 4, 10, 6)
    end_hour = st.slider("End hour", 18, 24, 23)
    px_per_hour = st.slider("Row height", 48, 96, 72)

# Keep the raw text area, parsed table, and generated calendar in sync.
# Streamlit widgets keep their own state, so we version the data editor key whenever
# the raw task list is reparsed. This prevents the calendar from using stale rows.
if "raw_task_text" not in st.session_state:
    st.session_state.raw_task_text = DEFAULT_TASKS

if "last_parsed_raw" not in st.session_state:
    st.session_state.last_parsed_raw = st.session_state.raw_task_text

if "editor_version" not in st.session_state:
    st.session_state.editor_version = 0

if "parsed_tasks" not in st.session_state:
    st.session_state.parsed_tasks = parse_tasks(st.session_state.raw_task_text)

def df_to_tasks(df):
    tasks = []
    for _, row in df.iterrows():
        if not str(row.get("title", "")).strip():
            continue
        kw = {f: row.get(f, Task.__dataclass_fields__[f].default) for f in Task.__dataclass_fields__}
        for k in ["duration_min", "sessions_per_week", "min_block_min", "max_block_min"]:
            try:
                kw[k] = int(kw[k])
            except Exception:
                kw[k] = int(Task.__dataclass_fields__[k].default)
        kw["splittable"], kw["can_overlap"] = bool(kw["splittable"]), bool(kw["can_overlap"])
        tasks.append(Task(**kw))
    return tasks

tab_calendar, tab_tasks, tab_issues, tab_table = st.tabs(["📅 Calendar", "📝 Tasks", "⚠️ Issues", "📋 Table"])

with tab_tasks:
    st.subheader("1) Paste messy task list")
    raw = st.text_area("Task list", height=320, key="raw_task_text")

    # Auto-sync: whenever the messy task list changes, reparse it before showing
    # the editable table. That way newly added tasks appear immediately.
    if raw != st.session_state.last_parsed_raw:
        st.session_state.parsed_tasks = parse_tasks(raw)
        st.session_state.last_parsed_raw = raw
        st.session_state.editor_version += 1
        if "events" in st.session_state:
            del st.session_state["events"]

    col_parse, col_load, col_hint = st.columns([1.1, 1.1, 3.8])
    with col_parse:
        if st.button("Refresh task table", type="primary", use_container_width=True):
            st.session_state.parsed_tasks = parse_tasks(st.session_state.raw_task_text)
            st.session_state.last_parsed_raw = st.session_state.raw_task_text
            st.session_state.editor_version += 1
            if "events" in st.session_state:
                del st.session_state["events"]
            st.rerun()
    with col_load:
        uploaded = st.file_uploader("Load JSON", type=["json"], label_visibility="collapsed")
        if uploaded is not None:
            try:
                loaded = tasks_from_json(uploaded.read().decode("utf-8"))
                st.session_state.parsed_tasks = loaded
                st.session_state.raw_task_text = "\n".join("• " + (t.notes or t.title) for t in loaded)
                st.session_state.last_parsed_raw = st.session_state.raw_task_text
                st.session_state.editor_version += 1
                if "events" in st.session_state:
                    del st.session_state["events"]
                st.success("Loaded task JSON.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not load JSON: {exc}")
    with col_hint:
        st.caption("New lines are automatically parsed. You can also save/load the edited task list as JSON.")

    st.subheader("2) Review and edit parsed tasks")
    tasks_df = pd.DataFrame([asdict(t) for t in st.session_state.parsed_tasks])
    edited_df = st.data_editor(
        tasks_df, num_rows="dynamic", use_container_width=True, height=430,
        column_config={
            "priority": st.column_config.SelectboxColumn("priority", options=list(PRIORITY_SCORE.keys())),
            "task_type": st.column_config.SelectboxColumn("task_type", options=["Fixed", "Flexible", "Recurring", "Multi-session"]),
            "fixed_day": st.column_config.SelectboxColumn("fixed_day", options=[""] + DAY_NAMES),
            "preferred_time": st.column_config.SelectboxColumn("preferred_time", options=["Morning", "Workday", "Afternoon", "Evening", "Weekend", "Any"]),
            "energy": st.column_config.SelectboxColumn("energy", options=["High", "Medium", "Low", "Physical", "Creative"]),
            "location": st.column_config.SelectboxColumn("location", options=["Lab", "Home", "Gym", "Any"]),
        },
        key=f"task_editor_{st.session_state.editor_version}",
    )
    if st.button("Generate / update calendar", type="primary"):
        # Use the reviewed table as the source of truth. If the raw task list was
        # changed, the table has already been refreshed above before this runs.
        tasks = df_to_tasks(edited_df)
        st.session_state.parsed_tasks = tasks
        if mood != "Normal":
            tasks = adapt_tasks_for_mood(tasks, mood)
        scheduler = Scheduler(wake_time.hour * 60 + wake_time.minute, sleep_time.hour * 60 + sleep_time.minute, protect_weekend=protect_weekend)
        st.session_state.events, st.session_state.unscheduled = scheduler.schedule(tasks, include_focus_guard)
        st.session_state.issues = validate_tasks(tasks, wake_time.hour * 60 + wake_time.minute, sleep_time.hour * 60 + sleep_time.minute)
        st.success("Calendar updated. Open the Calendar tab.")
        st.download_button("Save reviewed task JSON", data=tasks_to_json(tasks).encode("utf-8"), file_name="weekly_scheduler_tasks.json", mime="application/json")

if "events" not in st.session_state:
    tasks = st.session_state.parsed_tasks
    if mood != "Normal":
        tasks = adapt_tasks_for_mood(tasks, mood)
    scheduler = Scheduler(wake_time.hour * 60 + wake_time.minute, sleep_time.hour * 60 + sleep_time.minute, protect_weekend=protect_weekend)
    st.session_state.events, st.session_state.unscheduled = scheduler.schedule(tasks, include_focus_guard)
    st.session_state.issues = validate_tasks(tasks, wake_time.hour * 60 + wake_time.minute, sleep_time.hour * 60 + sleep_time.minute)

events = st.session_state.events
unscheduled = st.session_state.get("unscheduled", [])
issues = st.session_state.get("issues", [])
rows = [{"Day": DAY_NAMES[e.day_index], "Start": minutes_to_hhmm(e.start_min), "End": minutes_to_hhmm(e.end_min), "Task": e.title, "Priority": e.priority, "Explanation": e.explanation, "Notes": e.notes} for e in events]
schedule_df = pd.DataFrame(rows)

with tab_calendar:
    c1, c2, c3, c4, c5 = st.columns(5)
    counted_events = [e for e in events if e.source_task != "Focus Guard"]
    total_hours = sum((e.end_min - e.start_min) for e in counted_events) / 60
    high_count = sum(1 for e in counted_events if e.priority in ["Critical", "High"])
    weekend_hours = sum((e.end_min - e.start_min) for e in counted_events if e.day_index in [5, 6]) / 60
    c1.metric("Scheduled tasks", len(counted_events))
    c2.metric("Scheduled hours", f"{total_hours:.1f}")
    c3.metric("Unscheduled", len(unscheduled))
    c4.metric("High-priority blocks", high_count)
    c5.metric("Weekend hours", f"{weekend_hours:.1f}")
    if unscheduled:
        st.warning(f"{len(unscheduled)} task(s) could not be fully scheduled. Check the Issues tab.")

    components.html(render_calendar_html(events, week_start, start_hour, end_hour, px_per_hour), height=(end_hour - start_hour)*px_per_hour + 190, scrolling=True)
    st.markdown("### Export")
    ics_content = events_to_ics(events, week_start=week_start)
    st.download_button("Download Google Calendar .ics", data=ics_content.encode("utf-8"), file_name="weekly_scheduler_export.ics", mime="text/calendar")

with tab_issues:
    st.markdown("### Validation warnings")
    if not issues:
        st.success("No validation issues found.")
    else:
        for issue in issues:
            msg = f"**{issue['task']}** — {issue['message']}"
            if issue['level'] == 'error': st.error(msg)
            elif issue['level'] == 'warning': st.warning(msg)
            else: st.info(msg)
        st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)

    st.markdown("### Unscheduled or partially scheduled tasks")
    if not unscheduled:
        st.success("Everything was scheduled.")
    else:
        st.dataframe(pd.DataFrame([asdict(u) for u in unscheduled]), use_container_width=True, hide_index=True)


with tab_table:
    st.markdown("### Schedule table")
    st.dataframe(schedule_df, use_container_width=True, hide_index=True)
    st.markdown("### Day-by-day")
    for day in DAY_NAMES:
        with st.expander(day, expanded=day in ["Monday", "Tuesday"]):
            st.dataframe(schedule_df[schedule_df["Day"] == day][["Start", "End", "Task", "Priority", "Explanation"]], use_container_width=True, hide_index=True)
    if not schedule_df.empty:
        st.markdown("### Workload summary")
        tmp = schedule_df.copy()
        tmp["Duration_h"] = (pd.to_timedelta(tmp["End"] + ":00") - pd.to_timedelta(tmp["Start"] + ":00")).dt.total_seconds() / 3600
        by_day = tmp.groupby("Day", sort=False)["Duration_h"].sum().reindex(DAY_NAMES).fillna(0)
        st.bar_chart(by_day)
