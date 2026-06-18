from ortools.sat.python import cp_model

from .solver import WeeklyOptimizer as BaseWeeklyOptimizer


class WeeklyOptimizer(BaseWeeklyOptimizer):
    """Optimizer extension for explicit, profession-independent session metadata."""

    def _add_pairwise_transitions_and_spreading(
        self,
        model: cp_model.CpModel,
        sessions,
        request,
        objective_terms,
    ) -> None:
        for left_index in range(len(sessions)):
            left = sessions[left_index]
            for right_index in range(left_index + 1, len(sessions)):
                right = sessions[right_index]
                same_day = model.NewBoolVar(
                    f"same_day_{self._safe_name(left.task.id)}_{left.index}_"
                    f"{self._safe_name(right.task.id)}_{right.index}"
                )
                model.Add(left.day == right.day).OnlyEnforceIf(same_day)
                model.Add(left.day != right.day).OnlyEnforceIf(same_day.Not())

                if left.task.id == right.task.id:
                    if left.task.prefer_distinct_session_days:
                        objective_terms.append(self.config.weights.same_day_sessions * same_day)
                    if left.task.prefer_same_day_sessions:
                        objective_terms.append(self.config.weights.spread_across_days * (1 - same_day))

                left_before = model.NewBoolVar(
                    f"before_{self._safe_name(left.task.id)}_{left.index}_"
                    f"{self._safe_name(right.task.id)}_{right.index}"
                )
                buffer_left_right = self._transition_slots(left.task, right.task, request)
                buffer_right_left = self._transition_slots(right.task, left.task, request)
                model.Add(left.end + buffer_left_right <= right.start).OnlyEnforceIf([same_day, left_before])
                model.Add(right.end + buffer_right_left <= left.start).OnlyEnforceIf([same_day, left_before.Not()])
