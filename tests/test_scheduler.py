"""Tests for the scheduler subsystem."""

from __future__ import annotations

import asyncio

import pytest

from coderouter.core.models import AgentResult, Complexity, Task, TaskState
from coderouter.core.task_graph import TaskGraph
from coderouter.scheduler import (
    BatchingPolicy,
    SpeculativeConfig,
    is_speculative_eligible,
    race_attempts,
)
from coderouter.scheduler.policy import SchedulingPolicy


def test_batching_policy_returns_ready_tasks():
    a = Task(prompt="a")
    b = Task(prompt="b")
    c = Task(prompt="c", depends_on=[a.id])
    graph = TaskGraph([a, b, c])
    policy = BatchingPolicy()
    batch = policy.decide_batch(graph)
    assert {t.id for t in batch} == {a.id, b.id}


def test_batching_policy_respects_max_batch():
    tasks = [Task(prompt=str(i)) for i in range(5)]
    graph = TaskGraph(tasks)
    policy = BatchingPolicy(max_batch=2)
    batch = policy.decide_batch(graph)
    assert len(batch) == 2


def test_batching_policy_empty_when_finished():
    task = Task(prompt="x")
    graph = TaskGraph([task])
    task.state = TaskState.DONE
    assert BatchingPolicy().decide_batch(graph) == []


def test_speculative_eligibility_off_by_default():
    task = Task(prompt="x", complexity=Complexity.LOW)
    cfg = SpeculativeConfig()  # enabled=False by default
    assert not is_speculative_eligible(task, cfg)


def test_speculative_eligibility_for_low_when_enabled():
    task = Task(prompt="x", complexity=Complexity.LOW)
    cfg = SpeculativeConfig(enabled=True)
    assert is_speculative_eligible(task, cfg)


def test_speculative_ineligible_for_high_complexity():
    task = Task(prompt="x", complexity=Complexity.HIGH)
    cfg = SpeculativeConfig(enabled=True)
    assert not is_speculative_eligible(task, cfg)


def test_speculative_ineligible_for_critical():
    task = Task(prompt="x", complexity=Complexity.CRITICAL)
    cfg = SpeculativeConfig(enabled=True, max_complexity=Complexity.HIGH)
    assert not is_speculative_eligible(task, cfg)


def test_speculative_eligibility_caps_at_medium_by_default():
    task_medium = Task(prompt="x", complexity=Complexity.MEDIUM)
    cfg = SpeculativeConfig(enabled=True)
    assert is_speculative_eligible(task_medium, cfg)


async def test_race_attempts_returns_first_success():
    task = Task(prompt="x")

    class _Adapter:
        def __init__(self, aid: str, succeed: bool, delay: float = 0.0):
            self.id = aid
            self._succeed = succeed
            self._delay = delay

    async def runner(t: Task, adapter, model) -> AgentResult:
        await asyncio.sleep(adapter._delay)
        return AgentResult(
            task_id=t.id, agent_id=adapter.id, model=model,
            success=adapter._succeed, output="ok" if adapter._succeed else "fail",
            error=None if adapter._succeed else "nope",
        )

    a = _Adapter("a", succeed=False, delay=0.0)
    b = _Adapter("b", succeed=True, delay=0.05)
    result = await race_attempts(task, [(a, None), (b, None)], runner)
    assert result is not None
    assert result.agent_id == "b"
    assert result.success


async def test_race_attempts_returns_last_failure_when_all_fail():
    task = Task(prompt="x")

    class _Adapter:
        def __init__(self, aid: str):
            self.id = aid

    async def runner(t: Task, adapter, model) -> AgentResult:
        await asyncio.sleep(0.01)
        return AgentResult(
            task_id=t.id, agent_id=adapter.id, model=model,
            success=False, output="", error="nope",
        )

    a = _Adapter("a")
    b = _Adapter("b")
    result = await race_attempts(task, [(a, None), (b, None)], runner)
    assert result is not None
    assert not result.success


async def test_race_attempts_empty_candidates():
    task = Task(prompt="x")
    async def runner(t, a, m):  # pragma: no cover - never called
        raise AssertionError
    assert await race_attempts(task, [], runner) is None


async def test_scheduling_policy_is_abstract():
    """Future strategies must subclass SchedulingPolicy."""
    with pytest.raises(TypeError):
        SchedulingPolicy()  # type: ignore[abstract]
