from typing import List

from models import DAY_NAMES, PRIORITY_SCORE, ENERGY_SCORE, Task, Event, UnscheduledTask
from parser_utils import hhmm_to_minutes, minutes_to_hhmm


class Scheduler:
    def __init__(self, wake_min=360, sleep_min=1380, slot_min=15, protect_weekend=True, planning_mode="Balanced week"):
        self.wake_min, self.sleep_min, self.slot_min = wake_min, sleep_min, slot_min
        self.protect_weekend, self.planning_mode = protect_weekend, planning_mode
        self.events: List[Event] = []
        self.unscheduled: List[UnscheduledTask] = []
        self.busy = {d: [] for d in range(7)}
        self.flex_load = {d: 0 for d in range(7)}
        self.high_load = {d: 0 for d in range(7)}
        self.daily_flex_cap = {0: 420, 1: 420, 2: 420, 3: 420, 4: 360, 5: 240, 6: 180}
        self.daily_high_cap = {0: 360, 1: 360, 2: 360, 3: 360, 4: 300, 5: 60, 6: 0}
        self.apply_planning_mode()

    def apply_planning_mode(self):
        if self.planning_mode == "Work-heavy week":
            self.daily_flex_cap.update({0: 480, 1: 480, 2: 480, 3: 480, 4: 420, 5: 300, 6: 180})
            self.daily_high_cap.update({0: 420, 1: 420, 2: 420, 3: 420, 4: 360, 5: 120, 