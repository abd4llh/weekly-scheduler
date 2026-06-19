from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Tuple

from domain import ScheduledEvent


class SolverStatus(str, Enum):
    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    MODEL_INVALID = "model_invalid"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OptimizationResult:
    status: SolverStatus
    events: Tuple[ScheduledEvent, ...] = ()
    unscheduled_task_ids: Tuple[str, ...] = ()
    objective_score: float | None = None
    wall_time_seconds: float = 0.0
    diagnostics: Dict[str, object] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
