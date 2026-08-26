from pathlib import Path

from coderouter.core.models import Complexity, Task, TaskState


async def test_run_task_records_run_and_usage(orchestrator, db, tmp_path):
    outcome = await orchestrator.run_task(Task(prompt="add a helper", project_root=tmp_path))
    assert outcome.result.success
    assert outcome.task.state is TaskState.DONE
    metrics = db.dashboard_metrics()
    assert metrics["runs"] == 1
    assert metrics["total_tokens"] == 1000


async def test_failure_escalates_to_the_other_agent(orchestrator, adapters):
    adapters[0].succeed = False
    adapters[1].succeed = True
    outcome = await orchestrator.run_task(Task(prompt="anything", forced_agent="fake"))
    assert outcome.result.agent_id == "other", "should have escalated"
    assert adapters[1].calls[0].handoff is not None, "retry must carry the failure context"


async def test_escalation_stops_at_max_attempts(orchestrator, adapters):
    for adapter in adapters:
        adapter.succeed = False
    outcome = await orchestrator.run_task(Task(prompt="doomed"))
    assert not outcome.result.success
    total_calls = sum(len(a.calls) for a in adapters)
    assert total_calls == orchestrator.config.routing.max_attempts


async def test_handoff_is_compact(orchestrator):
    outcome = await orchestrator.run_task(Task(prompt="x" * 5000))
    rendered = outcome.handoff.render()
    assert len(rendered) < 2500, "handoff must not carry the whole conversation"
    assert "Task:" in rendered


async def test_graph_runs_all_subtasks(orchestrator, tmp_path):
    task = Task(prompt="build the whole auth system", complexity=Complexity.HIGH,
                project_root=tmp_path)
    graph = orchestrator.plan(task)
    outcomes = await orchestrator.run_graph(graph)
    assert len(outcomes) == 4
    assert all(o.result.success for o in outcomes)


async def test_analyze_populates_classification(orchestrator, tmp_path: Path):
    (tmp_path / "auth.py").write_text("def login(): ...")
    task = await orchestrator.analyze("fix the login auth bug", tmp_path)
    assert task.task_type == "security"
    assert any(p.name == "auth.py" for p in task.context_files)


async def test_speculative_dispatch_returns_a_real_outcome(orchestrator, db, tmp_path):
    """Regression: the speculative path used to import a non-existent
    `TaskOutcome` from `.models` and crash, and re-persisted the winning
    run a second time, double-counting its tokens against the quota."""
    orchestrator.speculative_config.enabled = True
    task = Task(prompt="tiny tweak", complexity=Complexity.LOW, project_root=tmp_path)

    outcome = await orchestrator._dispatch_task(
        task, mode_override=None, isolate=False, on_event=None,
    )

    assert outcome.result.success
    assert outcome.task.state is TaskState.DONE
    metrics = db.dashboard_metrics()
    # However many attempts got far enough to finish, each is on the books
    # exactly once. The winner used to be persisted twice, so its tokens were
    # counted twice against the quota.
    assert 1 <= metrics["runs"] <= 2
    assert metrics["total_tokens"] == metrics["runs"] * 1000
