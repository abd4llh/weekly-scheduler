from datetime import date

from models import Task
from optimizer.app_bridge import optimize_legacy_week


def base_settings():
    return {
        "wake_min": 6 * 60,
        "sleep_min": 23 * 60,
        "timezone": "Europe/Berlin",
        "protect_weekend": False,
        "transition_min": 15,
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


def test_bridge_returns_legacy_events_and_optimizer_metadata():
    tasks = [
        Task(
            title="Write report",
            duration_min=120,
            priority="High",
            task_type="Flexible",
            splittable=False,
            min_block_min=120,
            max_block_min=120,
            preferred_time="Workday",
            category="Work",
        )
    ]

    normalized, events, unscheduled, issues, metadata = optimize_legacy_week(
        tasks,
        date(2026, 6, 15),
        base_settings(),
    )

    assert not issues
    assert not unscheduled
    assert any(event.title == "Write report" for event in events)
    assert metadata["status"] in {"optimal", "feasible"}
    assert metadata["engine"] == "OR-Tools CP-SAT optimizer"
    assert any(task.title == "Lunch" for task in normalized)


def test_lunch_moves_after_fixed_market_when_preferred_window_is_blocked():
    tasks = [
        Task(
            title="Attend local art market",
            duration_min=300,
            priority="High",
            task_type="Fixed",
            fixed_day="Saturday",
            fixed_start="10:00",
            splittable=False,
            min_block_min=300,
            max_block_min=300,
            category="Work",
        )
    ]

    _, events, unscheduled, issues, _ = optimize_legacy_week(
        tasks,
        date(2026, 6, 15),
        base_settings(),
    )

    assert not issues
    assert not unscheduled
    saturday_lunch = next(
        event for event in events
        if event.title == "Lunch" and event.day_index == 5
    )
    assert saturday_lunch.start_min >= 15 * 60
    assert saturday_lunch.end_min - saturday_lunch.start_min == 45
