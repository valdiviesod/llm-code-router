import subprocess

from v4ld1.validation.engine import ValidationEngine, detect_stacks


def test_detects_python_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert [s.name for s in detect_stacks(tmp_path)] == ["python"]


def test_detects_nothing_for_an_empty_dir(tmp_path):
    assert detect_stacks(tmp_path) == []


def test_detects_multiple_stacks(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "go.mod").write_text("module x")
    assert {s.name for s in detect_stacks(tmp_path)} == {"node", "go"}


async def test_no_checks_run_for_unknown_stack(tmp_path):
    assert await ValidationEngine().run(tmp_path) == []


async def test_git_diff_reports_changed_files(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("one")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("two")
    _, files = await ValidationEngine().git_diff(tmp_path)
    assert files == ["a.txt"]


async def test_git_diff_on_non_repo_is_empty(tmp_path):
    assert await ValidationEngine().git_diff(tmp_path) == ("", [])
