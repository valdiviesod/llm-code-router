"""Tests for the skills subsystem."""

from __future__ import annotations

from pathlib import Path

from coderouter.core.models import Capability, Task
from coderouter.skills import (
    Skill,
    SkillBlock,
    SkillConfig,
    SkillInjector,
    SkillRegistry,
    load_skills_from_paths,
)


def _write_skill(path: Path, name: str, body: str, **front) -> None:
    """Helper: write a SKILL.md file with the given front-matter and body."""
    path.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    lines.append(f"name: {name}")
    for key, value in front.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    (path / "SKILL.md").write_text("\n".join(lines))


def test_loads_skill_with_front_matter(tmp_path: Path):
    _write_skill(tmp_path / "python-style", "python-style",
                 "Always use ruff.\nLine length 100.", priority=10)
    skills = load_skills_from_paths([tmp_path])
    assert len(skills) == 1
    s = skills[0]
    assert s.name == "python-style"
    assert "ruff" in s.body
    assert s.priority == 10
    assert s.token_cost > 0


def test_invalid_yaml_does_not_raise(tmp_path: Path):
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nthis: is: not: valid: yaml\n---\nbody\n")
    skills = load_skills_from_paths([tmp_path])
    # A broken front-matter is best-effort: the body is still picked up,
    # metadata is empty, and a warning was logged. The loader never raises.
    assert len(skills) == 1
    assert skills[0].body == "body"
    assert skills[0].metadata == {}


def test_project_skill_overrides_global(tmp_path: Path):
    proj = tmp_path / "project"
    proj.mkdir()
    _write_skill(proj / "foo", "foo", "project body")
    user = tmp_path / "user"
    user.mkdir()
    _write_skill(user / "foo", "foo", "user body")
    skills = load_skills_from_paths([proj, user])
    assert len(skills) == 1
    assert skills[0].body == "project body"


def test_registry_filters_by_task_type_and_capabilities():
    s_a = Skill(name="a", applies_to=frozenset({"database"}),
                required_capabilities=frozenset({Capability.CODE_EDIT}), priority=5)
    s_b = Skill(name="b", applies_to=frozenset({"refactor"}),
                required_capabilities=frozenset({Capability.DEEP_REASONING}), priority=10)
    s_c = Skill(name="c")  # no type filter
    reg = SkillRegistry(skills=[s_a, s_b, s_c])
    out = reg.for_task("database", frozenset({Capability.CODE_EDIT}))
    assert {s.name for s in out} == {"a", "c"}
    # c has no required capabilities so it appears regardless
    out2 = reg.for_task("anything", frozenset())
    assert {s.name for s in out2} == {"c"}


def test_registry_applies_allow_and_deny_lists():
    s_a = Skill(name="a", priority=1)
    s_b = Skill(name="b", priority=2)
    reg = SkillRegistry(skills=[s_a, s_b])
    cfg = SkillConfig(denylist=frozenset({"b"}))
    out = reg.for_task("general", frozenset(), config=cfg)
    assert [s.name for s in out] == ["a"]
    cfg2 = SkillConfig(allowlist=frozenset({"b"}))
    out2 = reg.for_task("general", frozenset(), config=cfg2)
    assert [s.name for s in out2] == ["b"]


def test_injector_respects_token_budget():
    s1 = Skill(name="s1", body="x" * 400)  # ~100 tokens
    s2 = Skill(name="s2", body="y" * 400)
    s3 = Skill(name="s3", body="z" * 400)
    reg = SkillRegistry(skills=[s1, s2, s3])
    injector = SkillInjector(budget_tokens=150)
    task = Task(prompt="do thing", task_type="general")
    block = injector.build(task, reg, frozenset())
    assert isinstance(block, SkillBlock)
    # s1 (~100) + s2 (~100) would already blow the budget, so we expect s1 + a
    # truncated s2 (or just s1) but never s3.
    assert "s3" not in block.skill_ids
    # The rendered block opens and closes with sentinels.
    if block.block:
        assert SkillInjector.OPEN in block.block
        assert SkillInjector.CLOSE in block.block


def test_injector_returns_empty_for_no_match():
    s = Skill(name="a", applies_to=frozenset({"database"}))
    reg = SkillRegistry(skills=[s])
    injector = SkillInjector()
    task = Task(prompt="x", task_type="refactor")
    block = injector.build(task, reg, frozenset())
    assert block.block == ""
    assert block.skill_ids == []


def test_injector_render_into_prompt_appends_block():
    s = Skill(name="s", body="hello")
    reg = SkillRegistry(skills=[s])
    injector = SkillInjector()
    task = Task(prompt="user prompt", task_type="general")
    block = injector.build(task, reg, frozenset())
    rendered = injector.render_into_prompt("user prompt", block)
    assert rendered.startswith("user prompt")
    if block.block:
        assert "hello" in rendered


def test_skill_is_applicable_respects_capabilities():
    s = Skill(name="x", required_capabilities=frozenset({Capability.MCP}))
    assert s.is_applicable("general", frozenset({Capability.MCP, Capability.CODE_EDIT}))
    assert not s.is_applicable("general", frozenset({Capability.CODE_EDIT}))
    s2 = Skill(name="y", applies_to=frozenset({"security"}))
    assert s2.is_applicable("security", frozenset())
    assert not s2.is_applicable("database", frozenset())


def test_load_from_paths_skips_missing_dirs(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    assert load_skills_from_paths([missing]) == []
