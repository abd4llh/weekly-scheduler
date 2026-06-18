from dataclasses import dataclass

@dataclass(frozen=True)
class OptimizerWeights:
    preferred_window: int = 20
    weekend: int = 18
    late_start: int = 1
    daily_load_imbalance: int = 12
    daily_overload: int = 30
    total_burden_overload: int = 22
    focused_work_overload: int = 28
    late_focused_work: int = 18
    same_day_sessions: int = 250
    spread_across_days: int = 120
    compact_gap: int = 2
    compact_gap_excess: int = 12

@dataclass(frozen=True)
class OptimizerConfig:
    max_solve_seconds: float = 20.0
    num_search_workers: int = 8
    random_seed: int = 7
    log_search_progress: bool = False
    weights: OptimizerWeights = OptimizerWeights()
