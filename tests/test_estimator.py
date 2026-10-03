"""Calibration of the token estimator (debt plan P0).

The scenario these tests exist for: the heuristic once estimated 2,626 tokens
for a run that spent 115,924. Every quota decision — reservations, the veto,
`router plan` — rested on a number that was 44x wrong. The estimator must put
recorded history first and mark its provenance honestly.
"""

from __future__ import annotations

from coderouter.core.models import Complexity, Task
from coderouter.storage.db import Database
from coderouter.usage.estimator import TokenEstimator


def _task(**kw) -> Task:
    defaults = dict(prompt="add a subtract function to calc.py, mirroring add",
                    task_type="bug_fix", complexity=Complexity.MEDIUM)
    defaults.update(kw)
    return Task(**defaults)


def _seed_stats(db: Database, agent: str, task_type: str, complexity: str,
                runs: int, total_tokens: int) -> None:
    db.conn.execute(
        "INSERT INTO agent_stats (agent_id, task_type, complexity, successes, "
        "failures, total_tokens, total_duration_s) VALUES (?,?,?,?,?,?,?)",
        (agent, task_type, complexity, runs, 0, total_tokens, 1.0 * runs),
    )
    db.conn.commit()


def _seed_run_with_estimate(db: Database, task_id: str, agent: str,
                            estimated: int, actual: int) -> None:
    db.conn.execute(
        "INSERT INTO tasks (id, prompt, task_type, complexity, risk, state, created_at) "
        "VALUES (?,?,?,?,?, 'done', '2026-08-26T00:00:00+00:00')",
        (task_id, "p", "bug_fix", "medium", "low"),
    )
    db.conn.execute(
        "INSERT INTO routing_decisions (task_id, selected_agent, selected_model, mode, "
        "reason, confidence, estimated_tokens, alternatives, created_at) "
        "VALUES (?,?,?,?,?,?,?,?, '2026-08-26T00:00:00+00:00')",
        (task_id, agent, None, "auto", "r", 0.5, estimated, "[]"),
    )
    db.conn.execute(
        "INSERT INTO runs (id, task_id, agent_id, attempt, success, duration_s, "
        "input_tokens, output_tokens, created_at) "
        "VALUES (?,?,?, 1, 1, 1.0, ?, ?, '2026-08-26T00:00:00+00:00')",
        (f"run_{task_id}", task_id, agent, actual // 2, actual - actual // 2),
    )
    db.conn.commit()


async def test_observed_history_outranks_the_heuristic(adapters, db):
    est = TokenEstimator(db)
    adapter = adapters[0]
    heuristic = await adapter.estimate(_task())
    _seed_stats(db, "fake", "bug_fix", "medium", runs=10, total_tokens=500_000)

    estimate = await est.estimate(adapter, _task())

    # The heuristic said ~2-3k; fifty recorded runs say 50k.
    assert estimate.total_tokens == 50_000
    assert estimate.source == "observed"
    assert estimate.confidence == 0.9
    assert estimate.total_tokens > 3 * heuristic.total_tokens


async def test_thin_complexity_bucket_falls_back_to_the_task_type(adapters, db):
    est = TokenEstimator(db)
    adapter = adapters[0]
    # No "high" bucket, but the task type as a whole has history.
    _seed_stats(db, "fake", "bug_fix", "medium", runs=10, total_tokens=300_000)

    estimate = await est.estimate(adapter, _task(complexity=Complexity.HIGH))

    assert estimate.total_tokens == 30_000
    assert estimate.source == "observed"


async def test_cold_start_is_corrected_by_the_observed_ratio(adapters, db):
    est = TokenEstimator(db)
    adapter = adapters[0]
    # The run that motivated calibration: estimated 2,626, spent 115,924.
    _seed_run_with_estimate(db, "task_calib", "fake", estimated=2_626, actual=115_924)

    heuristic = await adapter.estimate(_task())
    estimate = await est.estimate(adapter, _task())

    assert estimate.source == "calibrated"
    ratio = estimate.total_tokens / heuristic.total_tokens
    # The correction factor is the observed gap (~44x), within rounding.
    assert 40 <= ratio <= 48
    assert estimate.confidence == 0.6


async def test_no_history_at_all_returns_the_raw_heuristic(adapters, db):
    est = TokenEstimator(db)
    adapter = adapters[0]

    heuristic = await adapter.estimate(_task())
    estimate = await est.estimate(adapter, _task())

    assert estimate.total_tokens == heuristic.total_tokens
    assert estimate.source == "heuristic"
    assert estimate.confidence == heuristic.confidence


async def test_observed_split_uses_the_recorded_input_output_ratio(adapters, db):
    est = TokenEstimator(db)
    adapter = adapters[0]
    _seed_stats(db, "fake", "bug_fix", "medium", runs=10, total_tokens=1_000_000)
    _seed_run_with_estimate(db, "task_split", "fake", estimated=1_000,
                            actual=100_000)  # 50k in / 50k out

    estimate = await est.estimate(adapter, _task())

    assert estimate.input_tokens == 50_000
    assert estimate.output_tokens == 50_000


async def test_regression_estimator_within_3x_of_recorded_history(adapters, db):
    """The regression test the debt plan demands: on recorded historical runs
    the estimator may not be more than ~3x off. Seed a spread of agents and
    task types with realistic agentic spend and check every one."""
    est = TokenEstimator(db)
    cases = [
        ("fake", "bug_fix", "medium", 12, 96_000),
        ("fake", "feature", "low", 8, 41_000),
        ("other", "bug_fix", "high", 20, 210_000),
        ("other", "documentation", "trivial", 30, 9_000),
    ]
    for agent, task_type, complexity, runs, total in cases:
        _seed_stats(db, agent, task_type, complexity, runs, total)

    adapter_by_id = {a.id: a for a in adapters}
    for agent, task_type, complexity, runs, total in cases:
        adapter = adapter_by_id[agent]
        task = _task(task_type=task_type,
                     complexity=Complexity(complexity))
        estimate = await est.estimate(adapter, task)
        actual_avg = total / runs
        assert estimate.total_tokens == actual_avg, (
            f"{agent}/{task_type}: estimated {estimate.total_tokens}, "
            f"recorded average {actual_avg}"
        )


async def test_routing_engine_uses_the_calibrated_estimate(adapters, db, config):
    """The decision recorded for `router plan` / `router explain` must carry
    the calibrated figure, not the raw heuristic."""
    from coderouter.agents.base.registry import AgentRegistry
    from coderouter.routing.engine import RoutingEngine
    from coderouter.usage.manager import UsageManager

    _seed_stats(db, "fake", "bug_fix", "medium", runs=10, total_tokens=500_000)
    registry = AgentRegistry.__new__(AgentRegistry)
    registry._adapters = {a.id: a for a in adapters}
    engine = RoutingEngine(config, registry, UsageManager(config, db), db)

    decision = await engine.decide(_task())

    assert decision.estimated_usage.source == "observed"
    assert decision.estimated_usage.total_tokens == 50_000
