import unittest

from ai_parser import _apply_scoped_deadlines
from constraint_completion import complete_schedule_constraints
from models import Event, Task


SETTINGS = {
    "wake_min": 420,
    "sleep_min": 1380,
    "transition_min": 15,
    "morning_ramp_enabled": False,
    "breakfast_enabled": False,
    "lunch_enabled": False,
    "dinner_enabled": False,
    "wind_down_enabled": False,
}


class DeadlineConstraintTests(unittest.TestCase):
    def test_shared_before_deadline_applies_to_coordinated_tasks(self):
        raw_text = (
            "I need nine hours of statistics revision and six hours of "
            "case-study practice before Sunday. I attend an online seminar "
            "Wednesday 10:00 to 12:00."
        )
        tasks = [
            Task(title="Statistics revision", notes="Spread across at least three days"),
            Task(title="Case-study practice", notes="Three mock cases can be batched"),
            Task(title="Online seminar", notes="Wednesday 10:00 to 12:00"),
        ]

        result = _apply_scoped_deadlines(raw_text, tasks)
        by_title = {task.title: task for task in result}

        self.assertEqual(
            (by_title["Statistics revision"].deadline_day, by_title["Statistics revision"].deadline_time),
            ("Sunday", "00:00"),
        )
        self.assertEqual(
            (by_title["Case-study practice"].deadline_day, by_title["Case-study practice"].deadline_time),
            ("Sunday", "00:00"),
        )
        self.assertEqual(by_title["Online seminar"].deadline_day, "")

    def test_existing_sunday_blocks_are_removed_and_rescheduled(self):
        anchor = Task(
            title="Case-study practice",
            duration_min=360,
            task_type="Multi-session",
            splittable=True,
            min_block_min=120,
            max_block_min=120,
            deadline_day="Sunday",
            deadline_time="00:00",
            notes="Three mock cases can be batched if useful",
            category="Learning",
            energy="High",
        )
        planned_task = Task(**anchor.__dict__)
        events = [
            Event("Case-study practice", 3, 595, 715, source_task="Case-study practice", category="Learning"),
            Event("Case-study practice", 6, 510, 630, source_task="Case-study practice", category="Learning"),
            Event("Case-study practice", 6, 645, 765, source_task="Case-study practice", category="Learning"),
        ]

        _, repaired, unscheduled = complete_schedule_constraints(
            [planned_task], events, [], [anchor], SETTINGS
        )
        task_events = [event for event in repaired if event.source_task == "Case-study practice"]

        self.assertEqual(sum(event.end_min - event.start_min for event in task_events), 360)
        self.assertTrue(all(event.day_index < 6 for event in task_events))
        self.assertEqual(unscheduled, [])

    def test_midnight_deadline_is_not_treated_as_end_of_day(self):
        anchor = Task(
            title="Statistics revision",
            duration_min=180,
            task_type="Multi-session",
            min_block_min=180,
            max_block_min=180,
            deadline_day="Sunday",
            deadline_time="00:00",
        )
        planned_task = Task(**anchor.__dict__)
        events = [
            Event("Statistics revision", 6, 540, 720, source_task="Statistics revision")
        ]

        _, repaired, _ = complete_schedule_constraints(
            [planned_task], events, [], [anchor], SETTINGS
        )
        task_events = [event for event in repaired if event.source_task == "Statistics revision"]

        self.assertEqual(len(task_events), 1)
        self.assertLess(task_events[0].day_index, 6)


if __name__ == "__main__":
    unittest.main()
