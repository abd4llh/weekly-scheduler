from typing import List
from models import Task


def normalize_task_categories(tasks: List[Task]) -> List[Task]:
    """Keep AI-provided categories unchanged.

    Category inference is intentionally handled by the AI parser. This helper is
    retained so older imports keep working, but it no longer applies keyword or
    user-specific rules.
    """
    return tasks
