"""Scheduling subsystem.

The default `BatchingPolicy` is a no-op wrapper over `TaskGraph.ready()`.
Speculative execution lives in `race_attempts`, which the orchestrator
drives directly; `is_speculative_eligible` is the gate.
"""

from .policy import BatchingPolicy, SchedulingPolicy
from .speculative import SpeculativeConfig, is_speculative_eligible, race_attempts

__all__ = [
    "BatchingPolicy",
    "SchedulingPolicy",
    "SpeculativeConfig",
    "is_speculative_eligible",
    "race_attempts",
]
