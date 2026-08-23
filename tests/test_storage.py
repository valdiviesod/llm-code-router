from v4ld1.core.models import AgentResult, Task, ToolCall, UsageStatus


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


def test_memory_upsert(db):
    db.remember("p1", "convention", "style", "black")
    db.remember("p1", "convention", "style", "ruff")
    row = db.conn.execute("SELECT value FROM memory WHERE key='style'").fetchone()
    assert row["value"] == "ruff"


def test_audit_log_records_decisions(db):
    db.audit("execute", task_id="t1", agent_id="claude", decision="cheapest capable")
    row = db.conn.execute("SELECT * FROM audit_log").fetchone()
    assert row["action"] == "execute" and row["agent_id"] == "claude"
