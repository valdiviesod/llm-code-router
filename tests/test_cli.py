import pytest

from coderouter.cli import build_parser, main, run_parser


def test_subcommand_parses():
    args = build_parser().parse_args(["doctor"])
    assert args.command == "doctor"


def test_free_text_prompt_is_not_treated_as_a_subcommand():
    args = run_parser().parse_args(["implement JWT auth"])
    assert args.prompt == "implement JWT auth"
    assert args.command is None


def test_mode_and_agent_overrides_parse():
    args = run_parser().parse_args(["--mode", "economy", "--agent", "claude", "do it"])
    assert args.mode == "economy" and args.agent == "claude"


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_config_init_and_show(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    assert main(["--config", str(path), "config", "--init"]) == 0
    assert path.exists()
    assert main(["--config", str(path), "config"]) == 0
    assert "mode: auto" in capsys.readouterr().out


@pytest.mark.parametrize("command", [
    "quota", "models", "skills", "tools", "mcp", "history", "explain", "plan",
])
def test_every_documented_subcommand_is_wired(command):
    """A subcommand in the parser with no handler falls through to `run` and
    is executed as a prompt — silently spending quota on the word "quota"."""
    from coderouter.cli import COMMANDS

    assert command in COMMANDS


def test_history_limit_option_is_not_mistaken_for_a_subcommand():
    from coderouter.cli import _first_positional

    assert _first_positional(["history", "-n", "5"]) == "history"
    assert _first_positional(["--limit", "5", "history"]) == "history"


def test_explain_requires_a_task_id():
    args = build_parser().parse_args(["explain", "task_abc"])
    assert args.command == "explain" and args.task_id == "task_abc"


def test_plan_accepts_the_same_overrides_as_run():
    args = build_parser().parse_args(["plan", "--mode", "quality", "--agent",
                                      "claude", "do the thing"])
    assert args.command == "plan"
    assert args.prompt == "do the thing"
    assert args.mode == "quality" and args.agent == "claude"


def test_explain_reports_an_unknown_task(tmp_path, capsys):
    cfg = tmp_path / "config.yaml"
    assert main(["--config", str(cfg), "config", "--init"]) == 0
    assert main(["--config", str(cfg), "explain", "task_does_not_exist"]) == 1
    assert "no such task" in capsys.readouterr().err
