from datetime import datetime

from models import Event, Task
from optimizer.legacy_adapter import legacy_tasks_to_plan_request


WEEK_START = datetime(2026, 6, 15)


def test_recurring_duration_is_converted_to_weekly_total():
    legacy = Task(
        title="Gym",
        duration_min=60,
        task_type="Recurring",
        sessions_per_week=3,
        preferred_time="Morning",
        min_block_min=60,
        max_block_min=60,
    )

    request = legacy_tasks_to_plan_request([legacy], WEEK_START)
    task = request.tasks[0]

    assert task.total_duration_min == 180
    assert task.sessions_required == 3
    assert task.distinct_session_days is True
    assert task.splittable is True


def test_fixed_task_and_existing_event_are_converted():
    doctor = Task(
        title="Doctor",
        duration_min=60,
        task_type="Fixed",
        fixed_day="Sunday",
        fixed_start="14:00",
        splittable=False,
    )
    busy = Event(
        title="Existing meeting",
        day_index=2,
        start_min=16 * 60,
        end_min=17 * 60,
    )

    request = legacy_tasks_to_plan_request([doctor], WEEK_START, [busy])
    task = request.tasks[0]

    assert task.fixed_start == datetime(2026, 6, 21, 14, 0)
    assert task.fixed_end == datetime(2026, 6, 21, 15, 0)
    assert request.existing_events[0].start == datetime(2026, 6, 17, 16, 0)
    assert request.existing_events[0].end == datetime(2026, 6, 17, 17, 0)


def test_routine_settings_become_explicit_daily_preference_windows():
    lunch = Task(
        title="Lunch",
        duration_min=45,
        task_type="Recurring",
        sessions_per_week=7,
        min_block_min=45,
        max_block_min=45,
        category="Routine",
    )
    settings = {
        "wake_min": 6 * 60,
        "sleep_min": 23 * 60,
        "morning_ramp_enabled": False,
        "breakfast_enabled": False,
        "lunch_enabled": True,
        "lunch_window_start": "11:00",
        "lunch_window_end": "14:00",
        "lunch_preferred_time": "13:00",
        "lunch_duration_min": 45,
        "dinner_enabled": False,
        "wind_down_enabled": False,
    }

    request = legacy_tasks_to_plan_request(
        [lunch],
        WEEK_START,
        routine_settings=settings,
    )
    task = request.tasks[0]

    assert len(task.preferred_windows) == 7
    assert {window.weekday for window in task.preferred_windows} == set(range(7))
    assert all(window.start_min == 11 * 60 for window in task.preferred_windows)
    assert all(window.end_min == 14 * 60 for window in task.preferred_windows)
