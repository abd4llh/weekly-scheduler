from __future__ import annotations

from datetime import date, datetime, time
from typing import Dict, List, Sequence, Tuple

from models import Event, Task, UnscheduledTask
from routine_utils import ROUTINE_CATEGORY, normalize_routine_tasks

from .config import OptimizerConfig
from .legacy_adapter import legacy_tasks_to_plan_request
from .result import SolverStatus
from .solver import WeeklyOptimizer


DEFAULT_SLOT_MINUTES = 5
DEFAULT_DAILY_TARGET_MIN = 8 * 60
DEFAULT_DAILY_MAX_MIN = 10 * 60
DEFAULT_TRAVEL_MIN = 20
DEFAULT_COMPACT_GAP_MIN = 30


def _week_start_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return datetime.combine(value.date(), time.min, tzinfo=value.tzinfo)
    return datetime.combine(value, time.min)


def _legacy_task_lookup(tasks: Sequence[Task]) -> Dict[str, Task]:
    return {task.title.strip().lower(): task for task in tasks}


def _optimizer_event_to_legacy(event, week_start: datetime, task: Task) -> Event:
    day_index = (event.start.date() - week_start.date()).days
    start_min = event.start.hour * 60 + event.start.minute
    end_min = event.end.hour * 60 + event.end.minute

    if event.source.value == "fixed_task":
        explanation = "Fixed event preserved exactly by the optimization engine."
    elif task.category == ROUTINE_CATEGORY:
        explanation = (
            "Placed by the optimization engine using the routine's preferred time, "
            "daily sequence, and compact-gap rules while preserving fixed commitments."
        )
    else:
        explanation = (
            "Placed by the OR-Tools optimization engine to satisfy hard constraints while "
            "balancing workload, spreading sessions, and reserving transition or travel time."
        )

    return Event(
        title=event.title,
        day_index=day_index,
        start_min=start_min,
        end_min=end_min,
        priority=task.priority,
        source_task=task.title,
        notes=task.notes,
        explanation=explanation,
        category=task.category,
    )


def optimize_legacy_week(
    tasks: Sequence[Task],
    week_start: date | datetime,
    settings: Dict,
) -> Tuple[List[Task], List[Event], List[UnscheduledTask], List[Dict], Dict]:
    """Run the v0.12 optimizer and convert its result back to the current UI model."""

    normalized_tasks = normalize_routine_tasks(list(tasks), settings)
    start_dt = _week_start_datetime(week_start)
    request = legacy_tasks_to_plan_request(
        normalized_tasks,
        start_dt,
        wake_min=int(settings.get("wake_min", 360)),
        sleep_min=int(settings.get("sleep_min", 1380)),
        slot_minutes=DEFAULT_SLOT_MINUTES,
        protect_weekend=bool(settings.get("protect_weekend", False)),
        transition_min=int(settings.get("transition_min", 0)),
        preferred_daily_flexible_min=int(
            settings.get("preferred_daily_flexible_min", DEFAULT_DAILY_TARGET_MIN)
        ),
        max_daily_flexible_min=int(
            settings.get("max_daily_flexible_min", DEFAULT_DAILY_MAX_MIN)
        ),
        default_travel_min=int(settings.get("default_travel_min", DEFAULT_TRAVEL_MIN)),
        compact_gap_min=int(settings.get("compact_gap_min", DEFAULT_COMPACT_GAP_MIN)),
        timezone=str(settings.get("timezone", "Europe/Berlin")),
        routine_settings=settings,
    )

    optimizer = WeeklyOptimizer(
        OptimizerConfig(
            max_solve_seconds=20.0,
            num_search_workers=8,
            random_seed=7,
            log_search_progress=False,
        )
    )
    result = optimizer.solve(request)

    canonical_by_id = {task.id: task for task in request.tasks}
    legacy_by_title = _legacy_task_lookup(normalized_tasks)
    events: List[Event] = []
    unscheduled: List[UnscheduledTask] = []
    issues: List[Dict] = []

    if result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        for optimized_event in result.events:
            canonical_task = canonical_by_id[optimized_event.task_id]
            legacy_task = legacy_by_title[canonical_task.title.strip().lower()]
            events.append(
                _optimizer_event_to_legacy(
                    optimized_event,
                    start_dt,
                    legacy_task,
                )
            )
    else:
        reason = str(
            result.diagnostics.get("reason")
            or result.diagnostics.get("status_name")
            or "The optimizer could not find a feasible schedule."
        )
        issues.append({
            "level": "error",
            "task": "Optimization model",
            "message": reason,
        })
        for task_id in result.unscheduled_task_ids:
            canonical_task = canonical_by_id.get(task_id)
            if canonical_task is None:
                continue
            legacy_task = legacy_by_title.get(canonical_task.title.strip().lower())
            unscheduled.append(UnscheduledTask(
                title=canonical_task.title,
                reason=reason,
                task_type=legacy_task.task_type if legacy_task else "",
                priority=legacy_task.priority if legacy_task else "",
                duration_min=canonical_task.total_duration_min,
                notes=legacy_task.notes if legacy_task else "",
                category=legacy_task.category if legacy_task else "Other",
            ))

    events.sort(key=lambda event: (event.day_index, event.start_min, event.end_min, event.title))
    metadata = {
        "engine": "OR-Tools CP-SAT optimizer",
        "status": result.status.value,
        "objective_score": result.objective_score,
        "solve_seconds": round(float(result.wall_time_seconds), 3),
        "slot_minutes": DEFAULT_SLOT_MINUTES,
        "session_count": result.diagnostics.get("session_count", len(result.events)),
        "num_conflicts": result.diagnostics.get("num_conflicts"),
        "num_branches": result.diagnostics.get("num_branches"),
        "daily_flexible_load_minutes": result.diagnostics.get("daily_flexible_load_minutes", []),
        "preferred_daily_flexible_min": request.preferred_daily_flexible_min,
        "max_daily_flexible_min": request.max_daily_flexible_min,
        "default_travel_min": request.default_travel_min,
        "compact_gap_min": request.compact_gap_min,
    }
    return normalized_tasks, events, unscheduled, issues, metadata
