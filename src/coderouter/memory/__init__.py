"""Memory subsystem.

Project-scoped note store + learner + injector. The store wraps
the existing `memory` SQLite table; the learner turns run history
into typed notes when statistical thresholds are crossed; the
injector folds the most recent notes into the task prompt under a
token budget.
"""

from .injector import MemoryBlock, MemoryInjector
from .learner import Learner, LearnerConfig
from .store import MemoryKind, MemoryNote, MemoryStore

__all__ = [
    "Learner",
    "LearnerConfig",
    "MemoryBlock",
    "MemoryInjector",
    "MemoryKind",
    "MemoryNote",
    "MemoryStore",
]
