from dataclasses import dataclass

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_TO_INDEX = {d.lower(): i for i, d in enumerate(DAY_NAMES)} | {d.lower(): i for i, d in enumerate(DAY_SHORT)}

PRIORITY_SCORE = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Optional": 0}
ENERGY_SCORE = {"High": 3, "Medium": 2, "Physical": 2, "Creative": 2, "Low": 1}
CATEGORIES = ["Work", "Lab", "Writing", "Admin", "Health", "Home", "Relationship", "Social", "Learning", "Optional", "Focus", "Other"]
PLANNING_MODES = ["Balanced week", "Work-heavy week", "Recovery week", "Deadline mode", "Social weekend mode"]
APP_VERSION = "Weekly Scheduler v0.10.1 — public one-button UX"

@dataclass
class Task:
    title: str
    duration_min: int = 60
    priority: str = "Medium"
    task_type: str = "Flexible"
    sessions_per_week: int = 1
    fixed_day: str = ""
    fixed_start: str = ""
    preferred_time: str = "Any"
    energy: str = "Medium"
    location: str = "Any"
    splittable: bool = True
    min_block_min: int = 30
    max_block_min: int = 180
    can_overlap: bool = False
    notes: str = ""
    category: str = "Other"
    required_day: str = ""
    earliest_day: str = ""
    deadline_day: str = ""
    deadline_time: str = ""
    depends_on: str = ""
    phase: int = 0
    confidence: float = 0.8
    duration_is_estimated: bool = True
    assumptions: str = ""
    needs_clarification: bool = False
    clarification_question: str = ""

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
    category: str = "Other"

@dataclass
class UnscheduledTask:
    title: str
    reason: str
    task_type: str = ""
    priority: str = ""
    duration_min: int = 0
    notes: str = ""
    category: str = "Other"
