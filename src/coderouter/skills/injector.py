"""Skill injection.

Builds the "Skills" block that gets appended to a task's prompt. The
injector is budget-aware: a single `Skill` is large or there are many of
them, it picks the highest-priority ones and truncates the body of the
last included skill to fit.

The block is marked with sentinel delimiters so the agent can tell the
router's instructions apart from the user's prompt — and so the output
can be regenerated without ambiguity.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import Capability, Task
from .registry import SkillConfig, SkillRegistry


@dataclass(slots=True)
class SkillBlock:
    """The rendered skills block plus the list of skill ids that went into it.

    The ids are returned so the orchestrator can record them on the `Task`
    for tracing and learning.
    """

    block: str
    skill_ids: list[str]


class SkillInjector:
    """Compose a skills block under a token budget.

    `budget_tokens` is the per-task ceiling; default 800. The injector
    iterates the registry's applicable skills in priority order, taking
    each in full until adding the next would exceed the budget. The
    last included skill is truncated if needed.
    """

    OPEN = "<!-- router:skills:start -->"
    CLOSE = "<!-- router:skills:end -->"

    def __init__(self, budget_tokens: int = 800) -> None:
        self.budget_tokens = budget_tokens

    def build(
        self,
        task: Task,
        registry: SkillRegistry,
        agent_capabilities: frozenset[Capability],
        config: SkillConfig | None = None,
    ) -> SkillBlock:
        candidates = registry.for_task(task.task_type, agent_capabilities, config)
        if not candidates:
            return SkillBlock("", [])
        used = 0
        picked: list[tuple[str, str]] = []
        for skill in candidates:
            cost = skill.token_cost or max(len(skill.body) // 4, 1)
            if used + cost <= self.budget_tokens:
                picked.append((skill.name, skill.body))
                used += cost
                continue
            remaining = self.budget_tokens - used
            if remaining <= 32:
                break
            truncated = self._truncate(skill.body, remaining)
            picked.append((skill.name, truncated))
            used += max(len(truncated) // 4, 1)
            break
        if not picked:
            return SkillBlock("", [])
        sections = [f"### {name}\n\n{body.rstrip()}" for name, body in picked]
        block = (
            f"{self.OPEN}\n"
            "The following project conventions and gotchas apply:\n\n"
            + "\n\n".join(sections)
            + f"\n{self.CLOSE}"
        )
        return SkillBlock(block, [name for name, _ in picked])

    def render_into_prompt(self, prompt: str, block: SkillBlock) -> str:
        """Append the skills block to a prompt. Empty block is a no-op."""
        if not block.block:
            return prompt
        return f"{prompt}\n\n{block.block}"

    def _truncate(self, body: str, max_tokens: int) -> str:
        max_chars = max_tokens * 4
        if len(body) <= max_chars:
            return body
        head = body[: max_chars // 2]
        tail = body[-max_chars // 2 :]
        return f"{head}\n…[skill body truncated]…\n{tail}"
