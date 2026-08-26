"""Doctor must report what it actually checked, and only that."""

from __future__ import annotations

import subprocess

import pytest

from coderouter.core.doctor import run_doctor


def _by_name(findings):
    return {f.name: f for f in findings}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "a.txt").write_text("one")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


async def test_doctor_probes_worktree_isolation_for_real(config, repo):
    findings = _by_name(await run_doctor(config, repo))
    assert findings["Worktree isolation"].ok
    assert findings["Worktree leftovers"].ok
    assert findings["Unmerged attempt branches"].ok


async def test_doctor_flags_a_repo_with_no_commits(config, tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    findings = _by_name(await run_doctor(config, root))
    assert not findings["Worktree isolation"].ok
    assert "no commits" in findings["Worktree isolation"].detail


async def test_doctor_flags_leftover_attempt_branches(config, repo):
    subprocess.run(["git", "branch", "coderouter/task1-claude"], cwd=repo, check=True)
    findings = _by_name(await run_doctor(config, repo))
    assert not findings["Unmerged attempt branches"].ok
    assert "coderouter/task1-claude" in findings["Unmerged attempt branches"].detail


async def test_doctor_reports_schema_version(config, repo):
    from coderouter.storage.db import SCHEMA_VERSION

    findings = _by_name(await run_doctor(config, repo))
    assert findings["Schema version"].ok
    assert str(SCHEMA_VERSION) in findings["Schema version"].detail


async def test_doctor_covers_skills_tools_and_mcp(config, repo):
    findings = _by_name(await run_doctor(config, repo))
    assert {"Skills", "Tools", "Command policy", "MCP servers"} <= set(findings)
    assert findings["Command policy"].ok, "default config ships block patterns"


async def test_doctor_skips_worktree_checks_outside_a_repo(config, tmp_path):
    findings = _by_name(await run_doctor(config, tmp_path))
    assert "Worktree isolation" not in findings
    assert not findings["Project"].ok
