from typing import List
from models import DAY_NAMES, PRIORITY_SCORE, ENERGY_SCORE, Task, Event, UnscheduledTask
from parser_utils import hhmm_to_minutes, minutes_to_hhmm

class Scheduler:
    def __init__(self, wake_min=360, sleep_min=1380, slot_min=15, protect_weekend=True):
        self.wake_min, self.sleep_min, self.slot_min, self.protect_weekend = wake_min, sleep_min, slot_min, protect_weekend
        self.events: List[Event] = []
        self.unscheduled: List[UnscheduledTask] = []
        self.busy = {d: [] for d in range(7)}

    def add_unscheduled(self, t: Task, reason: str):
        if not any(u.title == t.title and u.reason == reason for u in self.unscheduled):
            self.unscheduled.append(UnscheduledTask(t.title, reason, t.task_type, t.priority, int(t.duration_min), t.notes))

    def conflicts(self, d, s, e):
        return [(a,b,n) for a,b,n in self.busy[d] if max(a,s) < min(b,e)]

    def is_free(self, d, s, e, allow=False):
        if s < self.wake_min or e > self.sleep_min or s >= e: return False
        return True if allow else not self.conflicts(d,s,e)

    def add_event(self, e: Event, allow=False):
        self.events.append(e)
        if not allow:
            self.busy[e.day_index].append((e.start_min, e.end_min, e.title))
            self.busy[e.day_index].sort()

    def windows(self, t: Task):
        w=[]
        if t.preferred_time == "Morning":
            for d in range(7): w.append((d,375,570,"morning preference"))
        elif t.preferred_time == "Workday" or t.location == "Lab":
            for d in range(5): w += [(d,540,720,"workday morning"),(d,780,930,"workday afternoon"),(d,960,1050,"late follow-up")]
        elif t.preferred_time == "Afternoon":
            for d in range(7): w.append((d,780,1020,"afternoon preference"))
        elif t.preferred_time == "Evening":
            for d in range(7): w.append((d,1080,1350,"evening preference"))
        elif t.preferred_time == "Weekend":
            for d in [5,6]: w += [(d,420,720,"weekend morning"),(d,840,1140,"weekend afternoon")]
        else:
            for d in range(5): w += [(d,540,720,"default morning"),(d,780,1050,"default afternoon"),(d,1080,1200,"early evening")]
            if not self.protect_weekend or t.priority in ["Critical","High"]:
                for d in [5,6]: w += [(d,540,720,"weekend fallback"),(d,840,1020,"weekend fallback")]
        if t.priority == "Optional":
            w = [(4,1080,1200,"optional Friday"),(5,960,1140,"optional weekend"),(6,840,1020,"optional Sunday")]
        return w

    def find_slot(self, t: Task, dur: int, preferred_days=None):
        wins = self.windows(t)
        if preferred_days:
            wins = [x for x in wins if x[0] in preferred_days] + [x for x in wins if x[0] not in preferred_days]
        for d,a,b,why in wins:
            s=a
            while s + dur <= b:
                if self.is_free(d,s,s+dur,t.can_overlap):
                    return d,s,s+dur,f"Placed on {DAY_NAMES[d]} {minutes_to_hhmm(s)}–{minutes_to_hhmm(s+dur)} because it is {t.priority.lower()} priority and matches the {why} window."
                s += self.slot_min
        return None

    def add_focus_guard(self):
        guards = [(d,360,375,"Wake up / stabilize — no reels") for d in range(6)] + [(6,420,435,"Wake up / stabilize — no reels")] + [(d,1350,1380,"Shutdown — no reels") for d in range(5)]
        for d,s,e,title in guards:
            if self.is_free(d,s,e):
                self.add_event(Event(title,d,s,e,"High","Focus Guard","Protect vulnerable scrolling moments.","Focus Guard block."))

    def schedule_fixed(self, t: Task):
        from models import DAY_TO_INDEX
        d = DAY_TO_INDEX.get(str(t.fixed_day).lower())
        s = hhmm_to_minutes(str(t.fixed_start))
        if d is None: return self.add_unscheduled(t,"Fixed task has no valid day.")
        if s is None: return self.add_unscheduled(t,"Fixed task has no valid start time.")
        e = s + int(t.duration_min)
        if not self.is_free(d,s,e,t.can_overlap):
            c = "; ".join(f"{n} ({minutes_to_hhmm(a)}–{minutes_to_hhmm(b)})" for a,b,n in self.conflicts(d,s,e))
            return self.add_unscheduled(t, f"Fixed-event conflict with existing event(s): {c}.")
        self.add_event(Event(t.title,d,s,e,t.priority,t.title,t.notes,f"Scheduled as a fixed event on {DAY_NAMES[d]} at {minutes_to_hhmm(s)}."), t.can_overlap)

    def schedule_recurring(self, t: Task):
        title=t.title.lower(); count=0
        if "gym" in title: days=[1,3,5]
        elif "german" in title: days=list(range(7))
        elif "cooking" in title: days=list(range(7))
        elif "israa" in title or "wife" in title or "talking" in title:
            for d in range(7):
                self.add_event(Event(t.title,d,1200,1350,t.priority,t.title,t.notes,"Protected relationship time; overlap allowed."), True)
            return
        else:
            days = list(range(7))[:t.sessions_per_week] if t.sessions_per_week not in [2,3,5] else ({2:[1,4],3:[0,2,4],5:list(range(5))}[t.sessions_per_week])
        for d in days[:t.sessions_per_week]:
            if "gym" in title: targets=[(390,510)]
            elif "german" in title: targets=[(1050,1080)] if d in [1,3,5] else [(375,405)]
            elif "cooking" in title: targets=[(1080,1200)]
            else: targets=[]
            placed=False
            for s,e in targets:
                if self.is_free(d,s,e,t.can_overlap):
                    self.add_event(Event(t.title,d,s,e,t.priority,t.title,t.notes,"Placed by recurring routine rule."), t.can_overlap)
                    placed=True; count += 1; break
            if not placed:
                slot = self.find_slot(t, int(t.duration_min), [d])
                if slot:
                    self.add_event(Event(t.title,*slot[:3],t.priority,t.title,t.notes,slot[3]), t.can_overlap)
                    count += 1
        if count < min(t.sessions_per_week, len(days)):
            self.add_unscheduled(t, f"Only scheduled {count}/{t.sessions_per_week} recurring sessions.")

    def schedule_flex(self, t: Task):
        total = int(t.duration_min) * int(t.sessions_per_week) if ("cabinet" in t.title.lower() and t.task_type == "Multi-session") else int(t.duration_min)
        remaining, scheduled = total, 0
        while remaining > 0:
            max_block = min(int(t.max_block_min), remaining)
            min_block = min(int(t.min_block_min), max_block)
            placed=False
            for block in range(max_block, min_block-1, -15):
                slot = self.find_slot(t, block)
                if slot:
                    self.add_event(Event(t.title,*slot[:3],t.priority,t.title,t.notes,slot[3]), t.can_overlap)
                    remaining -= block; scheduled += block; placed=True; break
            if not placed: break
        if remaining > 0:
            self.add_unscheduled(t, f"Scheduled {scheduled}/{total} minutes; {remaining} minutes did not fit.")

    def schedule(self, tasks: List[Task], include_focus_guard=False):
        if include_focus_guard: self.add_focus_guard()
        for t in tasks:
            if t.task_type == "Fixed": self.schedule_fixed(t)
        for t in tasks:
            if t.task_type == "Recurring": self.schedule_recurring(t)
        rest=[t for t in tasks if t.task_type in ["Flexible","Multi-session"]]
        rest.sort(key=lambda t:(PRIORITY_SCORE.get(t.priority,2), ENERGY_SCORE.get(t.energy,2), t.duration_min), reverse=True)
        for t in rest: self.schedule_flex(t)
        self.events.sort(key=lambda e:(e.day_index,e.start_min,e.end_min))
        return self.events, self.unscheduled
