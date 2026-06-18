from datetime import date, datetime, timedelta

from domain import PlanRequest, PlanningTask
from models import Task
from optimizer import OptimizerConfig, WeeklyOptimizer
from optimizer.app_bridge import optimize_legacy_week

WEEK_START_DT = datetime(2026, 6, 22)
WEEK_END_DT = WEEK_START_DT + timedelta(days=7)
WEEK_START = date(2026, 6, 22)


def optimizer():
    return WeeklyOptimizer(OptimizerConfig(max_solve_seconds=10.0, num_search_workers=1, random_seed=1))


def routine_settings():
    return {"wake_min":360,"sleep_min":1380,"timezone":"Europe/Berlin","protect_weekend":False,"transition_min":15,"preferred_daily_flexible_min":480,"max_daily_flexible_min":600,"preferred_daily_total_min":600,"preferred_daily_focus_min":240,"late_focus_start_min":1140,"default_travel_min":20,"compact_gap_min":30,"morning_ramp_enabled":False,"breakfast_enabled":False,"lunch_enabled":False,"dinner_enabled":True,"dinner_window_start":"18:00","dinner_window_end":"21:00","dinner_preferred_time":"19:00","dinner_duration_min":60,"wind_down_enabled":False}


def test_meal_never_moves_before_its_earliest_time():
    tasks = [Task(title="Evening commitment", duration_min=180, task_type="Fixed", fixed_day="Tuesday", fixed_start="18:00", location="Community venue", recovery_min=20, splittable=False, min_block_min=180, max_block_min=180, category="Social")]
    _, events, unscheduled, issues, _ = optimize_legacy_week(tasks, WEEK_START, routine_settings())
    assert not issues and not unscheduled
    dinners = [event for event in events if event.title == "Dinner"]
    assert all(event.start_min >= 1080 for event in dinners)
    assert next(event for event in dinners if event.day_index == 1).start_min >= 1280


def test_fixed_event_location_requires_travel_before_meal():
    fixed = PlanningTask(id="fixed", title="Fixed commitment", total_duration_min=300, fixed_start=WEEK_START_DT + timedelta(days=5, hours=10), fixed_end=WEEK_START_DT + timedelta(days=5, hours=15), min_block_min=300, max_block_min=300, splittable=False, location="site-a", transition_after_min=15, counts_toward_daily_limit=False)
    meal = PlanningTask(id="meal", title="Meal", total_duration_min=45, required_weekdays=(5,), hard_earliest_min_of_day=660, min_block_min=45, max_block_min=45, splittable=False, location="site-b", counts_toward_daily_limit=False)
    result = optimizer().solve(PlanRequest(horizon_start=WEEK_START_DT, horizon_end=WEEK_END_DT, tasks=(fixed, meal), slot_minutes=5, wake_min=360, sleep_min=1380, default_travel_min=20))
    assert result.is_success
    event = next(event for event in result.events if event.task_id == "meal")
    assert event.start.hour * 60 + event.start.minute >= 920


def test_total_daily_burden_includes_fixed_commitments_and_routines():
    fixed = PlanningTask(id="fixed", title="Fixed", total_duration_min=300, fixed_start=WEEK_START_DT + timedelta(days=5, hours=10), fixed_end=WEEK_START_DT + timedelta(days=5, hours=15), min_block_min=300, max_block_min=300, splittable=False, counts_toward_daily_limit=False)
    routine = PlanningTask(id="routine", title="Routine", total_duration_min=60, required_weekdays=(5,), hard_earliest_min_of_day=1080, min_block_min=60, max_block_min=60, splittable=False, counts_toward_daily_limit=False)
    result = optimizer().solve(PlanRequest(horizon_start=WEEK_START_DT, horizon_end=WEEK_END_DT, tasks=(fixed, routine), slot_minutes=15, wake_min=360, sleep_min=1380))
    assert result.is_success
    assert result.diagnostics["daily_total_burden_minutes"][5] == 360


def test_long_focused_project_is_distributed_across_days():
    project = PlanningTask(id="project", title="Focused project", total_duration_min=720, min_block_min=180, max_block_min=180, splittable=True, location="workplace", counts_as_focused_work=True)
    result = optimizer().solve(PlanRequest(horizon_start=WEEK_START_DT, horizon_end=WEEK_END_DT, tasks=(project,), slot_minutes=15, wake_min=360, sleep_min=1380, preferred_daily_focus_min=240, late_focus_start_min=1140))
    assert result.is_success
    loads = result.diagnostics["daily_focused_work_minutes"]
    assert sum(loads) == 720 and len([x for x in loads if x > 0]) >= 3 and max(loads) <= 360


def test_spreading_preference_uses_multiple_days():
    units = PlanningTask(id="units", title="Independent units", total_duration_min=180, sessions_required=3, prefer_distinct_session_days=True, min_block_min=60, max_block_min=60, splittable=True)
    result = optimizer().solve(PlanRequest(horizon_start=WEEK_START_DT, horizon_end=WEEK_END_DT, tasks=(units,), slot_minutes=15, wake_min=360, sleep_min=1380))
    assert result.is_success
    assert len({event.start.date() for event in result.events if event.task_id == "units"}) == 3
