import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from coderouter.core.models import AgentResult, Task, ToolCall, UsageStatus
from coderouter.storage.db import Database


def _result(task, agent="claude", success=True, tokens=(100, 50)):
    return AgentResult(
        task_id=task.id, agent_id=agent, model="m", success=success, output="ok",
        files_changed=["a.py"], tool_calls=[ToolCall("Edit", "a.py")],
        input_tokens=tokens[0], output_tokens=tokens[1],
        usage_status=UsageStatus.CONFIRMED, duration_s=2.0,
    )


def test_success_rate_is_computed_in_sql(db):
    for i in range(4):
        task = Task(prompt=f"t{i}", task_type="bug_fix")
        db.save_task(task)
        db.save_run(f"run{i}", task, _result(task, success=i != 0))
    rate, samples = db.success_rate("claude", "bug_fix")
    assert samples == 4
    assert rate == 0.75


def test_unknown_agent_has_no_samples(db):
    assert db.success_rate("ghost") == (0.0, 0)


def test_dashboard_aggregates(db):
    task = Task(prompt="t")
    db.save_task(task)
    db.save_run("r1", task, _result(task))
    metrics = db.dashboard_metrics()
    assert metrics["runs"] == 1
    assert metrics["total_tokens"] == 150
    assert metrics["avg_duration_s"] == 2.0


def test_recent_runs_fetches_limit_plus_one_for_truncation_detection(db):
    for i in range(5):
        task = Task(prompt=f"t{i}")
        db.save_task(task)
        db.save_run(f"r{i}", task, _result(task))
    assert len(db.recent_runs(limit=2)) == 3


def test_context_cache_roundtrip(db):
    db.cache_context("fp1", None, "summary")
    assert db.cached_context("fp1") == "summary"
    assert db.cached_context("missing") is None


def test_classification_cache_roundtrip(db):
    payload = {"task_type": "bug_fix", "complexity": "low", "risk": "low", "confidence": 0.8}
    db.cache_classification("fp1", payload)
    assert db.cached_classification("fp1") == payload
    assert db.cached_classification("missing") is None


def test_classification_cache_expiration(db):
    payload = {"task_type": "bug_fix", "complexity": "low", "risk": "low", "confidence": 0.8}
    old_time = (datetime.now(UTC) - timedelta(hours=200)).isoformat()
    db.conn.execute(
        "INSERT INTO classification_cache (fingerprint, payload, created_at) VALUES (?,?,?)",
        ("fp_old", json.dumps(payload), old_time),
    )
    db.conn.commit()
    assert db.cached_classification("fp_old", max_age_hours=168) is None
    assert db.cached_classification("fp_old", max_age_hours=300) == payload


def test_record_usage_and_tokens_since_counts_all_kinds(db):
    since = datetime.now(UTC) - timedelta(hours=1)
    db.record_usage("claude", 1000, "confirmed", kind="run", run_id="r1")
    db.record_usage("claude", 250, "confirmed", kind="classification")
    db.record_usage("other", 500, "confirmed", kind="run")
    assert db.tokens_since("claude", since) == 1250
    assert db.tokens_since("other", since) == 500


def test_migration_adds_kind_column(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE usage_events ("
        "id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL, run_id TEXT, "
        "tokens INTEGER NOT NULL, status TEXT NOT NULL, occurred_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO usage_events (agent_id, run_id, tokens, status, occurred_at) "
        "VALUES ('claude', 'r1', 100, 'confirmed', datetime('now'))"
    )
    conn.commit()
    conn.close()

    legacy_db = Database(db_path)
    try:
        row = legacy_db.conn.execute("SELECT kind FROM usage_events WHERE run_id='r1'").fetchone()
        assert row["kind"] == "run"
        legacy_db.record_usage("claude", 50, "confirmed", kind="classification")
        row2 = legacy_db.conn.execute("SELECT kind FROM usage_events WHERE tokens=50").fetchone()
        assert row2["kind"] == "classification"
    finally:
        legacy_db.close()


def test_memory_upsert(db):
    db.remember("p1", "convention", "style", "black")
    db.remember("p1", "convention", "style", "ruff")
    row = db.conn.execute("SELECT value FROM memory WHERE key='style'").fetchone()
    assert row["value"] == "ruff"


def test_audit_log_records_decisions(db):
    db.audit("execute", task_id="t1", agent_id="claude", decision="cheapest capable")
    row = db.conn.execute("SELECT * FROM audit_log").fetchone()
    assert row["action"] == "execute" and row["agent_id"] == "claude"


def test_schema_version_is_stamped_on_a_fresh_database(tmp_path):
    from coderouter.storage.db import SCHEMA_VERSION, Database
    db = Database(tmp_path / "fresh.db")
    assert db.schema_version == SCHEMA_VERSION
    db.close()


def test_migrations_are_idempotent(tmp_path):
    from coderouter.storage.db import SCHEMA_VERSION, Database
    path = tmp_path / "again.db"
    db = Database(path)
    db.close()
    db = Database(path)
    assert db.migrate() == [], "a database at the current version applies nothing"
    assert db.schema_version == SCHEMA_VERSION
    db.close()


def test_upgrade_preserves_existing_rows(tmp_path):
    """An upgrade must never destroy a user's history."""
    import sqlite3

    from coderouter.storage.db import SCHEMA, SCHEMA_VERSION, Database

    path = tmp_path / "legacy.db"
    # A pre-migration-framework database: schema present, user_version still 0,
    # and the ad-hoc `kind` column already applied by the old __init__.
    raw = sqlite3.connect(path)
    raw.executescript(SCHEMA)
    raw.execute("INSERT INTO usage_events (agent_id, tokens, status, occurred_at) "
                "VALUES ('claude', 4242, 'confirmed', '2026-01-01T00:00:00+00:00')")
    raw.commit()
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
    raw.close()

    db = Database(path)
    assert db.schema_version == SCHEMA_VERSION
    row = db.conn.execute("SELECT SUM(tokens) AS t FROM usage_events").fetchone()
    assert row["t"] == 4242, "existing usage history must survive the upgrade"
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(runs)")}
    assert {"integrated", "integration_detail"} <= cols
    db.close()


def test_run_records_whether_its_work_was_integrated(tmp_path, db):
    from coderouter.core.models import AgentResult, Task, UsageStatus
    from coderouter.git.worktree import IntegrationResult

    task = Task(prompt="x")
    result = AgentResult(task_id=task.id, agent_id="fake", model=None, success=True,
                         output="ok", usage_status=UsageStatus.CONFIRMED)
    db.save_run("run_merged", task, result,
                IntegrationResult(merged=True, detail="merged"))
    db.save_run("run_stranded", task, result,
                IntegrationResult(merged=False, conflicted=True, detail="conflict"))

    rows = {r["id"]: r for r in db.conn.execute(
        "SELECT id, integrated, integration_detail FROM runs")}
    assert rows["run_merged"]["integrated"] == 1
    assert rows["run_stranded"]["integrated"] == 0
    assert "conflict" in rows["run_stranded"]["integration_detail"]
