from dataclasses import dataclass


@dataclass(frozen=True)
class OptimizerWeights:
    """Integer penalty weights used by the CP-SAT objective."""

    preferred_window: int = 20
    weekend: int = 12
    late_start: int = 1
    daily_load_imbalance: int = 4


@dataclass(frozen=True)
class OptimizerConfig:
    max_solve_seconds: float = 20.0
    num_search_workers: int = 8
    random_seed: int = 7
    log_search_progress: bool = False
    weights: OptimizerWeights = OptimizerWeights()
