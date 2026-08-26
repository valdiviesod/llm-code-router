"""Scheduling subsystem.

The default `BatchingPolicy` is a no-op wrapper over
`TaskGraph.ready()`; `SpeculativeDispatcher` is the seam where
race-the-top-N dispatch will plug in.
"""

from .policy import BatchingPolicy, SchedulingPolicy
from .speculative import (
    SpeculativeConfig,
    SpeculativeDispatcher,
    is_speculative_eligible,
    race_attempts,
)

__all__ = [
    "BatchingPolicy",
    "SchedulingPolicy",
    "SpeculativeConfig",
    "SpeculativeDispatcher",
    "is_speculative_eligible",
    "race_attempts",
]
