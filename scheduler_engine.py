from models import DAY_NAMES, PRIORITY_SCORE, ENERGY_SCORE, Event, UnscheduledTask
from parser_utils import hhmm_to_minutes, minutes_to_hhmm


class Scheduler:
    def __init__(self, wake_min=360, sleep_min=1380, slot_min=15, protect_weekend=True, planning_mode="Balanced week"):
        self.wake_min = wake_min
        self.sleep_min = sleep_min
        self.slot_min = slot_min
        self.protect_weekend = protect_weekend
        self.planning_mode = planning_mode
        self.events = []
        self.unscheduled = []
        self.busy = {day: [] for day in range(7)}
        self.flex_load = {day: 0 for day in range(7)}
        self.high_load = {day: 0 for day in range(7)}
        self.daily_flex_cap = {0: 420, 1: 420, 2: 420, 3: 420, 4: 360, 5: 240, 6: 180}
        self.daily_high_cap = {0: 360, 1: 360, 2: 360, 3: 360, 4: 300, 5: 60, 6: 0}
        self.apply_planning_mode()

    def apply_planning_mode(self):
        if self.planning_mode == "Work-heavy week":
            self.daily_flex_cap.update({0: 480, 1: 480, 2: 480, 3: 480, 4: 420, 5: 300, 6: 180})
            self.daily_high_cap.update({0: 420, 1: 420, 2: 420, 3: 420, 4: 360, 5: 120, 6: 0})
        elif self.planning_mode == "Recovery week":
            self.daily_flex_cap.update({0: 300, 1: 300, 2: 300, 3: 300, 4: 240, 5: 120, 6: 60})
            self.daily_high_cap.update({0: 180, 1: 180, 2: 180, 3: 180, 4: 120, 5: 0, 6: 0})
        elif self.planning_mode == "Deadline mode":
            self.daily_flex_cap.update({0: 480, 1: 480, 2: 480, 3: 450, 4: 360, 5: 180, 6: 60})
            self.daily_high_cap.update({0: 450, 1: 450, 2: 420, 3: 360, 4: 240, 5: 60, 6: 0})
        elif self.planning_mode == "Social weekend mode":
            self.daily_flex_cap.update({0: 420, 1: 420, 2: 420, 3: 390, 4: 300, 5: 90, 6: 60})
            self.daily_high_cap.update({0: 360, 1: 360, 2: 360, 3: 300, 4: 180, 5: 0, 6: 0})

    def add_unscheduled(self, task, reason):
        self.unscheduled.append(
            UnscheduledTask(task.title, reason, task.task_type, task.priority, int(task.duration_min), task.notes, task.category)
        )

    def conflicts(self, day, start, end):
        return [(s, e, title) for s, e, title in self.busy[day] if max(s, start) < min(e, end)]

    def is_free(self, day, start, end, allow_overlap=False):
        if start < self.wake_min or end > self.sleep_min or start >= end:
            return False
        return allow_overlap or not self.conflicts(day, start, end)

    def fits_load(self, day, duration, task):
        if task.task_type not in ["Flexible", "Multi-session"]:
            return True
        if self.flex_load[day] + duration > self.daily_flex_cap[day]:
            return False
        if task.energy == "High" and self.high_load[day] + duration > self.daily_high_cap[day]:
            return False
        return True

    def add_event(self, event, allow_overlap=False, source_task=None):
        self.events.append(event)
        if not allow_overlap:
            self.busy[event.day_index].append((event.start_min, event.end_min, event.title))
            self.busy[event.day_index].sort()
        if source_task and source_task.task_type in ["Flexible", "Multi-session"]:
            duration = event.end_min - event.start_min
            self.flex_load[event.day_index] += duration
            if source_task.energy == "High":
                self.high_load[event.day_index] += duration

    def windows(self, task):
        windows = []
        if task.preferred_time == "Morning":
            for day in range(7):
                windows.append((day, self.wake_min + 15, min(self.wake_min + 240, self.sleep_min), "morning preference"))
        elif task.preferred_time == "Workday" or task.location == "Lab":
            for day in range(5):
                windows += [(day, 540, 720, "workday morning"), (day, 780, 930, "workday afternoon"), (day, 960, 1050, "late workday")]
        elif task.preferred_time == "Afternoon":
            for day in range(7):
                windows.append((day, 780, 1020, "afternoon preference"))
        elif task.preferred_time == "Evening":
            for day in range(7):
                windows.append((day, 1080, min(1350, self.sleep_min), "evening preference"))
        elif task.preferred_time == "Weekend":
            for day in [5, 6]:
                windows += [(day, 540, 720, "weekend morning"), (day, 840, 1140, "weekend afternoon")]
        else:
            for day in range(5):
                windows += [(day, 540, 720, "default morning"), (day, 780, 1050, "default afternoon"), (day, 1080, 1200, "early evening")]
            if not self.protect_weekend or task.priority in ["Critical", "High"]:
                for day in [5, 6]:
                    windows += [(day, 540, 720, "weekend fallback"), (day, 840, 1020, "weekend fallback")]
        if task.priority == "Optional":
            windows = [(4, 1080, 1200, "optional Friday"), (5, 960, 1140, "optional weekend"), (6, 840, 1020, "optional Sunday")]
        return windows

    def score_slot(self, task, day, start, end, reason):
        score = 100 + PRIORITY_SCORE.get(task.priority, 2) * 25 + ENERGY_SCORE.get(task.energy, 2) * 5
        if "morning" in reason and task.energy in ["High", "Physical"]:
            score += 25
        if task.category in ["Work", "Lab", "Writing"] and day <= 3:
            score += 18
        if task.category == "Admin" and end - start <= 30:
            score += 14
        if task.category in ["Home", "Social", "Optional"] and day in [5, 6]:
            score += 12
        if task.priority == "Optional":
            score -= 40
        if day == 5:
            score -= 20 if task.energy == "High" else 5
        if day == 6:
            score -= 45 if task.energy == "High" else 10
        if self.planning_mode == "Social weekend mode" and day in [5, 6]:
            score -= 35
        if self.planning_mode == "Recovery week" and task.energy == "High":
            score -= 25
        if self.planning_mode == "Deadline mode" and task.priority in ["Critical", "High"]:
            score += 25
        score -= self.flex_load[day] / 20
        if task.energy == "High":
            score -= self.high_load[day] / 15
        if start >= 960 and task.energy == "High":
            score -= 18
        return score

    def find_slot(self, task, duration, preferred_days=None):
        best = None
        for day, start_window, end_window, reason in self.windows(task):
            start = start_window
            while start + duration <= end_window:
                end = start + duration
                if self.fits_load(day, duration, task) and self.is_free(day, start, end, task.can_overlap):
                    score = self.score_slot(task, day, start, end, reason)
                    if preferred_days and day in preferred_days:
                        score += 15
                    if best is None or score > best[0]:
                        explanation = f"Best-fit score {score:.0f}: placed on {DAY_NAMES[day]} {minutes_to_hhmm(start)}-{minutes_to_hhmm(end)} because it matches {reason}. Mode: {self.planning_mode}."
                        best = (score, day, start, end, explanation)
                start += self.slot_min
        if best is None:
            return None
        return best[1], best[2], best[3], best[4]

    def add_focus_guard(self):
        guards = []
        for day in range(7):
            guards.append((day, self.wake_min, min(self.wake_min + 15, self.sleep_min), "Start day intentionally"))
            guards.append((day, max(self.sleep_min - 30, self.wake_min), self.sleep_min, "End day intentionally"))
        for day, start, end, title in guards:
            if self.is_free(day, start, end):
                self.add_event(Event(title, day, start, end, "Medium", "Focus Guard", "Protected transition block.", "Focus guard block.", "Focus"))

    def schedule_fixed(self, task):
        from models import DAY_TO_INDEX
        day = DAY_TO_INDEX.get(str(task.fixed_day).lower())
        start = hhmm_to_minutes(str(task.fixed_start))
        if day is None:
            return self.add_unscheduled(task, "Fixed task has no valid day.")
        if start is None:
            return self.add_unscheduled(task, "Fixed task has no valid start time.")
        end = start + int(task.duration_min)
        if not self.is_free(day, start, end, task.can_overlap):
            conflict = "; ".join(f"{title} ({minutes_to_hhmm(s)}-{minutes_to_hhmm(e)})" for s, e, title in self.conflicts(day, start, end))
            return self.add_unscheduled(task, f"Fixed-event conflict with existing event(s): {conflict}.")
        self.add_event(Event(task.title, day, start, end, task.priority, task.title, task.notes, f"Scheduled as a fixed event on {DAY_NAMES[day]} at {minutes_to_hhmm(start)}.", task.category), task.can_overlap)

    def recurring_days(self, task):
        sessions = max(1, min(int(task.sessions_per_week), 7))
        if sessions == 7:
            return list(range(7))
        if task.category == "Health" or task.energy == "Physical":
            patterns = {1: [1], 2: [1, 4], 3: [1, 3, 5], 4: [0, 2, 4, 5], 5: [0, 1, 2, 3, 4], 6: [0, 1, 2, 3, 4, 5]}
        else:
            patterns = {1: [0], 2: [1, 4], 3: [0, 2, 4], 4: [0, 1, 2, 3], 5: [0, 1, 2, 3, 4], 6: [0, 1, 2, 3, 4, 5]}
        return patterns.get(sessions, list(range(sessions)))

    def routine_targets(self, task, day):
        duration = int(task.duration_min)
        if task.category == "Health" or task.energy == "Physical":
            start = self.wake_min + 30
            return [(start, start + duration)]
        if task.category == "Relationship":
            end = min(self.sleep_min - 30, 1350)
            start = max(self.wake_min, end - duration)
            return [(start, end)]
        if task.category == "Home" and task.preferred_time == "Evening":
            start = 1080
            return [(start, start + duration)]
        if task.preferred_time == "Morning":
            start = self.wake_min + 15
            return [(start, start + duration)]
        if task.preferred_time == "Evening":
            end = min(self.sleep_min - 30, 1350)
            start = max(self.wake_min, end - duration)
            return [(start, end)]
        return []

    def schedule_recurring(self, task):
        days = self.recurring_days(task)
        count = 0
        for day in days:
            placed = False
            for start, end in self.routine_targets(task, day):
                if self.is_free(day, start, end, task.can_overlap):
                    explanation = "Placed by recurring routine rule."
                    if task.can_overlap:
                        explanation = "Placed as recurring overlapping time because overlap was allowed."
                    self.add_event(Event(task.title, day, start, end, task.priority, task.title, task.notes, explanation, task.category), task.can_overlap)
                    placed = True
                    count += 1
                    break
            if not placed:
                slot = self.find_slot(task, int(task.duration_min), [day])
                if slot:
                    self.add_event(Event(task.title, slot[0], slot[1], slot[2], task.priority, task.title, task.notes, slot[3], task.category), task.can_overlap)
                    count += 1
        if count < len(days):
            self.add_unscheduled(task, f"Only scheduled {count}/{len(days)} recurring sessions because compatible routine slots were full.")

    def schedule_flex(self, task):
        if task.priority == "Optional" and any(item.priority in ["Critical", "High"] for item in self.unscheduled):
            return self.add_unscheduled(task, "Skipped optional task because high-priority work is already unscheduled.")
        total = int(task.duration_min) * int(task.sessions_per_week) if task.task_type == "Multi-session" else int(task.duration_min)
        remaining = total
        scheduled = 0
        while remaining > 0:
            max_block = min(int(task.max_block_min), remaining)
            min_block = min(int(task.min_block_min), max_block)
            placed = False
            for block in range(max_block, min_block - 1, -15):
                slot = self.find_slot(task, block)
                if slot:
                    self.add_event(Event(task.title, slot[0], slot[1], slot[2], task.priority, task.title, task.notes, slot[3], task.category), task.can_overlap, task)
                    remaining -= block
                    scheduled += block
                    placed = True
                    break
            if not placed:
                break
        if remaining > 0:
            reason = "high-energy capacity and deep-work windows were already used" if task.energy == "High" else "realistic daily capacity limits were reached"
            self.add_unscheduled(task, f"Scheduled {scheduled}/{total} minutes; {remaining} minutes did not fit because {reason}.")

    def schedule(self, tasks, include_focus_guard=False):
        if include_focus_guard:
            self.add_focus_guard()
        for task in tasks:
            if task.task_type == "Fixed":
                self.schedule_fixed(task)
        for task in tasks:
            if task.task_type == "Recurring":
                self.schedule_recurring(task)
        rest = [task for task in tasks if task.task_type in ["Flexible", "Multi-session"]]
        rest.sort(key=lambda task: (PRIORITY_SCORE.get(task.priority, 2), ENERGY_SCORE.get(task.energy, 2), task.duration_min), reverse=True)
        for task in rest:
            self.schedule_flex(task)
        self.events.sort(key=lambda event: (event.day_index, event.start_min, event.end_min))
        return self.events, self.unscheduled
