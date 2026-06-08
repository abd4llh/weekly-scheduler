from dataclasses import replace
from typing import List
from models import Task


def infer_category_from_text(text: str) -> str:
    x = (text or "").lower()
    if any(k in x for k in ["inkjet", "experiment", "lab device", "lab", "device"]):
        return "Lab"
    if any(k in x for k in ["prepare paper", "paper with", "write", "writing", "federico", "giorgio"]):
        return "Writing"
    if any(k in x for k in ["send", "email", "book"]):
        return "Admin"
    if any(k in x for k in ["gym", "doctor", "health"]):
        return "Health"
    if any(k in x for k in ["cabinet", "clean", "laundry", "groceries", "grocery", "cooking", "house", "room"]):
        return "Home"
    if any(k in x for k in ["israa", "wife", "relationship"]):
        return "Relationship"
    if any(k in x for k in ["ahmad", "friend", "social", "meet "]):
        return "Social"
    if any(k in x for k in ["german", "study german"]):
        return "Learning"
    if any(k in x for k in ["raspberry", "udemy", "personal development"]):
        return "Optional"
    return "Other"


def normalize_task_category(task: Task) -> Task:
    current = getattr(task, "category", "Other") or "Other"
    if current != "Other":
        return task
    text = f"{task.title} {task.notes}"
    return replace(task, category=infer_category_from_text(text))


def normalize_task_categories(tasks: List[Task]) -> List[Task]:
    return [normalize_task_category(t) for t in tasks]
