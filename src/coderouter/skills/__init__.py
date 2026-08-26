"""Skills subsystem.

A skill is a named, project-scoped piece of guidance the router can
attach to a task. Discovery walks the configured search paths and
parses `SKILL.md` files with a YAML front-matter; the registry dedupes
by name (project overrides global) and the injector builds a budgeted
block that goes onto the task prompt.
"""

from .injector import SkillBlock, SkillInjector
from .loader import load_skills_from_paths
from .models import Skill
from .registry import SkillConfig, SkillRegistry

__all__ = [
    "Skill",
    "SkillBlock",
    "SkillConfig",
    "SkillInjector",
    "SkillRegistry",
    "load_skills_from_paths",
]
