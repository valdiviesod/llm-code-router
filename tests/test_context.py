from coderouter.config import TokenSavingConfig
from coderouter.context.manager import ContextManager
from coderouter.core.models import Task


def _project(tmp_path):
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "login.py").write_text("def login(user): return user")
    (tmp_path / "unrelated.py").write_text("x = 1")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "auth.py").write_text("noise")
    return tmp_path


def test_selects_relevant_files_and_skips_vendor_dirs(tmp_path):
    root = _project(tmp_path)
    manager = ContextManager(TokenSavingConfig())
    bundle = manager.select(Task(prompt="fix the login auth flow", project_root=root))
    names = {p.name for p in bundle.files}
    assert "login.py" in names
    assert not any("node_modules" in str(p) for p in bundle.files)


def test_fingerprint_is_stable_and_content_sensitive(tmp_path):
    root = _project(tmp_path)
    manager = ContextManager(TokenSavingConfig())
    task = Task(prompt="fix the login auth flow", project_root=root)
    first = manager.select(task).fingerprint
    assert first == manager.select(task).fingerprint
    target = root / "auth" / "login.py"
    target.write_text("def login(user): return user  # changed, different size")
    assert manager.select(task).fingerprint != first


def test_disabled_token_saving_selects_nothing(tmp_path):
    manager = ContextManager(TokenSavingConfig(enabled=False))
    assert manager.select(Task(prompt="anything", project_root=tmp_path)).files == []


def test_summarize_keeps_head_and_tail():
    manager = ContextManager(TokenSavingConfig())
    text = "START" + "x" * 5000 + "END"
    summary = manager.summarize_output(text, max_chars=200)
    assert summary.startswith("START")
    assert summary.endswith("END")
    assert len(summary) < len(text)
