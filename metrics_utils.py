from typing import List, Dict
from models import Event, UnscheduledTask, DAY_NAMES

WORK_CATEGORIES = {"Work", "Lab", "Writing", "Admin"}
PERSONAL_CATEGORIES = {"Health", "Home", "Learning", "Social", "Optional"}
RELATIONSHIP_CATEGORIES = {"Relationship"}

def event_duration_h(event: Event) -> float:
    return max(event.end_min - event.start_min, 0) / 60

def merge_intervals(intervals):
    if not intervals:
        return []
    intervals = sorted(intervals)
    merged = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged

def occupied_hours(events: List[Event]) -> float:
    by_day = {d: [] for d in range(7)}
    for e in events:
        if e.source_task == "Focus Guard":
            continue
        by_day[e.day_index].append((e.start_min, e.end_min))
    total = 0
    for intervals in by_day.values():
        for start, end in merge_intervals(intervals):
            total += max(end - start, 0)
    return total / 60

def category_hours(events: List[Event]) -> Dict[str, float]:
    out = {}
    for e in events:
        cat = e.category or "Other"
        out[cat] = out.get(cat, 0.0) + event_duration_h(e)
    return out

def workload_summary(events: List[Event], unscheduled: List[UnscheduledTask]) -> Dict[str, float]:
    counted = [e for e in events if e.source_task != "Focus Guard"]
    total_scheduled = sum(event_duration_h(e) for e in counted)
    true_occupied = occupied_hours(counted)
    cats = category_hours(counted)
    return {
        "scheduled_tasks": len(counted),
        "scheduled_hours": round(total_scheduled, 1),
        "true_occupied_hours": round(true_occupied, 1),
        "overlap_hours": round(max(total_scheduled - true_occupied, 0), 1),
        "work_hours": round(sum(cats.get(c, 0) for c in WORK_CATEGORIES), 1),
        "personal_hours": round(sum(cats.get(c, 0) for c in PERSONAL_CATEGORIES), 1),
        "relationship_hours": round(sum(cats.get(c, 0) for c in RELATIONSHIP_CATEGORIES), 1),
        "other_hours": round(cats.get("Other", 0), 1),
        "weekend_hours": round(sum(event_duration_h(e) for e in counted if e.day_index in [5, 6]), 1),
        "high_priority_blocks": sum(1 for e in counted if e.priority in ["Critical", "High"]),
        "unscheduled_count": len(unscheduled),
        "unscheduled_high_count": sum(1 for u in unscheduled if u.priority in ["Critical", "High"]),
    }

def by_day_dataframe(events: List[Event]):
    import pandas as pd
    rows = []
    for d in range(7):
        day_events = [e for e in events if e.day_index == d and e.source_task != "Focus Guard"]
        intervals = [(e.start_min, e.end_min) for e in day_events]
        rows.append({
            "Day": DAY_NAMES[d],
            "True occupied h": round(sum(end - start for start, end in merge_intervals(intervals)) / 60, 1),
            "Work h": round(sum(event_duration_h(e) for e in day_events if e.category in WORK_CATEGORIES), 1),
            "Personal h": round(sum(event_duration_h(e) for e in day_events if e.category in PERSONAL_CATEGORIES), 1),
            "Relationship h": round(sum(event_duration_h(e) for e in day_events if e.category in RELATIONSHIP_CATEGORIES), 1),
            "Other h": round(sum(event_duration_h(e) for e in day_events if e.category == "Other"), 1),
        })
    return pd.DataFrame(rows)
