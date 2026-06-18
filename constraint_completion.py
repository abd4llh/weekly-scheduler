import re
from typing import Dict, List, Optional, Tuple

from models import DAY_NAMES, DAY_TO_INDEX, Event, Task, UnscheduledTask
from parser_utils import hhmm_to_minutes


def _norm_tokens(text: str):
    words = re.findall(r"[a-zA-Z0-9]+", str(text).lower())
    stop = {
        "the", "and", "for", "with", "this", "that", "task", "work",
        "session", "sessions", "daily", "every", "main",
    }
    return {word for word in words if len(word) > 2 and word not in stop}


def _match_score(a: Task, b: Task) -> float:
    left = _norm_tokens(f"{a.title} {a.notes}")
    right = _norm_tokens(f"{b.title} {b.notes}")
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _best_task_match(anchor: Task, tasks: List[Task]):
    for task in tasks:
        if task.title.strip().lower() == anchor.title.strip().lower():
            return task, 1.0
    scored = sorted(
        ((_match_score(anchor, task), task) for task in tasks),
        key=lambda item: item[0],
        reverse=True,
    )
    return (scored[0][1], scored[0][0]) if scored else (None, 0.0)


def _expected_minutes(task: Task) -> int:
    if task.task_type == "Recurring":
        return int(task.duration_min) * int(task.sessions_per_week)
    return int(task.duration_min)


def _scheduled_minutes(events: List[Event]) -> int:
    return sum(max(0, event.end_min - event.start_min) for event in events)


def _event_abs_start(event: Event) -> int:
    return event.day_index * 1440 + event.start_min


def _event_abs_end(event: Event) -> int:
    return event.day_index * 1440 + event.end_min


def _resolve_dependency_title(task: Task, tasks: List[Task]) -> str:
    dependency = str(task.depends_on or "").strip()
    if not dependency:
        return ""
    for candidate in tasks:
        if candidate.title.strip().lower() == dependency.lower():
            return candidate.title
    probe = Task(title=dependency, notes=dependency)
    match, score = _best_task_match(probe, tasks)
    return match.title if match and score >= 0.25 else dependency


def _topological_order(tasks: List[Task]) -> List[Task]:
    pending = list(tasks)
    ordered: List[Task] = []
    placed = set()

    while pending:
        progressed = False
        for task in list(pending):
            dependency = _resolve_dependency_title(task, tasks)
            if not dependency or dependency in placed or dependency == task.title:
                ordered.append(task)
                placed.add(task.title)
                pending.remove(task)
                progressed = True
        if not progressed:
            ordered.extend(pending)
            break
    return ordered


def _allowed_days(task: Task) -> List[int]:
    if task.fixed_day:
        day = DAY_TO_INDEX.get(task.fixed_day.lower())
        return [day] if day is not None else list(range(7))
    if task.required_day:
        day = DAY_TO_INDEX.get(task.required_day.lower())
        return [day] if day is not None else list(range(7))

    days = list(range(7))
    if task.earliest_day:
        earliest = DAY_TO_INDEX.get(task.earliest_day.lower())
        if earliest is not None:
            days = [day for day in days if day >= earliest]
    if task.deadline_day:
        latest = DAY_TO_INDEX.get(task.deadline_day.lower())
        if latest is not None:
            days = [day for day in days if day <= latest]
    if task.preferred_time == "Weekend":
        days = [day for day in days if day in [5, 6]]
    return days


def _preferred_window(task: Task, day: int, wake_min: int, sleep_min: int) -> Tuple[int, int]:
    if task.preferred_time == "Morning":
        return wake_min, min(12 * 60, sleep_min)
    if task.preferred_time == "Afternoon":
        return max(12 * 60, wake_min), min(18 * 60, sleep_min)
    if task.preferred_time == "Evening":
        return max(17 * 60, wake_min), min(22 * 60, sleep_min)
    if task.preferred_time == "Workday" and day <= 4:
        return max(8 * 60, wake_min), min(18 * 60, sleep_min)
    return wake_min, sleep_min


def _is_free(day: int, start: int, end: int, events: List[Event], buffer_min: int = 0) -> bool:
    for event in events:
        if event.day_index != day:
            continue
        busy_start = event.start_min - buffer_min
        busy_end = event.end_min + buffer_min
        if max(start, busy_start) < min(end, busy_end):
            return False
    return True


def _candidate_slots(
    task: Task,
    duration: int,
    events: List[Event],
    settings: Dict,
    earliest_abs: Optional[int] = None,
    latest_abs: Optional[int] = None,
    avoid_days: Optional[set] = None,
) -> List[Tuple[float, int, int]]:
    wake_min = int(settings.get("wake_min", 360))
    sleep_min = int(settings.get("sleep_min", 1380))
    transition = int(settings.get("transition_min", 0))
    avoid_days = avoid_days or set()
    day_load = {
        day: sum(max(0, event.end_min - event.start_min) for event in events if event.day_index == day)
        for day in range(7)
    }

    candidates = []
    for day in _allowed_days(task):
        pref_start, pref_end = _preferred_window(task, day, wake_min, sleep_min)
        windows = [(pref_start, pref_end)]
        if (pref_start, pref_end) != (wake_min, sleep_min):
            windows.append((wake_min, sleep_min))

        for window_index, (window_start, window_end) in enumerate(windows):
            latest_start = window_end - duration
            if latest_start < window_start:
                continue
            for start in range(window_start, latest_start + 1, 15):
                end = start + duration
                absolute_start = day * 1440 + start
                absolute_end = day * 1440 + end
                if earliest_abs is not None and absolute_start < earliest_abs:
                    continue
                if latest_abs is not None and absolute_end > latest_abs:
                    continue

                preferred_buffer = transition if duration >= 60 or task.energy in ["High", "Physical", "Creative"] else 0
                free_with_buffer = _is_free(day, start, end, events, preferred_buffer)
                if not free_with_buffer and not _is_free(day, start, end, events, 0):
                    continue

                center = (window_start + window_end - duration) / 2
                score = float(day_load[day])
                score += abs(start - center) * 0.15
                score += day * 3
                score += window_index * 250
                if day in avoid_days:
                    score += 1000
                if not free_with_buffer:
                    score += 120
                candidates.append((score, day, start))
            if candidates:
                break
    candidates.sort(key=lambda item: item[0])
    return candidates


def _append_event(task: Task, day: int, start: int, duration: int, events: List[Event], reason: str):
    events.append(Event(
        title=task.title,
        day_index=day,
        start_min=start,
        end_min=start + duration,
        priority=task.priority,
        source_task=task.title,
        notes=task.notes,
        explanation=reason,
        category=task.category,
    ))


def _trim_surplus(task: Task, events: List[Event], expected: int):
    task_events = sorted(
        [event for event in events if event.source_task == task.title],
        key=lambda event: (_event_abs_start(event), _event_abs_end(event)),
        reverse=True,
    )
    surplus = _scheduled_minutes(task_events) - expected
    for event in task_events:
        if surplus <= 0:
            break
        duration = event.end_min - event.start_min
        if surplus >= duration:
            events.remove(event)
            surplus -= duration
        else:
            event.end_min -= surplus
            surplus = 0


def _schedule_recurring_deficit(task: Task, events: List[Event], settings: Dict, earliest_abs: Optional[int], latest_abs: Optional[int]) -> bool:
    unit = int(task.duration_min)
    task_events = [event for event in events if event.source_task == task.title]

    for event in list(task_events):
        if event.end_min - event.start_min != unit:
            events.remove(event)

    task_events = [event for event in events if event.source_task == task.title]
    if len(task_events) > int(task.sessions_per_week):
        extras = sorted(task_events, key=_event_abs_start, reverse=True)[: len(task_events) - int(task.sessions_per_week)]
        for event in extras:
            events.remove(event)

    existing_days = {event.day_index for event in events if event.source_task == task.title}
    missing = int(task.sessions_per_week) - len([event for event in events if event.source_task == task.title])
    for _ in range(max(0, missing)):
        slots = _candidate_slots(task, unit, events, settings, earliest_abs, latest_abs, existing_days)
        if not slots:
            return False
        _, day, start = slots[0]
        _append_event(task, day, start, unit, events, "Added by final constraint completion to satisfy the required recurring session count.")
        existing_days.add(day)
    return True


def _schedule_total_deficit(task: Task, events: List[Event], settings: Dict, earliest_abs: Optional[int], latest_abs: Optional[int]) -> bool:
    expected = _expected_minutes(task)
    _trim_surplus(task, events, expected)
    current = _scheduled_minutes([event for event in events if event.source_task == task.title])
    deficit = expected - current
    if deficit <= 0:
        return True

    if not task.splittable and deficit > int(task.max_block_min):
        block_sizes = [deficit]
    else:
        block_sizes = []
        remaining = deficit
        max_block = max(15, int(task.max_block_min or deficit))
        min_block = max(15, int(task.min_block_min or 15))
        while remaining > 0:
            block = min(max_block, remaining)
            if 0 < remaining - block < min_block:
                block = remaining
            block_sizes.append(block)
            remaining -= block

    for block in block_sizes:
        slots = _candidate_slots(task, block, events, settings, earliest_abs, latest_abs)
        if not slots:
            return False
        _, day, start = slots[0]
        _append_event(task, day, start, block, events, "Added by final constraint completion to satisfy the user's requested total duration.")
    return True


def complete_schedule_constraints(
    tasks: List[Task],
    events: List[Event],
    unscheduled: List[UnscheduledTask],
    anchors: List[Task],
    settings: Dict,
) -> Tuple[List[Task], List[Event], List[UnscheduledTask]]:
    """Deterministically complete deficits left after AI repair passes.

    This layer does not redesign the AI plan. It only enforces remaining hard
    constraints: exact durations, recurring counts, and dependency order.
    """
    tasks = list(tasks)
    events = list(events)
    unscheduled = list(unscheduled)

    matched_tasks: List[Task] = []
    for anchor in anchors or []:
        match, score = _best_task_match(anchor, tasks)
        if not match or score < 0.25:
            continue
        match.duration_min = anchor.duration_min
        match.task_type = anchor.task_type
        match.sessions_per_week = anchor.sessions_per_week
        match.splittable = anchor.splittable
        match.min_block_min = anchor.min_block_min
        match.max_block_min = anchor.max_block_min
        if anchor.fixed_day:
            match.fixed_day = anchor.fixed_day
        if anchor.fixed_start:
            match.fixed_start = anchor.fixed_start
        if anchor.required_day:
            match.required_day = anchor.required_day
        if anchor.earliest_day:
            match.earliest_day = anchor.earliest_day
        if anchor.deadline_day:
            match.deadline_day = anchor.deadline_day
        if anchor.deadline_time:
            match.deadline_time = anchor.deadline_time
        if anchor.preferred_time != "Any":
            match.preferred_time = anchor.preferred_time
        if anchor.depends_on:
            match.depends_on = anchor.depends_on
        matched_tasks.append(match)

    for task in _topological_order(matched_tasks):
        dependency_title = _resolve_dependency_title(task, tasks)
        earliest_abs = None
        if dependency_title:
            dependency_events = [event for event in events if event.source_task == dependency_title]
            if dependency_events:
                dependency_end = max(_event_abs_end(event) for event in dependency_events)
                earliest_abs = dependency_end + int(settings.get("transition_min", 0))
                for event in list(events):
                    if event.source_task == task.title and _event_abs_start(event) < earliest_abs:
                        events.remove(event)

        latest_abs = None
        if task.deadline_day:
            deadline_day = DAY_TO_INDEX.get(task.deadline_day.lower())
            if deadline_day is not None:
                deadline_time = hhmm_to_minutes(task.deadline_time) if task.deadline_time else 24 * 60
                latest_abs = deadline_day * 1440 + int(deadline_time or 24 * 60)

        if task.task_type == "Fixed":
            success = True
        elif task.task_type == "Recurring":
            success = _schedule_recurring_deficit(task, events, settings, earliest_abs, latest_abs)
        else:
            success = _schedule_total_deficit(task, events, settings, earliest_abs, latest_abs)

        unscheduled = [item for item in unscheduled if item.title != task.title]
        if not success:
            remaining = _expected_minutes(task) - _scheduled_minutes(
                [event for event in events if event.source_task == task.title]
            )
            unscheduled.append(UnscheduledTask(
                title=task.title,
                reason=f"Could not place the remaining {max(0, remaining)} minutes without breaking fixed events, routine windows, or dependencies.",
                task_type=task.task_type,
                priority=task.priority,
                duration_min=max(0, remaining),
                notes=task.notes,
                category=task.category,
            ))

    events.sort(key=lambda event: (event.day_index, event.start_min, event.end_min, event.title))
    return tasks, events, unscheduled
