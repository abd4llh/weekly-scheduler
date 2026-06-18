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
        OptimizerConfig(max_solve_seconds=10.0, num_search_workers=1, random_seed=1)
    )


def routine_settings():
    return {
        "wake_min": 6 * 60,
        "sleep_min": 23 * 60,
        "timezone": "Europe/Berlin",
        "protect_weekend": False,
        "transition_min": 15,
        "preferred_daily_flexible_min": 8 * 60,
        "max_daily_flexible_min": 10 * 60,
        "preferred_daily_total_min": 10 * 60,
        "preferred_daily_focus_min": 4 * 60,
        "late_focus_start_min": 19 * 60,
        "default_travel_min": 20,
        "compact_gap_min": 30,
        "morning_ramp_enabled": False,
        "breakfast_enabled": False,
        "lunch_enabled": False,
        "dinner_enabled": True,
        "dinner_window_start": "18:00",
        "dinner_window_end": "21:00",
        "dinner_preferred_time": "19:00",
        "dinner_duration_min": 60,
        "wind_down_enabled": False,
    }


def test_meal_never_moves_before_its_earliest_time():
    tasks = [
        Task(
            title="Client meeting",
            duration_min=180,
            task_type="Fixed",
            fixed_day="Tuesday",
            fixed_start="18:00",
            location="Any",
            splittable=False,
            min_block_min=180,
            max_block_min=180,
            category="Social",
        )
    ]

    _, events, unscheduled, issues, _ = optimize_legacy_week(
        tasks,
        WEEK_START,
        routine_settings(),
    )

    assert not issues
    assert not unscheduled
    dinners = [event for event in events if event.title == "Dinner"]
    assert len(dinners) == 7
    assert all(event.start_min >= 18 * 60 for event in dinners)
    tuesday = next(event for event in dinners if event.day_index == 1)
    assert tuesday.start_min >= 21 * 60 + 20


def test_fixed_event_location_requires_travel_before_meal():
    market = PlanningTask(
        id="market",
        title="Art market",
        total_duration_min=300,
        fixed_start=WEEK_START_DT + timedelta(days=5, hours=10),
        fixed_end=WEEK_START_DT + timedelta(days=5, hours=15),
        min_block_min=300,
        max_block_min=300,
        splittable=False,
        location="outside",
        transition_after_min=15,
        counts_toward_daily_limit=False,
    )
    lunch = PlanningTask(
        id="lunch",
        title="Lunch",
        total_duration_min=45,
        required_weekdays=(5,),
        hard_earliest_min_of_day=11 * 60,
        min_block_min=45,
        max_block_min=45,
        splittable=False,
        location="home",
        counts_toward_daily_limit=False,
    )
    request = PlanRequest(
        horizon_start=WEEK_START_DT,
        horizon_end=WEEK_END_DT,
        tasks=(market, lunch),
        slot_minutes=5,
        wake_min=6 * 60,
        sleep_min=23 * 60,
        default_travel_min=20,
        travel_time_overrides=(("outside", "home", 20),),
    )

    result = optimizer().solve(request)

    assert result.is_success
    lunch_event = next(event for event in result.events if event.task_id == "lunch")
    assert lunch_event.start.hour * 60 + lunch_event.start.minute >= 15 * 60 + 20


def test_total_daily_burden_includes_fixed_commitments_and_routines():
    fixed = PlanningTask(
        id="market",
        title="Market",
        total_duration_min=300,
        fixed_start=WEEK_START_DT + timedelta(days=5, hours=10),
        fixed_end=WEEK_START_DT + timedelta(days=5, hours=15),
        min_block_min=300,
        max_block_min=300,
        splittable=False,
        counts_toward_daily_limit=False,
        counts_toward_total_burden=True,
    )
    routine = PlanningTask(
        id="dinner",
        title="Dinner",
        total_duration_min=60,
        required_weekdays=(5,),
        hard_earliest_min_of_day=18 * 60,
        min_block_min=60,
        max_block_min=60,
        splittable=False,
        counts_toward_daily_limit=False,
        counts_toward_total_burden=True,
    )
    request = PlanRequest(
        horizon_start=WEEK_START_DT,
        horizon_end=WEEK_END_DT,
        tasks=(fixed, routine),
        slot_minutes=15,
        wake_min=6 * 60,
        sleep_min=23 * 60,
    )

    result = optimizer().solve(request)

    assert result.is_success
    saturday_burden = result.diagnostics["daily_total_burden_minutes"][5]
    assert saturday_burden == 360


def test_long_focused_project_is_distributed_across_days():
    painting = PlanningTask(
        id="painting",
        title="Commissioned painting",
        total_duration_min=720,
        min_block_min=180,
        max_block_min=180,
        splittable=True,
        location="studio",
        counts_as_focused_work=True,
    )
    request = PlanRequest(
        horizon_start=WEEK_START_DT,
        horizon_end=WEEK_END_DT,
        tasks=(painting,),
        slot_minutes=15,
        wake_min=6 * 60,
        sleep_min=23 * 60,
        preferred_daily_focus_min=4 * 60,
        late_focus_start_min=19 * 60,
    )

    result = optimizer().solve(request)

    assert result.is_success
    loads = result.diagnostics["daily_focused_work_minutes"]
    assert sum(loads) == 720
    assert len([load for load in loads if load > 0]) >= 3
    assert max(loads) <= 360


def test_strong_spreading_avoids_duplicate_deliverables_on_one_day():
    studies = PlanningTask(
        id="studies",
        title="Three studies",
        total_duration_min=180,
        sessions_required=3,
        prefer_distinct_session_days=True,
        min_block_min=60,
        max_block_min=60,
        splittable=True,
    )
    request = PlanRequest(
        horizon_start=WEEK_START_DT,
        horizon_end=WEEK_END_DT,
        tasks=(studies,),
        slot_minutes=15,
        wake_min=6 * 60,
        sleep_min=23 * 60,
    )

    result = optimizer().solve(request)

    assert result.is_success
    events = [event for event in result.events if event.task_id == "studies"]
    assert len({event.start.date() for event in events}) == 3
