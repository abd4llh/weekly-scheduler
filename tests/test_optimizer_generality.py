import random
from datetime import datetime, timedelta

from ai_parser import (
    _explicit_non_time_quantity,
    _postprocess_task,
    _task_from_dict,
    validate_ai_tasks,
)
from domain import PlanRequest, PlanningTask
from models import Task
from optimizer.config import OptimizerConfig
from optimizer.generic_adapter import legacy_tasks_to_plan_request
from optimizer.generic_solver import WeeklyOptimizer

START = datetime(2026, 7, 6)
END = START + timedelta(days=7)


def optimizer():
    return WeeklyOptimizer(OptimizerConfig(max_solve_seconds=6, num_search_workers=1, random_seed=3))


def solve(tasks):
    request = PlanRequest(
        horizon_start=START,
        horizon_end=END,
        tasks=tuple(tasks),
        slot_minutes=15,
        wake_min=420,
        sleep_min=1320,
        default_travel_min=15,
    )
    return optimizer().solve(request)


def test_titles_do_not_change_metadata_driven_schedule():
    def run(names):
        tasks = [
            PlanningTask(id="a", title=names[0], total_duration_min=120, min_block_min=120, max_block_min=120, splittable=False, location="place-a", counts_as_focused_work=True),
            PlanningTask(id="b", title=names[1], total_duration_min=60, min_block_min=60, max_block_min=60, splittable=False, location="place-b"),
        ]
        result = solve(tasks)
        assert result.is_success
        return [(event.task_id, event.start, event.end) for event in result.events]
    assert run(("Task alpha", "Task beta")) == run(("Task one", "Task two"))


def test_adapter_does_not_infer_behavior_from_titles():
    tasks = [
        Task(title="Creative project", cognitive_load="Medium", location="Any"),
        Task(title="Technical project", cognitive_load="Medium", location="Any"),
        Task(title="Administrative project", cognitive_load="Medium", location="Any"),
    ]
    request = legacy_tasks_to_plan_request(tasks, START)
    assert {task.location for task in request.tasks} == {"any"}
    assert not any(task.counts_as_focused_work for task in request.tasks)


def test_arbitrary_location_and_distribution_are_preserved():
    task = _task_from_dict({
        "title": "Inspect equipment",
        "duration_min": 120,
        "task_type": "Multi-session",
        "sessions_per_week": 2,
        "location": "North warehouse",
        "session_distribution": "Prefer same day",
    })
    request = legacy_tasks_to_plan_request([task], START)
    canonical = request.tasks[0]
    assert canonical.location == "north warehouse"
    assert canonical.prefer_same_day_sessions is True


def test_explicit_non_time_count_is_validated():
    assert _explicit_non_time_quantity("two grocery trips for the kitchen") == 2
    assert _explicit_non_time_quantity("work for three hours") is None
    task = Task(
        title="Grocery trips",
        notes="Two grocery trips for the volunteer kitchen",
        task_type="Flexible",
        sessions_per_week=1,
    )
    issues = validate_ai_tasks([task])
    assert any("explicit count of 2" in issue for issue in issues)


def test_before_named_day_becomes_midnight_deadline():
    task = Task(
        title="Prepare booklet",
        notes="Complete focused blocks before Friday",
        deadline_day="Friday",
        deadline_time="23:00",
    )
    processed = _postprocess_task(task)
    assert processed.deadline_day == "Friday"
    assert processed.deadline_time == "00:00"


def test_strict_before_deadline_excludes_named_day():
    task = Task(
        title="Prepare booklet",
        duration_min=360,
        task_type="Multi-session",
        sessions_per_week=2,
        deadline_day="Friday",
        deadline_time="00:00",
        cognitive_load="High",
        splittable=True,
        min_block_min=180,
        max_block_min=180,
    )
    request = legacy_tasks_to_plan_request([task], START, slot_minutes=15)
    result = optimizer().solve(request)
    assert result.is_success
    friday_start = START + timedelta(days=4)
    assert all(event.end <= friday_start for event in result.events)


def test_unspecified_time_prefers_configured_normal_start():
    task = Task(
        title="Follow-up action",
        duration_min=30,
        task_type="Flexible",
        preferred_time="Any",
        splittable=False,
        min_block_min=30,
        max_block_min=30,
    )
    request = legacy_tasks_to_plan_request(
        [task],
        START,
        slot_minutes=15,
        wake_min=360,
        sleep_min=1380,
        default_flexible_start_min=540,
    )
    result = optimizer().solve(request)
    assert result.is_success
    event = result.events[0]
    assert event.start.hour * 60 + event.start.minute == 540


def test_random_structured_scenarios_are_exact():
    for seed in range(5):
        rng = random.Random(seed)
        tasks, expected = [], 0
        for index in range(5):
            duration = rng.choice([30, 45, 60, 90])
            expected += duration
            tasks.append(PlanningTask(
                id=f"t{index}",
                title=f"Item {index}",
                total_duration_min=duration,
                required_weekdays=(rng.randrange(7),),
                min_block_min=duration,
                max_block_min=duration,
                splittable=False,
                location=rng.choice(["home", "office", "campus", "remote"]),
                counts_as_focused_work=rng.choice([False, True]),
            ))
        result = solve(tasks)
        assert result.is_success
        actual = sum(int((event.end - event.start).total_seconds() // 60) for event in result.events)
        assert actual == expected
