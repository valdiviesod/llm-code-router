"""Tests for the memory subsystem."""

from __future__ import annotations

from coderouter.core.models import AgentResult, Task, TaskState, UsageStatus
from coderouter.memory import (
    Learner,
    LearnerConfig,
    MemoryBlock,
    MemoryInjector,
    MemoryKind,
    MemoryStore,
)


def _record_run(db, agent_id: str, task_type: str, complexity: str, success: bool):
    task = Task(prompt="x", task_type=task_type)
    task.state = TaskState.DONE
    db.save_task(task)
    result = AgentResult(
        task_id=task.id, agent_id=agent_id, model="m", success=success,
        output="ok", input_tokens=10, output_tokens=10,
        usage_status=UsageStatus.CONFIRMED, duration_s=0.1,
    )
    db.save_run(f"r-{task.id}", task, result)
    # save_run also records an agent_stats row internally via the run path.
    # We still need to bump the stats explicitly for the learner to see them.
    successes = 1 if success else 0
    failures = 0 if success else 1
    db.conn.execute(
        "INSERT INTO agent_stats (agent_id, task_type, complexity, successes, failures, "
        "total_tokens, total_duration_s) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(agent_id, task_type, complexity) DO UPDATE SET "
        "successes = successes + excluded.successes, "
        "failures = failures + excluded.failures",
        (agent_id, task_type, complexity, successes, failures, 20, 0.1),
    )
    db.conn.commit()


def test_store_upsert_and_list(db):
    store = MemoryStore(db)
    store.upsert("p1", MemoryKind.CONVENTION, "python-style", "ruff + 100 cols")
    notes = store.list("p1", kind=MemoryKind.CONVENTION)
    assert len(notes) == 1
    assert notes[0].key == "python-style"
    assert "ruff" in notes[0].value


def test_store_upsert_overwrites(db):
    store = MemoryStore(db)
    store.upsert("p1", MemoryKind.NOTE, "x", "first")
    store.upsert("p1", MemoryKind.NOTE, "x", "second")
    notes = store.list("p1", kind=MemoryKind.NOTE)
    assert len(notes) == 1
    assert notes[0].value == "second"


def test_store_delete_returns_true_when_present(db):
    store = MemoryStore(db)
    store.upsert("p1", MemoryKind.NOTE, "x", "v")
    assert store.delete("p1", MemoryKind.NOTE, "x")
    assert not store.delete("p1", MemoryKind.NOTE, "x")


def test_learner_writes_success_pattern_when_rate_high(db, tmp_path):
    store = MemoryStore(db)
    learner = Learner(db, store, LearnerConfig(min_samples=5))
    for _ in range(9):
        _record_run(db, "claude", "refactor", "medium", success=True)
    _record_run(db, "claude", "refactor", "medium", success=False)
    written = learner.observe(str(tmp_path))
    assert any("claude/refactor/medium" in k for k in written)
    notes = store.list(str(tmp_path), kind=MemoryKind.SUCCESS_PATTERN)
    assert any("claude/refactor/medium" in n.key for n in notes)


def test_learner_writes_failure_pattern_when_rate_low(db, tmp_path):
    store = MemoryStore(db)
    learner = Learner(db, store, LearnerConfig(min_samples=5))
    for _ in range(3):
        _record_run(db, "claude", "database", "high", success=True)
    for _ in range(7):
        _record_run(db, "claude", "database", "high", success=False)
    written = learner.observe(str(tmp_path))
    assert any("claude/database/high" in k for k in written)
    notes = store.list(str(tmp_path), kind=MemoryKind.FAILURE_PATTERN)
    assert any("claude/database/high" in n.key for n in notes)


def test_learner_skips_when_min_samples_not_met(db, tmp_path):
    store = MemoryStore(db)
    learner = Learner(db, store, LearnerConfig(min_samples=5))
    for _ in range(2):
        _record_run(db, "claude", "refactor", "low", success=True)
    assert learner.observe(str(tmp_path)) == []


def test_learner_disabled_does_nothing(db, tmp_path):
    store = MemoryStore(db)
    learner = Learner(db, store, LearnerConfig(enabled=False))
    for _ in range(10):
        _record_run(db, "claude", "refactor", "low", success=True)
    assert learner.observe(str(tmp_path)) == []


def test_injector_builds_block_under_budget(db, tmp_path):
    store = MemoryStore(db)
    project_id = str(tmp_path.resolve())
    store.upsert(project_id, MemoryKind.CONVENTION, "k1", "v1")
    store.upsert(project_id, MemoryKind.DECISION, "k2", "v2")
    injector = MemoryInjector(store, budget_tokens=1000)
    task = Task(prompt="x", project_root=tmp_path)
    block = injector.build(task)
    assert isinstance(block, MemoryBlock)
    assert "v1" in block.block
    assert "v2" in block.block


def test_injector_empty_when_no_notes(db, tmp_path):
    store = MemoryStore(db)
    injector = MemoryInjector(store)
    task = Task(prompt="x", project_root=tmp_path)
    block = injector.build(task)
    assert block.block == ""
    assert block.note_count == 0


def test_injector_truncates_to_budget(db, tmp_path):
    store = MemoryStore(db)
    project_id = str(tmp_path.resolve())
    for i in range(5):
        store.upsert(project_id, MemoryKind.NOTE, f"k{i}", "x" * 400)
    injector = MemoryInjector(store, budget_tokens=120)
    task = Task(prompt="x", project_root=tmp_path)
    block = injector.build(task)
    assert block.note_count < 5
    assert "truncated" in block.block or "omitted" in block.block


def test_injector_render_into_prompt_appends_block():
    block = MemoryBlock("Project context here", 1)
    out = MemoryInjector.render_into_prompt_static("user prompt", block)
    assert out.startswith("user prompt")
    assert "Project context" in out


def test_secrets_redacted_on_upsert(db):
    store = MemoryStore(db)
    store.upsert("p1", MemoryKind.GOTCHA, "k", "API_KEY=sk-abcdefghijklmnop1234")
    note = store.list("p1", kind=MemoryKind.GOTCHA)[0]
    assert "sk-abcdef" not in note.value
