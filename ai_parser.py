import json
from typing import List, Tuple

from openai import OpenAI

from models import CATEGORIES, DAY_NAMES, Task

VALID_PRIORITIES = ["Critical", "High", "Medium", "Low", "Optional"]
VALID_TASK_TYPES = ["Fixed", "Flexible", "Recurring", "Multi-session"]
VALID_TIMES = ["Morning", "Workday", "Afternoon", "Evening", "Weekend", "Any"]
VALID_ENERGY = ["High", "Medium", "Low", "Physical", "Creative"]
VALID_LOCATIONS = ["Lab", "Home", "Gym", "Any"]


class AIParserError(Exception):
    pass


def _allowed(value,