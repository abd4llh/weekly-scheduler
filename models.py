from dataclasses import dataclass

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_TO_INDEX = {d.lower(): i for i, d in enumerate(DAY_NAMES)} | {d.lower(): i for i, d in enumerate(DAY_SHORT)}

PRIORITY_SCORE = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Optional": 0}
ENERGY_SCORE