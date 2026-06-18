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
