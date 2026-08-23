import subprocess

import pytest

from v4ld1.git.worktree import WorktreeManager, is_git_repo


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("one")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


async def test_isolated_worktree_is_separate_and_cleaned_up(repo):
    manager = WorktreeManager(repo)
    async with manager.isolated("task1", "claude") as wt:
        assert wt is not None
        assert wt.path.exists() and wt.path != repo
        (wt.path / "a.txt").write_text("changed in isolation")
        assert (repo / "a.txt").read_text() == "one", "main tree must be untouched"
    assert not wt.path.exists()


async def test_two_agents_get_different_trees(repo):
    manager = WorktreeManager(repo)
    async with manager.isolated("task1", "claude") as a:
        async with manager.isolated("task1", "antigravity") as b:
            assert a.path != b.path
            assert a.branch != b.branch


async def test_non_repo_yields_none(tmp_path):
    assert not await is_git_repo(tmp_path)
    async with WorktreeManager(tmp_path).isolated("t", "a") as wt:
        assert wt is None
