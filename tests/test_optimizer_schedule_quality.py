from datetime import date, datetime, timedelta

from domain import PlanRequest, PlanningTask
from models import Task
from optimizer import OptimizerConfig, WeeklyOptimizer
from optimizer.app_bridge import optimize_legacy_week


WEEK_START_DT = datetime(2026, 6, 22)
WEEK_END_DT = WEEK_START_DT + timedelta(days=7)
WEEK_START = date(2026, 6, 22)


def optimizer():
    return WeeklyOptimizer(
        OptimizerConfig(max_solve_seconds=8.0, num_search_workers=1, random_seed=1)
    )


def test_daily_flexible_workload_respects_hard_maximum():
    tasks = tuple(
        PlanningTask(
            id=f"task-{index}",
            title=f"Task {index}",
            total_duration_min=240,
            min_block_min=240,
            max_block_min=240,
            splittable=False,
        )
        for index in range(4)
    )
    request = PlanRequest(
        horizon_start=WEEK_START_DT,
        horizon_end=WEEK_END_DT,
        tasks=tasks,
        slot_minutes=15,
        wake_min=6 * 60,
        sleep_min=23 * 60,
        preferred_daily_flexible_min=360,
        max_daily_flexible_min=480,
    )

    result = optimizer().solve(request)

    assert result.is_success
    daily_load = result.diagnostics["daily_flexible_load_minutes"]
    assert max(daily_load) <= 480
    assert sum(daily_load) == 16 * 60


def test_multi_session_deliverables_prefer_different_days():
    task = PlanningTask(
        id="studies",
        title="Watercolor studies",
        total_duration_min=180,
        sessions_required=3,
        prefer_distinct_session_days=True,
        min_block_min=60,
        max_block_min=60,
        splittable=True,
        location="studio",
    )
    request = PlanRequest(
        horizon_start=WEEK_START_DT,
        horizon_end=WEEK_END_DT,
        tasks=(task,),
        slot_minutes=15,
        wake_min=6 * 60,
        sleep_min=23 * 60,
    )

    result = optimizer().solve(request)

    assert result.is_success
    events = [event for event in result.events if event.task_id == "studies"]
    assert len(events) == 3
    assert len({event.start.date() for event in events}) == 3


def test_location_change_reserves_travel_time():
    store = PlanningTask(
        id="store",
        title="Visit art supply store",
        total_duration_min=60,
        required_weekdays=(0,),
        min_block_min=60,
        max_block_min=60,
        splittable=False,
        location="store",
    )
    painting = PlanningTask(
        id="painting",
        title="Continue painting",
        total_duration_min=120,
        required_weekdays=(0,),
        dependencies=("store",),
        min_block_min=120,
        max_block_min=120,
        splittable=False,
        location="studio",
    )
    request = PlanRequest(
        horizon_start=WEEK_START_DT,
        horizon_end=WEEK_END_DT,
        tasks=(store, painting),
        slot_minutes=5,
        wake_min=6 * 60,
        sleep_min=23 * 60,
        default_travel_min=20,
        travel_time_overrides=(("store", "studio", 20),),
    )

    result = optimizer().solve(request)

    assert result.is_success
    by_task = {event.task_id: event for event in result.events}
    gap = int((by_task["painting"].start - by_task["store"].end).total_seconds() // 60)
    assert gap >= 20


def test_morning_chain_is_compact_when_breakfast_is_enabled():
    settings = {
        "wake_min": 6 * 60,
        "sleep_min": 23 * 60,
        "timezone": "Europe/Berlin",
        "protect_weekend": False,
        "transition_min": 15,
        "preferred_daily_flexible_min": 8 * 60,
        "max_daily_flexible_min": 10 * 60,
        "default_travel_min": 20,
        "compact_gap_min": 30,
        "morning_ramp_enabled": True,
        "morning_ramp_min": 60,
        "breakfast_enabled": True,
        "breakfast_window_start": "07:00",
        "breakfast_window_end": "10:00",
        "breakfast_preferred_time": "08:00",
        "breakfast_duration_min": 30,
        "lunch_enabled": False,
        "dinner_enabled": False,
        "wind_down_enabled": False,
    }
    tasks = [
        Task(
            title="Daily morning sketching practice",
            duration_min=45,
            task_type="Recurring",
            sessions_per_week=7,
            preferred_time="Morning",
            energy="Creative",
            splittable=False,
            min_block_min=45,
            max_block_min=45,
            category="Learning",
        )
    ]

    _, events, unscheduled, issues, _ = optimize_legacy_week(tasks, WEEK_START, settings)

    assert not issues
    assert not unscheduled
    for day in range(7):
        routine = next(event for event in events if event.title == "Morning routine" and event.day_index == day)
        breakfast = next(event for event in events if event.title == "Breakfast" and event.day_index == day)
        sketch = next(event for event in events if event.title == "Daily morning sketching practice" and event.day_index == day)
        assert 0 <= breakfast.start_min - routine.end_min <= 30
        assert 0 <= sketch.start_min - breakfast.end_min <= 30
