import json
from openai import OpenAI
from models import Task, CATEGORIES, DAY_NAMES

PRIORITIES = ["Critical", "High", "Medium", "Low", "Optional"]
TASK_TYPES = ["Fixed", "Flexible", "Recurring", "Multi-session"]
PREFERRED_TIMES = ["Morning", "Workday", "Afternoon", "Evening", "Weekend", "Any"]
ENERGIES = ["High", "Medium", "Low", "Physical", "Creative"]
LOCATIONS = ["Lab", "Home", "Gym", "