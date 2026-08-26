"""Skill registry.

Mirrors the `AgentRegistry` shape so the orchestrator learns one discovery
pattern. A `SkillRegistry` is built from a list of search paths and
(optionally) a per-agent allowlist/denylist. It exposes `for_task()`,
which selects the skills that apply to a given task type and have the
capabilities the agent actually has.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.models import Capability
from .loader import load_skills_from_paths
from .models import Skill


@dataclass(slots=True)
class SkillConfig:
    """Per-agent or per-run allow/deny rules.

    An empty allowlist means "any loaded skill that applies" — the default
    for a brand-new install. An empty denylist means nothing is blocked.
    Both are name sets; matching is exact and case-sensitive.
    """

    allowlist: frozenset[str] = frozenset()
    denylist: frozenset[str] = frozenset()


@dataclass(slots=True)
class SkillRegistry:
    skills: list[Skill] = field(default_factory=list)

    def __iter__(self):
        return iter(self.skills)

    def __len__(self) -> int:
        return len(self.skills)

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.skills]

    def get(self, name: str) -> Skill | None:
        for s in self.skills:
            if s.name == name:
                return s
        return None

    def for_task(
        self,
        task_type: str,
        agent_capabilities: frozenset[Capability],
        config: SkillConfig | None = None,
    ) -> list[Skill]:
        """Return skills that apply to `task_type` and the agent's capabilities.

        The result is ordered by descending priority and then name, so
        `SkillInjector` can truncate cheaply from the tail.
        """
        config = config or SkillConfig()
        applicable: list[Skill] = []
        for skill in self.skills:
            if config.denylist and skill.name in config.denylist:
                continue
            if config.allowlist and skill.name not in config.allowlist:
                continue
            if not skill.is_applicable(task_type, agent_capabilities):
                continue
            applicable.append(skill)
        applicable.sort(key=lambda s: (-s.priority, s.name))
        return applicable

    @classmethod
    def from_paths(cls, paths: list[Path]) -> SkillRegistry:
        return cls(skills=load_skills_from_paths(paths))
