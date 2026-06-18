import random
from datetime import datetime, timedelta

from ai_parser import _task_from_dict
from domain import PlanRequest, PlanningTask
from models import Task
from optimizer.config import OptimizerConfig
from optimizer.generic_adapter import legacy_tasks_to_plan_request
from optimizer.generic_solver import WeeklyOptimizer

START = datetime(2026, 7, 6)
END = START + timedelta(days=7)


def solve(tasks):
    request = PlanRequest(horizon_start=START, horizon_end=END, tasks=tuple(tasks), slot_minutes=15, wake_min=420, sleep_min=1320, default_travel_min=15)
    return WeeklyOptimizer(OptimizerConfig(max_solve_seconds=6, num_search_workers=1, random_seed=3)).solve(request)


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
    task = _task_from_dict({"title":"Inspect equipment","duration_min":120,"task_type":"Multi-session","sessions_per_week":2,"location":"North warehouse","session_distribution":"Prefer same day"})
    request = legacy_tasks_to_plan_request([task], START)
    canonical = request.tasks[0]
    assert canonical.location == "north warehouse"
    assert canonical.prefer_same_day_sessions is True


def test_random_structured_scenarios_are_exact():
    for seed in range(5):
        rng = random.Random(seed)
        tasks, expected = [], 0
        for index in range(5):
            duration = rng.choice([30, 45, 60, 90])
            expected += duration
            tasks.append(PlanningTask(id=f"t{index}", title=f"Item {index}", total_duration_min=duration, required_weekdays=(rng.randrange(7),), min_block_min=duration, max_block_min=duration, splittable=False, location=rng.choice(["home","office","campus","remote"]), counts_as_focused_work=rng.choice([False, True])))
        result = solve(tasks)
        assert result.is_success
        actual = sum(int((event.end-event.start).total_seconds()//60) for event in result.events)
        assert actual == expected
