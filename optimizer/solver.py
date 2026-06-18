from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Sequence, Tuple

from ortools.sat.python import cp_model

from domain import EventSource, PlanRequest, PlanningTask, ScheduledEvent, TaskStatus, TimeWindow
from optimizer.config import OptimizerConfig
from optimizer.result import OptimizationResult, SolverStatus


@dataclass
class _SessionVariable:
    task: PlanningTask
    index: int
    duration_slots: int
    start: cp_model.IntVar
    end: cp_model.IntVar
    interval: cp_model.IntervalVar
    day: cp_model.IntVar
    day_flags: Tuple[cp_model.BoolVar, ...]


class WeeklyOptimizer:
    """Single-horizon CP-SAT scheduler.

    The AI layer is expected to produce canonical ``PlanningTask`` objects.
    This class owns exact time placement, overlap prevention, dependencies and
    soft-preference scoring.
    """

    def __init__(self, config: OptimizerConfig | None = None) -> None:
        self.config = config or OptimizerConfig()

    def solve(self, request: PlanRequest) -> OptimizationResult:
        model = cp_model.CpModel()
        slot_minutes = request.slot_minutes
        horizon_minutes = int((request.horizon_end - request.horizon_start).total_seconds() // 60)
        if horizon_minutes <= 0 or horizon_minutes % slot_minutes != 0:
            raise ValueError("Planning horizon must be a positive multiple of slot_minutes.")
        horizon_slots = horizon_minutes // slot_minutes
        horizon_days = max(1, math.ceil(horizon_minutes / (24 * 60)))

        active_tasks = tuple(
            task
            for task in request.tasks
            if task.status not in {TaskStatus.COMPLETED, TaskStatus.SKIPPED, TaskStatus.CANCELLED}
        )
        task_by_id = {task.id: task for task in active_tasks}
        self._validate_dependencies(active_tasks, task_by_id)

        fixed_intervals: List[cp_model.IntervalVar] = []
        fixed_busy_ranges: List[Tuple[int, int]] = []
        fixed_task_events: List[ScheduledEvent] = []
        task_start_vars: Dict[str, cp_model.IntVar] = {}
        task_end_vars: Dict[str, cp_model.IntVar] = {}
        fixed_daily_load = [0 for _ in range(horizon_days)]

        for event in request.existing_events:
            if not event.busy:
                continue
            clipped = self._clip_to_horizon(event.start, event.end, request)
            if clipped is None:
                continue
            start_slot, end_slot = clipped
            fixed_intervals.append(
                model.NewFixedSizeIntervalVar(
                    start_slot,
                    end_slot - start_slot,
                    f"busy_{self._safe_name(event.id)}",
                )
            )
            fixed_busy_ranges.append((start_slot, end_slot))

        sessions: List[_SessionVariable] = []
        all_intervals: List[cp_model.IntervalVar] = list(fixed_intervals)
        objective_terms: List[cp_model.LinearExpr] = []

        for task in active_tasks:
            if task.locked and task.fixed_start is None:
                raise ValueError(f"Locked task '{task.id}' requires fixed_start/fixed_end.")

            if task.fixed_start is not None:
                fixed = self._fixed_task_slots(task, request)
                start_slot, end_slot = fixed
                interval = model.NewFixedSizeIntervalVar(
                    start_slot,
                    end_slot - start_slot,
                    f"fixed_task_{self._safe_name(task.id)}",
                )
                all_intervals.append(interval)
                fixed_busy_ranges.append((start_slot, end_slot))
                task_start_vars[task.id] = model.NewConstant(start_slot)
                task_end_vars[task.id] = model.NewConstant(end_slot)
                day_index = self._day_offset(request.horizon_start, task.fixed_start)
                if 0 <= day_index < horizon_days:
                    fixed_daily_load[day_index] += end_slot - start_slot
                fixed_task_events.append(
                    ScheduledEvent(
                        id=f"{task.id}:fixed",
                        task_id=task.id,
                        title=task.title,
                        start=task.fixed_start,
                        end=task.fixed_end,
                        locked=True,
                        source=EventSource.FIXED_TASK,
                    )
                )
                continue

            block_sizes = self._decompose_task(task, slot_minutes)
            task_sessions: List[_SessionVariable] = []
            for index, duration_slots in enumerate(block_sizes):
                allowed_starts = self._allowed_start_slots(
                    task,
                    duration_slots,
                    request,
                    horizon_slots,
                    fixed_busy_ranges,
                )
                if not allowed_starts:
                    return self._infeasible_result(
                        active_tasks,
                        f"Task '{task.title}' has no legal start slot in the planning horizon.",
                    )

                domain = cp_model.Domain.FromValues(allowed_starts)
                name = f"{self._safe_name(task.id)}_{index}"
                start_var = model.NewIntVarFromDomain(domain, f"start_{name}")
                end_var = model.NewIntVar(0, horizon_slots, f"end_{name}")
                model.Add(end_var == start_var + duration_slots)
                interval = model.NewFixedSizeIntervalVar(start_var, duration_slots, f"interval_{name}")
                all_intervals.append(interval)

                day_table = self._day_table(request, horizon_slots)
                day_var = model.NewIntVar(0, horizon_days - 1, f"day_{name}")
                model.AddElement(start_var, day_table, day_var)
                day_flags = []
                for day in range(horizon_days):
                    flag = model.NewBoolVar(f"is_day_{day}_{name}")
                    model.Add(day_var == day).OnlyEnforceIf(flag)
                    model.Add(day_var != day).OnlyEnforceIf(flag.Not())
                    day_flags.append(flag)

                session = _SessionVariable(
                    task=task,
                    index=index,
                    duration_slots=duration_slots,
                    start=start_var,
                    end=end_var,
                    interval=interval,
                    day=day_var,
                    day_flags=tuple(day_flags),
                )
                task_sessions.append(session)
                sessions.append(session)

                preference_table = self._preferred_penalty_table(
                    task,
                    duration_slots,
                    request,
                    horizon_slots,
                )
                pref_var = model.NewIntVar(
                    0,
                    max(preference_table) if preference_table else 0,
                    f"pref_penalty_{name}",
                )
                model.AddElement(start_var, preference_table, pref_var)
                objective_terms.append(self.config.weights.preferred_window * pref_var)

                if request.protect_weekend:
                    weekend_table = self._weekend_table(request, horizon_slots)
                    weekend_var = model.NewIntVar(0, 1, f"weekend_{name}")
                    model.AddElement(start_var, weekend_table, weekend_var)
                    objective_terms.append(self.config.weights.weekend * weekend_var)

                late_table = self._late_start_table(request, horizon_slots)
                late_var = model.NewIntVar(0, max(late_table), f"late_{name}")
                model.AddElement(start_var, late_table, late_var)
                objective_terms.append(self.config.weights.late_start * late_var)

            for previous, current in zip(task_sessions, task_sessions[1:]):
                model.Add(previous.end <= current.start)

            if len(task_sessions) == 1:
                task_start_vars[task.id] = task_sessions[0].start
                task_end_vars[task.id] = task_sessions[0].end
            else:
                task_start = model.NewIntVar(0, horizon_slots, f"task_start_{self._safe_name(task.id)}")
                task_end = model.NewIntVar(0, horizon_slots, f"task_end_{self._safe_name(task.id)}")
                model.AddMinEquality(task_start, [session.start for session in task_sessions])
                model.AddMaxEquality(task_end, [session.end for session in task_sessions])
                task_start_vars[task.id] = task_start
                task_end_vars[task.id] = task_end

        model.AddNoOverlap(all_intervals)

        for task in active_tasks:
            for predecessor_id in task.dependencies:
                model.Add(task_end_vars[predecessor_id] <= task_start_vars[task.id])

        if sessions and self.config.weights.daily_load_imbalance > 0:
            daily_load_vars = []
            max_possible_load = sum(session.duration_slots for session in sessions) + sum(fixed_daily_load)
            for day in range(horizon_days):
                load = model.NewIntVar(0, max_possible_load, f"daily_load_{day}")
                terms = [fixed_daily_load[day]]
                terms.extend(
                    session.duration_slots * session.day_flags[day]
                    for session in sessions
                )
                model.Add(load == sum(terms))
                daily_load_vars.append(load)
            max_load = model.NewIntVar(0, max_possible_load, "max_daily_load")
            min_load = model.NewIntVar(0, max_possible_load, "min_daily_load")
            model.AddMaxEquality(max_load, daily_load_vars)
            model.AddMinEquality(min_load, daily_load_vars)
            imbalance = model.NewIntVar(0, max_possible_load, "daily_load_imbalance")
            model.Add(imbalance == max_load - min_load)
            objective_terms.append(self.config.weights.daily_load_imbalance * imbalance)

        if objective_terms:
            model.Minimize(sum(objective_terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.config.max_solve_seconds
        solver.parameters.num_search_workers = self.config.num_search_workers
        solver.parameters.random_seed = self.config.random_seed
        solver.parameters.log_search_progress = self.config.log_search_progress

        raw_status = solver.Solve(model)
        status = self._map_status(raw_status)
        if status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
            return OptimizationResult(
                status=status,
                unscheduled_task_ids=tuple(task.id for task in active_tasks),
                wall_time_seconds=solver.WallTime(),
                diagnostics={
                    "status_name": solver.StatusName(raw_status),
                    "response_stats": solver.ResponseStats(),
                },
            )

        generated_events = list(fixed_task_events)
        for session in sessions:
            start_slot = solver.Value(session.start)
            end_slot = start_slot + session.duration_slots
            generated_events.append(
                ScheduledEvent(
                    id=f"{session.task.id}:{session.index}",
                    task_id=session.task.id,
                    title=session.task.title,
                    start=self._slot_to_datetime(request, start_slot),
                    end=self._slot_to_datetime(request, end_slot),
                    locked=session.task.locked,
                    source=EventSource.OPTIMIZER,
                )
            )

        generated_events.sort(key=lambda event: (event.start, event.end, event.task_id))
        return OptimizationResult(
            status=status,
            events=tuple(generated_events),
            objective_score=solver.ObjectiveValue() if objective_terms else 0.0,
            wall_time_seconds=solver.WallTime(),
            diagnostics={
                "status_name": solver.StatusName(raw_status),
                "num_conflicts": solver.NumConflicts(),
                "num_branches": solver.NumBranches(),
                "best_objective_bound": solver.BestObjectiveBound() if objective_terms else 0.0,
                "session_count": len(sessions),
            },
        )

    def _decompose_task(self, task: PlanningTask, slot_minutes: int) -> List[int]:
        if task.total_duration_min % slot_minutes != 0:
            raise ValueError(
                f"Task '{task.id}' duration must be divisible by slot_minutes ({slot_minutes})."
            )
        total_slots = task.total_duration_min // slot_minutes
        min_slots = max(1, math.ceil(task.min_block_min / slot_minutes))
        max_slots = max(min_slots, task.max_block_min // slot_minutes)

        if not task.splittable:
            return [total_slots]

        if task.sessions_required is not None:
            session_count = task.sessions_required
        else:
            minimum_sessions = max(1, math.ceil(total_slots / max_slots))
            maximum_sessions = max(1, total_slots // min_slots)
            if minimum_sessions > maximum_sessions:
                raise ValueError(
                    f"Task '{task.id}' cannot be split within its min/max block constraints."
                )
            session_count = minimum_sessions

        if session_count > total_slots:
            raise ValueError(f"Task '{task.id}' has more sessions than available time slots.")
        base, remainder = divmod(total_slots, session_count)
        sizes = [base + (1 if index < remainder else 0) for index in range(session_count)]
        if any(size < min_slots or size > max_slots for size in sizes):
            raise ValueError(
                f"Task '{task.id}' session count conflicts with its min/max block constraints."
            )
        return sizes

    def _allowed_start_slots(
        self,
        task: PlanningTask,
        duration_slots: int,
        request: PlanRequest,
        horizon_slots: int,
        fixed_busy_ranges: Sequence[Tuple[int, int]],
    ) -> List[int]:
        allowed = []
        duration_min = duration_slots * request.slot_minutes
        for start_slot in range(0, horizon_slots - duration_slots + 1):
            end_slot = start_slot + duration_slots
            start_dt = self._slot_to_datetime(request, start_slot)
            end_dt = self._slot_to_datetime(request, end_slot)
            if start_dt.date() != (end_dt - timedelta(microseconds=1)).date():
                continue
            if task.earliest_start and start_dt < task.earliest_start:
                continue
            if task.deadline and end_dt > task.deadline:
                continue
            if task.required_weekdays and start_dt.weekday() not in task.required_weekdays:
                continue
            minute_of_day = start_dt.hour * 60 + start_dt.minute
            if minute_of_day < request.wake_min:
                continue
            if minute_of_day + duration_min > request.sleep_min:
                continue
            if any(max(start_slot, busy_start) < min(end_slot, busy_end) for busy_start, busy_end in fixed_busy_ranges):
                continue
            allowed.append(start_slot)
        return allowed

    def _preferred_penalty_table(
        self,
        task: PlanningTask,
        duration_slots: int,
        request: PlanRequest,
        horizon_slots: int,
    ) -> List[int]:
        if not task.preferred_windows:
            return [0] * horizon_slots
        duration_min = duration_slots * request.slot_minutes
        table = []
        for slot in range(horizon_slots):
            start_dt = self._slot_to_datetime(request, slot)
            start_min = start_dt.hour * 60 + start_dt.minute
            end_min = start_min + duration_min
            applicable = [
                window
                for window in task.preferred_windows
                if window.weekday is None or window.weekday == start_dt.weekday()
            ]
            if not applicable:
                table.append(24 * 60 // request.slot_minutes)
                continue
            penalties = [
                self._window_distance_slots(start_min, end_min, window, request.slot_minutes)
                * max(1, window.weight)
                for window in applicable
            ]
            table.append(min(penalties))
        return table

    @staticmethod
    def _window_distance_slots(
        start_min: int,
        end_min: int,
        window: TimeWindow,
        slot_minutes: int,
    ) -> int:
        if start_min >= window.start_min and end_min <= window.end_min:
            return 0
        if end_min <= window.start_min:
            distance = window.start_min - end_min
        elif start_min >= window.end_min:
            distance = start_min - window.end_min
        else:
            distance = max(window.start_min - start_min, end_min - window.end_min, 0)
        return math.ceil(distance / slot_minutes) + 1

    def _weekend_table(self, request: PlanRequest, horizon_slots: int) -> List[int]:
        return [
            1 if self._slot_to_datetime(request, slot).weekday() >= 5 else 0
            for slot in range(horizon_slots)
        ]

    def _late_start_table(self, request: PlanRequest, horizon_slots: int) -> List[int]:
        table = []
        for slot in range(horizon_slots):
            dt = self._slot_to_datetime(request, slot)
            minute = dt.hour * 60 + dt.minute
            table.append(max(0, (minute - request.wake_min) // request.slot_minutes))
        return table

    def _day_table(self, request: PlanRequest, horizon_slots: int) -> List[int]:
        return [
            max(0, self._day_offset(request.horizon_start, self._slot_to_datetime(request, slot)))
            for slot in range(horizon_slots)
        ]

    def _fixed_task_slots(self, task: PlanningTask, request: PlanRequest) -> Tuple[int, int]:
        assert task.fixed_start is not None and task.fixed_end is not None
        clipped = self._clip_to_horizon(task.fixed_start, task.fixed_end, request)
        if clipped is None:
            raise ValueError(f"Fixed task '{task.id}' lies outside the planning horizon.")
        start_slot, end_slot = clipped
        if self._slot_to_datetime(request, start_slot) != task.fixed_start:
            raise ValueError(f"Fixed task '{task.id}' start must align with slot_minutes.")
        if self._slot_to_datetime(request, end_slot) != task.fixed_end:
            raise ValueError(f"Fixed task '{task.id}' end must align with slot_minutes.")
        return start_slot, end_slot

    def _clip_to_horizon(
        self,
        start: datetime,
        end: datetime,
        request: PlanRequest,
    ) -> Tuple[int, int] | None:
        clipped_start = max(start, request.horizon_start)
        clipped_end = min(end, request.horizon_end)
        if clipped_end <= clipped_start:
            return None
        start_slot = math.floor(
            (clipped_start - request.horizon_start).total_seconds() / 60 / request.slot_minutes
        )
        end_slot = math.ceil(
            (clipped_end - request.horizon_start).total_seconds() / 60 / request.slot_minutes
        )
        return start_slot, end_slot

    @staticmethod
    def _day_offset(horizon_start: datetime, value: datetime) -> int:
        return (value.date() - horizon_start.date()).days

    @staticmethod
    def _slot_to_datetime(request: PlanRequest, slot: int) -> datetime:
        return request.horizon_start + timedelta(minutes=slot * request.slot_minutes)

    @staticmethod
    def _safe_name(value: str) -> str:
        return "".join(character if character.isalnum() else "_" for character in value)

    @staticmethod
    def _validate_dependencies(
        tasks: Iterable[PlanningTask],
        task_by_id: Dict[str, PlanningTask],
    ) -> None:
        for task in tasks:
            for dependency in task.dependencies:
                if dependency not in task_by_id:
                    raise ValueError(
                        f"Task '{task.id}' depends on unknown or inactive task '{dependency}'."
                    )
                if dependency == task.id:
                    raise ValueError(f"Task '{task.id}' cannot depend on itself.")

    @staticmethod
    def _map_status(raw_status: int) -> SolverStatus:
        mapping = {
            cp_model.OPTIMAL: SolverStatus.OPTIMAL,
            cp_model.FEASIBLE: SolverStatus.FEASIBLE,
            cp_model.INFEASIBLE: SolverStatus.INFEASIBLE,
            cp_model.MODEL_INVALID: SolverStatus.MODEL_INVALID,
            cp_model.UNKNOWN: SolverStatus.UNKNOWN,
        }
        return mapping.get(raw_status, SolverStatus.UNKNOWN)

    @staticmethod
    def _infeasible_result(tasks: Sequence[PlanningTask], reason: str) -> OptimizationResult:
        return OptimizationResult(
            status=SolverStatus.INFEASIBLE,
            unscheduled_task_ids=tuple(task.id for task in tasks),
            diagnostics={"reason": reason},
        )
