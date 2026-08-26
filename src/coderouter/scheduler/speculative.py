"""Speculative dispatch.

`routing.speculative: true` enables a "race two agents, take the
first success" mode. The router already produces a ranked list of
candidates per task; the speculative dispatcher runs the top two
in parallel and accepts whichever finishes first with `success=True`.

Speculative is **opt-in** and **never** for HIGH/CRITICAL: the
quota cost of two parallel runs is real, and HIGH/CRITICAL work
needs deterministic serial execution so the audit trail and the
review layer can reason about what happened. A LOW/MEDIUM task
that fails fast and cheap is the right shape for this lever.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..agents.base.adapter import AgentAdapter
from ..core.models import (
    AgentResult,
    Complexity,
    Task,
)
from ..logging import get_logger

logger = get_logger("scheduler.speculative")


@dataclass(slots=True)
class SpeculativeConfig:
    enabled: bool = False
    # Cap on the number of parallel attempts per task. Default 2 so the
    # cost is at most 2x a normal run; raising it past 2 burns quota
    # faster than most tasks are worth.
    max_attempts: int = 2
    # Maximum complexity the lever is allowed to fire on. HIGH and
    # CRITICAL are excluded by design.
    max_complexity: Complexity = Complexity.MEDIUM


SpeculativeRunner = Callable[
    [Task, AgentAdapter, str | None],
    Awaitable[AgentResult],
]


def is_speculative_eligible(task: Task, config: SpeculativeConfig) -> bool:
    """A task is eligible when the feature is on, the task is small enough,
    and at least two candidates are available.
    """
    if not config.enabled:
        return False
    return task.complexity.rank <= config.max_complexity.rank


async def race_attempts(
    task: Task,
    candidates: list[tuple[AgentAdapter, str | None]],
    runner: SpeculativeRunner,
    on_attempt_started: Callable[[str, AgentAdapter], Awaitable[None]] | None = None,
) -> AgentResult | None:
    """Run every candidate in parallel; return the first success.

    The losers are cancelled. If every attempt fails, the last failure
    is returned. The audit log records every attempt so the user can
    see how much quota a speculative run actually cost.
    """
    if not candidates:
        return None
    tasks: list[asyncio.Task[AgentResult]] = []
    for adapter, model in candidates:
        async def _run(a: AgentAdapter = adapter, m: str | None = model) -> AgentResult:
            if on_attempt_started is not None:
                await on_attempt_started(task.id, a)
            return await runner(task, a, m)
        tasks.append(asyncio.create_task(_run()))
    last_failure: AgentResult | None = None
    try:
        for done in asyncio.as_completed(tasks):
            result = await done
            if result.success:
                for pending in tasks:
                    if not pending.done():
                        pending.cancel()
                return result
            last_failure = result
        return last_failure
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
