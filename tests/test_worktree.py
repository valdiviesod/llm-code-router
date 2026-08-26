import subprocess

import pytest

from coderouter.git.worktree import WorktreeManager, is_git_repo


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "a.txt").write_text("one")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return path


@pytest.fixture
def project(tmp_path):
    """A repo that is *not* the coderouter data dir.

    The `config` fixture points `data_dir` at `tmp_path`, so a repo rooted
    there would see coderouter's own SQLite file as an uncommitted change and
    every merge would be refused.
    """
    return _init_repo(tmp_path / "project")


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


def _branches(repo) -> list[str]:
    out = subprocess.run(["git", "branch", "--format=%(refname:short)"],
                         cwd=repo, capture_output=True, text=True, check=True)
    return out.stdout.split()


async def test_integrate_merges_the_work_back(repo):
    """Regression: `isolated` used to remove the worktree with --force and
    nothing ever merged, so every isolated run silently destroyed its output."""
    manager = WorktreeManager(repo)
    async with manager.isolated("task1", "claude") as wt:
        (wt.path / "a.txt").write_text("changed in isolation")
        (wt.path / "new.txt").write_text("brand new")
        integration = await manager.integrate(wt, "test: agent work")

    assert integration.merged, integration.detail
    assert sorted(integration.files) == ["a.txt", "new.txt"]
    assert (repo / "a.txt").read_text() == "changed in isolation"
    assert (repo / "new.txt").read_text() == "brand new"


async def test_cleanup_deletes_the_attempt_branch(repo):
    """Regression: one leaked branch per attempt, kept forever."""
    before = _branches(repo)
    manager = WorktreeManager(repo)
    async with manager.isolated("task1", "claude") as wt:
        (wt.path / "a.txt").write_text("discarded work")
    assert _branches(repo) == before


async def test_discarded_work_does_not_reach_the_project(repo):
    manager = WorktreeManager(repo)
    async with manager.isolated("task1", "claude") as wt:
        (wt.path / "a.txt").write_text("never integrated")
    assert (repo / "a.txt").read_text() == "one"


async def test_no_changes_is_reported_not_merged(repo):
    manager = WorktreeManager(repo)
    async with manager.isolated("task1", "claude") as wt:
        integration = await manager.integrate(wt, "test: nothing")
    assert not integration.merged
    assert not integration.had_changes


async def test_conflicting_merge_aborts_and_keeps_the_branch(repo):
    """A conflict must leave the project tree untouched and the work
    recoverable — the branch is the only copy of it."""
    manager = WorktreeManager(repo)
    async with manager.isolated("task1", "claude") as wt:
        (wt.path / "a.txt").write_text("agent version")
        # Meanwhile the project moves on with a conflicting commit.
        (repo / "a.txt").write_text("human version")
        subprocess.run(["git", "commit", "-aqm", "human edit"], cwd=repo, check=True)
        integration = await manager.integrate(wt, "test: conflict")
        if integration.conflicted:
            manager.keep_branch(wt)

    assert integration.conflicted
    assert not integration.merged
    assert (repo / "a.txt").read_text() == "human version", "root must be restored"
    assert integration.branch in _branches(repo), "work must stay recoverable"


async def test_dirty_project_tree_is_refused_not_half_merged(repo):
    manager = WorktreeManager(repo)
    (repo / "wip.txt").write_text("human work in progress")
    async with manager.isolated("task1", "claude") as wt:
        (wt.path / "new.txt").write_text("agent work")
        integration = await manager.integrate(wt, "test: dirty root")
        if integration.conflicted:
            manager.keep_branch(wt)
    assert not integration.merged
    assert integration.conflicted
    assert (repo / "wip.txt").read_text() == "human work in progress"


async def test_repo_without_commits_yields_none(tmp_path):
    """`git worktree add` needs a commit to branch from; that must degrade,
    not raise."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    async with WorktreeManager(tmp_path).isolated("t", "a") as wt:
        assert wt is None


async def test_isolated_orchestrator_run_lands_its_changes_in_the_project(project, config, db):
    """End-to-end regression: an isolated run used to write into a worktree
    that was then removed with --force, so the work never reached the project."""
    from conftest import FakeAdapter, FakeRegistry

    from coderouter.core.models import Task
    from coderouter.core.orchestrator import Orchestrator

    class WritingAdapter(FakeAdapter):
        async def execute(self, task, *, model=None, on_event=None):
            (task.project_root / "generated.py").write_text("def hello(): ...\n")
            return await super().execute(task, model=model, on_event=on_event)

    adapters = [WritingAdapter(config, "fake"), WritingAdapter(config, "other")]
    orch = Orchestrator(config, db, FakeRegistry(adapters))

    outcome = await orch.run_task(Task(prompt="write a helper", project_root=project),
                                  isolate=True)

    assert outcome.result.success
    assert (project / "generated.py").read_text() == "def hello(): ...\n"
    assert "generated.py" in outcome.result.files_changed
    assert _branches(project) == ["main"] or "coderouter" not in " ".join(_branches(project))


async def test_failed_isolated_run_leaves_the_project_untouched(project, config, db):
    from conftest import FakeAdapter, FakeRegistry

    from coderouter.core.models import Task
    from coderouter.core.orchestrator import Orchestrator

    class WritingAdapter(FakeAdapter):
        async def execute(self, task, *, model=None, on_event=None):
            (task.project_root / "garbage.py").write_text("syntax error(((\n")
            return await super().execute(task, model=model, on_event=on_event)

    adapters = [WritingAdapter(config, "fake", succeed=False),
                WritingAdapter(config, "other", succeed=False)]
    orch = Orchestrator(config, db, FakeRegistry(adapters))

    outcome = await orch.run_task(Task(prompt="break things", project_root=project),
                                  isolate=True)

    assert not outcome.result.success
    assert not (project / "garbage.py").exists(), "a failed attempt must not be merged"
