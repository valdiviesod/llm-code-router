"""SQLite persistence.

Deliberately plain sqlite3 with a narrow repository API. The schema uses no
SQLite-only syntax beyond AUTOINCREMENT-free integer keys, so moving to
PostgreSQL later means swapping this module's connection layer, not the callers.

All aggregation happens in SQL (COUNT/SUM/AVG). Callers never sum rows in Python.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..logging import get_logger

logger = get_logger("storage.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, root TEXT UNIQUE NOT NULL, name TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, prompt TEXT NOT NULL,
    task_type TEXT, complexity TEXT, risk TEXT, state TEXT NOT NULL,
    created_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, agent_id TEXT NOT NULL,
    model TEXT, attempt INTEGER NOT NULL DEFAULT 1, success INTEGER NOT NULL,
    duration_s REAL, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cost_usd REAL, usage_status TEXT, error TEXT, output_summary TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_agent ON runs(agent_id, created_at);
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL, run_id TEXT,
    tokens INTEGER NOT NULL, status TEXT NOT NULL, occurred_at TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'run'
);
CREATE INDEX IF NOT EXISTS idx_usage_agent_time ON usage_events(agent_id, occurred_at);
CREATE TABLE IF NOT EXISTS routing_decisions (
    id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, selected_agent TEXT NOT NULL,
    selected_model TEXT, mode TEXT, reason TEXT, confidence REAL,
    estimated_tokens INTEGER, alternatives TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_stats (
    agent_id TEXT NOT NULL, task_type TEXT NOT NULL, complexity TEXT NOT NULL,
    successes INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0, total_duration_s REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_id, task_type, complexity)
);
CREATE TABLE IF NOT EXISTS file_changes (
    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, name TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS validations (
    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, check_name TEXT NOT NULL,
    passed INTEGER NOT NULL, detail TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL,
    key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (project_id, kind, key)
);
CREATE TABLE IF NOT EXISTS context_cache (
    fingerprint TEXT PRIMARY KEY, project_id TEXT, summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS classification_cache (
    fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER, task_id TEXT, agent_id TEXT, action TEXT NOT NULL,
    decision TEXT, detail TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
    id TEXT PRIMARY KEY, pool_id TEXT NOT NULL, agent_id TEXT NOT NULL,
    tokens INTEGER NOT NULL, task_id TEXT,
    created_at TEXT NOT NULL, settled INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reservations_pool ON reservations(pool_id, settled);
"""


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_usage_kind(conn: sqlite3.Connection) -> None:
    """Record what a usage event was spent on (a run, a classification, ...).

    Guarded because installs that predate the migration framework already ran
    this as an ad-hoc `ALTER TABLE` with `user_version` still at 0.
    """
    if "kind" not in _columns(conn, "usage_events"):
        conn.execute(
            "ALTER TABLE usage_events ADD COLUMN kind TEXT NOT NULL DEFAULT 'run'"
        )


def _add_run_integration(conn: sqlite3.Connection) -> None:
    """Whether an isolated run's work actually reached the project tree.

    A run that succeeded but whose branch could not be merged is not the same
    as a run that landed, and the history was unable to tell them apart.
    """
    cols = _columns(conn, "runs")
    if "integrated" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN integrated INTEGER")
    if "integration_detail" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN integration_detail TEXT")


def _add_decision_rejected(conn: sqlite3.Connection) -> None:
    """Candidates a hard constraint removed, and why.

    Without it `router explain` can only say what was chosen, never what was
    ruled out — which is the half of a routing decision people actually argue
    with.
    """
    if "rejected" not in _columns(conn, "routing_decisions"):
        conn.execute("ALTER TABLE routing_decisions ADD COLUMN rejected TEXT")


#: Ordered schema migrations. Append only — never renumber, never edit a
#: migration that has shipped, because existing databases have already run it.
#: `user_version` records how far a database has got.
MIGRATIONS: tuple[tuple[int, str, Callable[[sqlite3.Connection], None]], ...] = (
    (1, "usage_events.kind", _add_usage_kind),
    (2, "runs.integrated", _add_run_integration),
    (3, "routing_decisions.rejected", _add_decision_rejected),
)

SCHEMA_VERSION = MIGRATIONS[-1][0]


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.migrate()

    # --- schema ---------------------------------------------------------

    @property
    def schema_version(self) -> int:
        row = self.conn.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def migrate(self) -> list[str]:
        """Bring the database up to `SCHEMA_VERSION`, returning what was applied.

        Migrations are additive by policy: they add columns and tables, never
        drop or rewrite them. An upgrade must never destroy a user's history.
        """
        applied: list[str] = []
        current = self.schema_version
        for version, name, step in MIGRATIONS:
            if version <= current:
                continue
            step(self.conn)
            # PRAGMA does not take a parameter binding.
            self.conn.execute(f"PRAGMA user_version = {int(version)}")
            self.conn.commit()
            applied.append(f"{version}: {name}")
            logger.info("applied schema migration %d (%s)", version, name)
        return applied

    def close(self) -> None:
        self.conn.close()

    # --- writes ---------------------------------------------------------
    def upsert_project(self, project_id: str, root: Path, name: str) -> str:
        self.conn.execute(
            "INSERT INTO projects (id, root, name, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(root) DO UPDATE SET name=excluded.name",
            (project_id, str(root), name, _utc()),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM projects WHERE root=?", (str(root),)).fetchone()
        return str(row["id"])

    def save_task(self, task: Any, project_id: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO tasks (id, project_id, parent_id, prompt, task_type, complexity, "
            "risk, state, created_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET state=excluded.state",
            (task.id, project_id, task.parent_id, task.prompt, task.task_type,
             task.complexity.value, task.risk.value, task.state.value,
             task.created_at.isoformat()),
        )
        self.conn.commit()

    def save_decision(self, decision: Any) -> None:
        self.conn.execute(
            "INSERT INTO routing_decisions (task_id, selected_agent, selected_model, mode, "
            "reason, confidence, estimated_tokens, alternatives, created_at, rejected) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (decision.task_id, decision.selected_agent, decision.selected_model,
             decision.mode.value, decision.reason, decision.confidence,
             decision.estimated_usage.total_tokens,
             json.dumps([{"agent": c.agent_id, "score": c.score, "reasons": c.reasons}
                         for c in decision.alternatives]),
             _utc(),
             json.dumps([{"agent": a, "reason": r}
                         for a, r in getattr(decision, "rejected", [])])),
        )
        self.conn.commit()

    def save_run(self, run_id: str, task: Any, result: Any,
                 integration: Any = None) -> None:
        """Persist one run. `integration` is the `IntegrationResult` for an
        isolated run, so history can tell work that landed from work that
        merely succeeded in a worktree that was then thrown away."""
        now = _utc()
        integrated = None if integration is None else int(integration.merged)
        detail = None if integration is None else integration.detail[:500]
        self.conn.execute(
            "INSERT INTO runs (id, task_id, agent_id, model, attempt, success, duration_s, "
            "input_tokens, output_tokens, cost_usd, usage_status, error, output_summary, "
            "created_at, integrated, integration_detail) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, task.id, result.agent_id, result.model, task.attempt,
             int(result.success), result.duration_s, result.input_tokens,
             result.output_tokens, result.cost_usd, result.usage_status.value,
             result.error, result.output[:2000], now, integrated, detail),
        )
        self.record_usage(
            result.agent_id, result.total_tokens, result.usage_status.value,
            kind="run", run_id=run_id,
        )
        self.conn.executemany(
            "INSERT INTO file_changes (run_id, path) VALUES (?,?)",
            [(run_id, p) for p in result.files_changed],
        )
        self.conn.executemany(
            "INSERT INTO tool_calls (run_id, name, detail) VALUES (?,?,?)",
            [(run_id, t.name, t.detail) for t in result.tool_calls],
        )
        self.conn.execute(
            "INSERT INTO agent_stats (agent_id, task_type, complexity, successes, failures, "
            "total_tokens, total_duration_s) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(agent_id, task_type, complexity) DO UPDATE SET "
            "successes = successes + excluded.successes, "
            "failures = failures + excluded.failures, "
            "total_tokens = total_tokens + excluded.total_tokens, "
            "total_duration_s = total_duration_s + excluded.total_duration_s",
            (result.agent_id, task.task_type, task.complexity.value,
             int(result.success), int(not result.success), result.total_tokens,
             result.duration_s),
        )
        self.conn.commit()

    def record_usage(
        self,
        agent_id: str,
        tokens: int,
        status: str,
        *,
        kind: str = "run",
        run_id: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO usage_events (agent_id, run_id, tokens, status, occurred_at, kind) "
            "VALUES (?,?,?,?,?,?)",
            (agent_id, run_id, tokens, status, _utc(), kind),
        )
        self.conn.commit()

    def audit(self, action: str, *, task_id: str | None = None, agent_id: str | None = None,
              decision: str | None = None, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO audit_log (task_id, agent_id, action, decision, detail, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (task_id, agent_id, action, decision, detail, _utc()),
        )
        self.conn.commit()

    def save_validation(self, run_id: str, results: Iterable[Any]) -> None:
        self.conn.executemany(
            "INSERT INTO validations (run_id, check_name, passed, detail, created_at) "
            "VALUES (?,?,?,?,?)",
            [(run_id, r.name, int(r.passed), r.detail[:2000], _utc()) for r in results],
        )
        self.conn.commit()

    def remember(self, project_id: str, kind: str, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO memory (project_id, kind, key, value, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(project_id, kind, key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (project_id, kind, key, value, _utc()),
        )
        self.conn.commit()

    def cache_context(self, fingerprint: str, project_id: str | None, summary: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO context_cache (fingerprint, project_id, summary, created_at) "
            "VALUES (?,?,?,?)",
            (fingerprint, project_id, summary, _utc()),
        )
        self.conn.commit()

    def cache_classification(self, fingerprint: str, payload: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO classification_cache (fingerprint, payload, created_at) "
            "VALUES (?,?,?)",
            (fingerprint, json.dumps(payload), _utc()),
        )
        self.conn.commit()

    # --- reads (aggregation stays in SQL) --------------------------------
    def cached_context(self, fingerprint: str, max_age_hours: int = 24) -> str | None:
        cutoff = (datetime.now(UTC) - timedelta(hours=max_age_hours)).isoformat()
        row = self.conn.execute(
            "SELECT summary FROM context_cache WHERE fingerprint=? AND created_at>=?",
            (fingerprint, cutoff),
        ).fetchone()
        return str(row["summary"]) if row else None

    def cached_classification(self, fingerprint: str, max_age_hours: int = 168) -> dict | None:
        cutoff = (datetime.now(UTC) - timedelta(hours=max_age_hours)).isoformat()
        row = self.conn.execute(
            "SELECT payload FROM classification_cache WHERE fingerprint=? AND created_at>=?",
            (fingerprint, cutoff),
        ).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row["payload"])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    def tokens_since(self, agent_id: str, since: datetime) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(tokens), 0) AS total FROM usage_events "
            "WHERE agent_id=? AND occurred_at >= ?",
            (agent_id, since.isoformat()),
        ).fetchone()
        return int(row["total"])

    def success_rate(self, agent_id: str, task_type: str | None = None) -> tuple[float, int]:
        """Returns (rate, sample_size). Rate is 0..1, computed by SQL."""
        sql = ("SELECT COALESCE(SUM(successes),0) AS s, COALESCE(SUM(failures),0) AS f "
               "FROM agent_stats WHERE agent_id=?")
        params: list[Any] = [agent_id]
        if task_type:
            sql += " AND task_type=?"
            params.append(task_type)
        row = self.conn.execute(sql, params).fetchone()
        total = int(row["s"]) + int(row["f"])
        if total == 0:
            return 0.0, 0
        return int(row["s"]) / total, total

    def avg_tokens(self, agent_id: str, task_type: str | None = None) -> int | None:
        sql = ("SELECT COALESCE(SUM(total_tokens),0) AS t, "
               "COALESCE(SUM(successes)+SUM(failures),0) AS n "
               "FROM agent_stats WHERE agent_id=?")
        params: list[Any] = [agent_id]
        if task_type:
            sql += " AND task_type=?"
            params.append(task_type)
        row = self.conn.execute(sql, params).fetchone()
        return int(int(row["t"]) / int(row["n"])) if int(row["n"]) else None

    def avg_tokens_detail(
        self, agent_id: str, task_type: str | None = None, complexity: str | None = None,
    ) -> tuple[int | None, int]:
        """Average observed total tokens and the sample size behind it.

        The sample size is what the estimator uses to decide whether the
        number can be trusted or is still a cold-start guess: an average of
        two runs is noise, an average of fifty is a forecast.
        """
        sql = ("SELECT COALESCE(SUM(total_tokens),0) AS t, "
               "COALESCE(SUM(successes)+SUM(failures),0) AS n "
               "FROM agent_stats WHERE agent_id=?")
        params: list[Any] = [agent_id]
        if task_type:
            sql += " AND task_type=?"
            params.append(task_type)
        if complexity:
            sql += " AND complexity=?"
            params.append(complexity)
        row = self.conn.execute(sql, params).fetchone()
        n = int(row["n"])
        return (int(int(row["t"]) / n) if n else None, n)

    def avg_token_split(
        self, agent_id: str, task_type: str | None = None
    ) -> tuple[float, float]:
        """Average (input, output) tokens per recorded run of this agent.

        Used to split an observed *total* into the input/output halves the
        UsageEstimate shape needs. Returns (0.0, 0.0) with no history.
        """
        sql = ("SELECT AVG(r.input_tokens) AS i, AVG(r.output_tokens) AS o "
               "FROM runs r JOIN tasks t ON t.id = r.task_id "
               "WHERE r.agent_id=?")
        params: list[Any] = [agent_id]
        if task_type:
            sql += " AND t.task_type=?"
            params.append(task_type)
        row = self.conn.execute(sql, params).fetchone()
        return (float(row["i"] or 0.0), float(row["o"] or 0.0))

    def estimate_correction(self, agent_id: str, max_ratio: float = 100.0) -> float | None:
        """Running ratio of actual spend to pre-flight estimate, per agent.

        This is the honest bridge between the single-shot heuristic and the
        agentic reality it models badly: whatever the systematic gap has been
        on recorded runs (44x on the run that motivated calibration), the
        heuristic gets multiplied by it instead of being trusted raw. Each
        per-run ratio is capped at `max_ratio` so one pathological run cannot
        dominate the mean.
        """
        row = self.conn.execute(
            "SELECT AVG(MIN(?, 1.0*(r.input_tokens+r.output_tokens)/d.estimated_tokens)) "
            "AS ratio FROM runs r JOIN routing_decisions d ON d.task_id = r.task_id "
            "WHERE r.agent_id=? AND d.estimated_tokens > 0 "
            "AND (r.input_tokens + r.output_tokens) > 0",
            (max_ratio, agent_id),
        ).fetchone()
        ratio = row["ratio"]
        return float(ratio) if ratio is not None else None

    def dashboard_metrics(self) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT COUNT(*) AS runs, COALESCE(SUM(success),0) AS ok, "
            "COALESCE(AVG(duration_s),0) AS avg_duration, "
            "COALESCE(SUM(input_tokens+output_tokens),0) AS tokens "
            "FROM runs"
        ).fetchone()
        states = {
            r["state"]: r["n"]
            for r in self.conn.execute("SELECT state, COUNT(*) AS n FROM tasks GROUP BY state")
        }
        return {
            "runs": int(row["runs"]),
            "successes": int(row["ok"]),
            "avg_duration_s": float(row["avg_duration"]),
            "total_tokens": int(row["tokens"]),
            "tasks_by_state": states,
        }

    def decision_for(self, task_id: str) -> sqlite3.Row | None:
        """The most recent routing decision recorded for a task."""
        return self.conn.execute(
            "SELECT * FROM routing_decisions WHERE task_id = ? "
            "ORDER BY created_at DESC LIMIT 1", (task_id,),
        ).fetchone()

    def runs_for(self, task_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM runs WHERE task_id = ? ORDER BY created_at", (task_id,)))

    def task_row(self, task_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    def recent_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        # Fetch limit+1 so callers can tell a full page from a truncated one.
        return list(self.conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit + 1,)
        ))
