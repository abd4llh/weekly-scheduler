from datetime import date

from ai_parser import _postprocess_task
from models import Task
from optimizer.app_bridge import optimize_legacy_week

WEEK_START = date(2026, 6, 22)


def settings(**overrides):
    values = {
        "wake_min": 360, "sleep_min": 1380, "timezone": "Europe/Berlin",
        "protect_weekend": False, "transition_min": 15,
        "morning_ramp_enabled": True, "morning_ramp_min": 60,
        "breakfast_enabled": True, "breakfast_window_start": "07:00",
        "breakfast_window_end": "10:00", "breakfast_preferred_time": "08:00",
        "breakfast_duration_min": 30, "lunch_enabled": True,
        "lunch_window_start": "11:00", "lunch_window_end": "14:00",
        "lunch_preferred_time": "13:00", "lunch_duration_min": 45,
        "dinner_enabled": True, "dinner_window_start": "18:00",
        "dinner_window_end": "21:00", "dinner_preferred_time": "19:00",
        "dinner_duration_min": 60, "wind_down_enabled": True, "wind_down_min": 30,
    }
    values.update(overrides)
    return values


def event_for(events, title, day):
    return next(event for event in events if event.title == title and event.day_index == day)


def test_daily_morning_order():
    tasks = [Task(title="Daily morning practice", duration_min=45, task_type="Recurring", sessions_per_week=7, preferred_time="Morning", cognitive_load="Medium", splittable=False, min_block_min=45, max_block_min=45, category="Learning")]
    _, events, unscheduled, issues, _ = optimize_legacy_week(tasks, WEEK_START, settings())
    assert not issues and not unscheduled
    for day in range(7):
        routine = event_for(events, "Morning routine", day)
        breakfast = event_for(events, "Breakfast", day)
        practice = event_for(events, "Daily morning practice", day)
        assert routine.end_min <= breakfast.start_min <= practice.start_min
        assert breakfast.end_min <= practice.start_min


def test_lunch_prefers_selected_time():
    _, events, unscheduled, issues, _ = optimize_legacy_week([], WEEK_START, settings(morning_ramp_enabled=False, breakfast_enabled=False, dinner_enabled=False, wind_down_enabled=False))
    assert not issues and not unscheduled
    assert event_for(events, "Lunch", 0).start_min == 780


def test_fixed_commitment_transition_before_lunch():
    tasks = [Task(title="Fixed commitment", duration_min=300, priority="High", task_type="Fixed", fixed_day="Saturday", fixed_start="10:00", location="Venue", recovery_min=15, splittable=False, min_block_min=300, max_block_min=300, category="Work")]
    _, events, unscheduled, issues, _ = optimize_legacy_week(tasks, WEEK_START, settings(morning_ramp_enabled=False, breakfast_enabled=False, dinner_enabled=False, wind_down_enabled=False))
    assert not issues and not unscheduled
    assert event_for(events, "Lunch", 5).start_min >= 915


def test_afternoon_starts_after_1300():
    tasks = [Task(title="Afternoon errand", duration_min=60, task_type="Flexible", required_day="Tuesday", preferred_time="Afternoon", location="Destination", splittable=False, min_block_min=60, max_block_min=60, category="Home")]
    _, events, unscheduled, issues, _ = optimize_legacy_week(tasks, WEEK_START, settings(morning_ramp_enabled=False, breakfast_enabled=False, lunch_enabled=False, dinner_enabled=False, wind_down_enabled=False))
    assert not issues and not unscheduled
    assert event_for(events, "Afternoon errand", 1).start_min >= 780


def test_postprocessing_preserves_explicit_session_metadata():
    task = Task(title="Three independent units", duration_min=180, task_type="Multi-session", sessions_per_week=3, session_distribution="Prefer different days", splittable=True, min_block_min=60, max_block_min=60, category="Work")
    processed = _postprocess_task(task)
    assert processed.sessions_per_week == 3
    assert processed.session_distribution == "Prefer different days"
