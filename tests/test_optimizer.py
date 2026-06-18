from datetime import datetime, timedelta

from domain import CalendarEvent, EventSource, PlanRequest, PlanningTask, TimeWindow
from optimizer import OptimizerConfig, SolverStatus, WeeklyOptimizer


WEEK_START = datetime(2026, 6, 15, 0, 0)
WEEK_END = WEEK_START + timedelta(days=7)


def make_request(tasks, existing_events=(), **kwargs):
    return PlanRequest(
        horizon_start=WEEK_START,
        horizon_end=WEEK_END,
        tasks=tuple(tasks),
        existing_events=tuple(existing_events),
        slot_minutes=15,
        wake_min=6 * 60,
        sleep_min=23 * 60,
        **kwargs,
    )


def optimizer():
    return WeeklyOptimizer(
        OptimizerConfig(max_solve_seconds=5.0, num_search_workers=1, random_seed=1)
    )


def test_preserves_fixed_meeting_and_avoids_overlap():
    meeting = CalendarEvent(
        id="meeting",
        title="Client meeting",
        start=WEEK_START + timedelta(hours=10),
        end=WEEK_START + timedelta(hours=11),
        source=EventSource.USER,
    )
    task = PlanningTask(
        id="paint",
        title="Painting",
        total_duration_min=120,
        required_weekdays=(0,),
        preferred_windows=(TimeWindow(9 * 60, 13 * 60, weekday=0),),
        min_block_min=120,
        max_block_min=120,
        splittable=False,
    )

    result = optimizer().solve(make_request([task], [meeting]))

    assert result.is_success
    event = result.events[0]
    assert event.end <= meeting.start or event.start >= meeting.end
    assert int((event.end - event.start).total_seconds() // 60) == 120


def test_dependency_is_respected():
    first = PlanningTask(
        id="draft",
        title="Draft",
        total_duration_min=60,
        required_weekdays=(0,),
        min_block_min=60,
        max_block_min=60,
        splittable=False,
    )
    second = PlanningTask(
        id="review",
        title="Review",
        total_duration_min=60,
        required_weekdays=(0,),
        dependencies=("draft",),
        min_block_min=60,
        max_block_min=60,
        splittable=False,
    )

    result = optimizer().solve(make_request([first, second]))

    assert result.is_success
    by_task = {event.task_id: event for event in result.events}
    assert by_task["draft"].end <= by_task["review"].start


def test_recurring_session_count_duration_and_distinct_days_are_exact():
    task = PlanningTask(
        id="gym",
        title="Gym",
        total_duration_min=180,
        sessions_required=3,
        distinct_session_days=True,
        min_block_min=60,
        max_block_min=60,
        preferred_windows=(TimeWindow(6 * 60, 10 * 60),),
    )

    result = optimizer().solve(make_request([task]))

    assert result.is_success
    events = [event for event in result.events if event.task_id == "gym"]
    assert len(events) == 3
    assert sum(int((event.end - event.start).total_seconds() // 60) for event in events) == 180
    assert len({event.start.date() for event in events}) == 3


def test_preferred_window_is_used_when_available():
    task = PlanningTask(
        id="study",
        title="Study",
        total_duration_min=60,
        required_weekdays=(0,),
        preferred_windows=(TimeWindow(9 * 60, 12 * 60, weekday=0),),
        min_block_min=60,
        max_block_min=60,
        splittable=False,
    )

    result = optimizer().solve(make_request([task]))

    assert result.is_success
    event = result.events[0]
    minute = event.start.hour * 60 + event.start.minute
    assert 9 * 60 <= minute
    assert minute + 60 <= 12 * 60


def test_fixed_task_is_returned_unchanged():
    task = PlanningTask(
        id="doctor",
        title="Doctor",
        total_duration_min=60,
        fixed_start=WEEK_START + timedelta(days=6, hours=14),
        fixed_end=WEEK_START + timedelta(days=6, hours=15),
        splittable=False,
    )

    result = optimizer().solve(make_request([task]))

    assert result.is_success
    assert result.events[0].start == task.fixed_start
    assert result.events[0].end == task.fixed_end
    assert result.events[0].locked is True


def test_returns_infeasible_when_no_legal_slot_exists():
    task = PlanningTask(
        id="impossible",
        title="Impossible task",
        total_duration_min=120,
        required_weekdays=(0,),
        min_block_min=120,
        max_block_min=120,
        splittable=False,
    )
    request = PlanRequest(
        horizon_start=WEEK_START,
        horizon_end=WEEK_END,
        tasks=(task,),
        slot_minutes=15,
        wake_min=9 * 60,
        sleep_min=10 * 60,
    )

    result = optimizer().solve(request)

    assert result.status == SolverStatus.INFEASIBLE
    assert "impossible" in result.unscheduled_task_ids
