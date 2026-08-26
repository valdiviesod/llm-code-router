"""Scheduling policy.

A `SchedulingPolicy` decides which tasks in a `TaskGraph` to dispatch
together. The default `BatchingPolicy` is a no-op wrapper around
`TaskGraph.ready()`: it returns the same batches the orchestrator
already produced, in the same order. New strategies — speculative
parallel dispatch, priority-weighted scheduling, deadline-aware
queues — plug in by implementing `decide_batch()`.

The scheduler never decides *which agent* runs a task; that is the
router's job. It decides *which tasks* run together, *how many*
may run at once, and (in the speculative case) *which additional
agents* a single task may race against.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.models import Task
from ..core.task_graph import TaskGraph


class SchedulingPolicy(ABC):
    """The seam where future scheduling strategies plug in.

    `decide_batch()` returns the next batch of tasks to dispatch. The
    orchestrator runs them in parallel and asks again for the next
    batch when the current one finishes.
    """

    @abstractmethod
    def decide_batch(self, graph: TaskGraph) -> list[Task]:
        """Pick the next batch of tasks to dispatch in parallel.

        Return an empty list when the graph is finished. The order
        inside the batch does not matter; tasks are independent
        within a batch by construction.
        """


class BatchingPolicy(SchedulingPolicy):
    """The default: dispatch every ready task together, bounded by
    `max_batch` so a flood of ready tasks never overwhelms the
    concurrency semaphore.

    `max_batch=None` means "no cap" and matches v0.1.0 behaviour
    exactly; a positive int caps the batch to that many tasks.
    """

    def __init__(self, max_batch: int | None = None) -> None:
        self.max_batch = max_batch

    def decide_batch(self, graph: TaskGraph) -> list[Task]:
        ready = graph.ready()
        if not ready:
            return []
        if self.max_batch is not None:
            return ready[: self.max_batch]
        return list(ready)
