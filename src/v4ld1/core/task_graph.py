"""Task decomposition and dependency ordering.

Splitting is heuristic and conservative: only tasks that clearly span several
disciplines get decomposed, because every extra subtask is another agent run and
another chunk of quota.
"""

from __future__ import annotations

from collections import deque

from ..core.models import Capability, Complexity, Task, TaskState

# (subtask label, prompt suffix, required capabilities)
_STAGES: list[tuple[str, str, frozenset[Capability]]] = [
    ("architecture", "Design the approach and the file-level plan. Do not write "
     "implementation code yet.", frozenset({Capability.PLANNING, Capability.DEEP_REASONING})),
    ("implementation", "Implement the plan.", frozenset({Capability.CODE_EDIT})),
    ("tests", "Write or update the tests covering this change, then run them.",
     frozenset({Capability.CODE_EDIT, Capability.SHELL})),
    ("review", "Review the change for correctness, security and regressions.",
     frozenset({Capability.REVIEW})),
]


class TaskGraph:
    """A DAG of tasks with ready-set scheduling."""

    def __init__(self, tasks: list[Task]):
        self.tasks = {t.id: t for t in tasks}
        self._validate()

    def _validate(self) -> None:
        # Kahn's algorithm; a non-empty remainder means a cycle.
        indegree = {tid: len(t.depends_on) for tid, t in self.tasks.items()}
        queue = deque(tid for tid, deg in indegree.items() if deg == 0)
        seen = 0
        while queue:
            tid = queue.popleft()
            seen += 1
            for other in self.tasks.values():
                if tid in other.depends_on:
                    indegree[other.id] -= 1
                    if indegree[other.id] == 0:
                        queue.append(other.id)
        if seen != len(self.tasks):
            raise ValueError("task graph contains a cycle")

    def ready(self) -> list[Task]:
        """Pending tasks whose dependencies have all completed."""
        done = {t.id for t in self.tasks.values() if t.state is TaskState.DONE}
        return [
            t for t in self.tasks.values()
            if t.state is TaskState.PENDING and set(t.depends_on) <= done
        ]

    def finished(self) -> bool:
        return all(
            t.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)
            for t in self.tasks.values()
        )

    def failed(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.state is TaskState.FAILED]


def build_graph(task: Task) -> TaskGraph:
    """Decompose a task if it is big enough to be worth it, else return it alone."""
    if task.complexity.rank < Complexity.HIGH.rank:
        return TaskGraph([task])

    subtasks: list[Task] = []
    previous: str | None = None
    for label, suffix, caps in _STAGES:
        sub = Task(
            prompt=f"{task.prompt}\n\nStage: {label}. {suffix}",
            parent_id=task.id,
            project_root=task.project_root,
            task_type=label if label != "implementation" else task.task_type,
            complexity=task.complexity,
            risk=task.risk,
            required_capabilities=caps,
            depends_on=[previous] if previous else [],
            forced_agent=task.forced_agent,
        )
        subtasks.append(sub)
        previous = sub.id
    return TaskGraph(subtasks)
