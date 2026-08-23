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
