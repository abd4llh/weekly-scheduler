from typing import List

from models import DAY_NAMES, PRIORITY_SCORE, ENERGY_SCORE, Task, Event, UnscheduledTask
from parser_utils import hhmm_to_minutes, minutes_to_hhmm


class Scheduler:
    def __init__(self, wake_min=360, sleep_min=1380, slot_min=15, protect_weekend=True, planning_mode="Balanced week"):
        self.wake_min, self.sleep_min, self.slot_min = wake_min, sleep_min, slot_min
        self.protect_weekend, self.planning_mode = protect_weekend, planning_mode