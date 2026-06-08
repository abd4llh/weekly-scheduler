import json
from openai import OpenAI
from models import Task, CATEGORIES, DAY_NAMES

PRIORITIES = ["Critical", "High", "Medium", "Low", "Optional"]
TYPES = ["Fixed", "Flexible", "Recurring", "Multi-session"]
PREFS = ["Morning", "Workday", "Afternoon", "Evening", "Weekend", "Any"]
ENERGY = ["High", "Medium", "Low", "Physical", "Creative"]
LOCATIONS