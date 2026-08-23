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
