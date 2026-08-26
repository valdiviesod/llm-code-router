"""Tool selector.

A thin wrapper over `ToolRegistry.select` that is the public entry
point the orchestrator calls. Kept separate so future strategies
(LLM-based selection, project heuristics) can replace it without
touching the registry.
"""

from __future__ import annotations

from ..agents.base.adapter import AgentAdapter
from ..core.models import Task
from .models import Tool
from .registry import ToolRegistry


class ToolSelector:
    def __init__(self, registry: ToolRegistry, default_budget_tokens: int = 600) -> None:
        self._registry = registry
        self._default_budget = default_budget_tokens

    def select(
        self,
        adapter: AgentAdapter,
        task: Task,
        budget_tokens: int | None = None,
        *,
        discovered: list[Tool] | None = None,
    ) -> list[Tool]:
        budget = budget_tokens if budget_tokens is not None else self._default_budget
        return self._registry.select(adapter, task, budget, discovered=discovered)
