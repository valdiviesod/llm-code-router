"""Skill discovery from disk.

Skills live as `SKILL.md` files with a YAML front-matter block followed by
a Markdown body. The loader walks configured search paths, parses each
file, and returns a list of `Skill` objects. A missing or malformed file
is logged and skipped, never raised — discovery is best-effort and the
orchestrator must not crash because one skill file is broken.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import yaml

from ..core.models import Capability
from ..logging import get_logger
from .models import Skill

logger = get_logger("skills.loader")

# The front-matter is delimited by a `---` line at the very top of the file
# and another `---` line before the body. Empty lines around the body are
# tolerated; the body is taken verbatim from after the closing fence.
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _split_front_matter(text: str) -> tuple[dict, str]:
    match = _FRONT_MATTER_RE.match(text)
    if match is None:
        return {}, text.strip()
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        logger.warning("invalid YAML in skill front-matter: %s", exc)
        return {}, match.group(2).strip()
    if not isinstance(meta, dict):
        logger.warning("skill front-matter is not a mapping; ignoring")
        return {}, match.group(2).strip()
    return meta, match.group(2).strip()


def _approx_tokens(text: str) -> int:
    """Rough body token estimate. The same heuristic the context manager uses,
    so the rest of the router cannot accidentally double-budget the same body.
    """
    return max(len(text) // 4, 1)


def _parse_capabilities(raw: object) -> frozenset[Capability]:
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    out: set[Capability] = set()
    for value in raw:
        try:
            out.add(Capability(str(value).strip().lower()))
        except ValueError:
            continue
    return frozenset(out)


def _parse_skill_file(path: Path) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("cannot read skill %s: %s", path, exc)
        return None
    meta, body = _split_front_matter(text)
    name = str(meta.get("name") or path.parent.name).strip()
    if not name:
        logger.warning("skill at %s has no name; skipping", path)
        return None
    return Skill(
        name=name,
        description=str(meta.get("description", "")).strip(),
        body=body,
        source=path,
        applies_to=frozenset(str(t).strip() for t in (meta.get("applies_to") or ())),
        required_capabilities=_parse_capabilities(meta.get("required_capabilities")),
        priority=int(meta.get("priority", 0) or 0),
        token_cost=_approx_tokens(body),
        metadata={k: v for k, v in meta.items() if k not in {
            "name", "description", "applies_to", "required_capabilities",
            "priority", "token_cost",
        }},
    )


def load_skills_from_paths(paths: Iterable[Path]) -> list[Skill]:
    """Discover every `SKILL.md` under the given search roots, deduped by name.

    The first occurrence wins, so a project-level skill overrides a global
    one with the same name. This is deliberate: a project owns its own
    conventions.
    """
    found: dict[str, Skill] = {}
    for root in paths:
        if not root.exists():
            continue
        for path in sorted(root.rglob("SKILL.md")):
            if not path.is_file():
                continue
            skill = _parse_skill_file(path)
            if skill is None:
                continue
            if skill.name in found:
                logger.debug("skill %r already loaded; %s overridden by %s",
                             skill.name, found[skill.name].source, skill.source)
                continue
            found[skill.name] = skill
    return sorted(found.values(), key=lambda s: (-s.priority, s.name))
