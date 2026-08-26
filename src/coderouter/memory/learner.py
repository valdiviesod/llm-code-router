"""Learner: turn run history into memory notes.

The router records every (agent, task_type, complexity) cell in
`agent_stats`. The Learner watches those cells and, when one crosses
a statistical threshold, writes a memory note summarising what was
learned. Success patterns are written when the rate is high enough;
failure patterns when it is low enough; the threshold is the same on
both sides so the two flags never fire on the same cell at the same
time.

Notes are written through the MemoryStore, so they are project-
scoped and the same redaction rules apply as everywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import Complexity
from ..storage.db import Database
from .store import MemoryKind, MemoryStore

MIN_SAMPLES = 5
SUCCESS_THRESHOLD = 0.85
FAILURE_THRESHOLD = 0.4


@dataclass(slots=True)
class LearnerConfig:
    enabled: bool = True
    min_samples: int = MIN_SAMPLES
    success_threshold: float = SUCCESS_THRESHOLD
    failure_threshold: float = FAILURE_THRESHOLD


class Learner:
    def __init__(self, db: Database, store: MemoryStore,
                 config: LearnerConfig | None = None) -> None:
        self._db = db
        self._store = store
        self._config = config or LearnerConfig()

    def observe(self, project_id: str) -> list[str]:
        """Walk every `(agent, task_type, complexity)` cell and write notes
        for the ones that crossed a threshold. Returns the keys it wrote.
        """
        if not self._config.enabled:
            return []
        written: list[str] = []
        rows = self._db.conn.execute(
            "SELECT agent_id, task_type, complexity, "
            "COALESCE(SUM(successes),0) AS s, "
            "COALESCE(SUM(failures),0) AS f "
            "FROM agent_stats GROUP BY agent_id, task_type, complexity"
        ).fetchall()
        for row in rows:
            successes = int(row["s"])
            failures = int(row["f"])
            total = successes + failures
            if total < self._config.min_samples:
                continue
            rate = successes / total if total else 0.0
            key = f"{row['agent_id']}/{row['task_type']}/{row['complexity']}"
            if rate >= self._config.success_threshold:
                value = (
                    f"agent {row['agent_id']} succeeds on {row['task_type']} "
                    f"@ {row['complexity']} in {successes}/{total} runs "
                    f"({rate:.0%})"
                )
                self._store.upsert(project_id, MemoryKind.SUCCESS_PATTERN, key, value)
                written.append(key)
            elif rate <= self._config.failure_threshold:
                value = (
                    f"agent {row['agent_id']} fails on {row['task_type']} "
                    f"@ {row['complexity']} in {failures}/{total} runs "
                    f"({rate:.0%}); route elsewhere unless forced"
                )
                self._store.upsert(project_id, MemoryKind.FAILURE_PATTERN, key, value)
                written.append(key)
        return written

    def note_complexity_distribution(self, project_id: str) -> None:
        """Persist the current complexity distribution as a single
        convention note. Cheap; lets the user see at a glance what
        the router is doing.
        """
        rows = self._db.conn.execute(
            "SELECT complexity, COUNT(*) AS n FROM tasks GROUP BY complexity"
        ).fetchall()
        if not rows:
            return
        parts = ", ".join(f"{r['complexity']}={r['n']}" for r in rows)
        self._store.upsert(
            project_id, MemoryKind.CONVENTION, "complexity_distribution", parts,
        )

    @staticmethod
    def all_complexities() -> list[str]:
        return [c.value for c in Complexity]
