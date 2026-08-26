"""Skill domain types.

A Skill is a piece of reusable, project-scoped knowledge that the router
injects into a task's prompt. It mirrors the `SKILL.md` convention popular
across coding-agentic runtimes (Claude Code, OpenCode, Codex) and adds typed
metadata so the router can reason about cost and applicability rather than
blindly concatenating text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import Capability


@dataclass(slots=True)
class Skill:
    """A loadable skill.

    `name` is the stable identifier; the loader enforces uniqueness within
    a discovery run. `body` is the Markdown body of the SKILL.md after the
    YAML front-matter. `token_cost` is the router's own estimate of how many
    input tokens the body will cost the model — used by the injector to
    stay within the per-task budget.
    """

    name: str
    description: str = ""
    body: str = ""
    source: Path | None = None
    applies_to: frozenset[str] = frozenset()
    required_capabilities: frozenset[Capability] = frozenset()
    priority: int = 0
    token_cost: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_applicable(self, task_type: str, capabilities: frozenset[Capability]) -> bool:
        """A skill applies if it has no type filter or its filter matches, and
        none of its required capabilities are missing from the agent.
        """
        if self.applies_to and task_type not in self.applies_to:
            return False
        return self.required_capabilities.issubset(capabilities)
