import pytest

from coderouter.config import load_config, write_default_config
from coderouter.core.models import RoutingMode
from coderouter.errors import ConfigError


def test_missing_config_yields_defaults(tmp_path):
    cfg = load_config(tmp_path / "absent.yaml")
    assert cfg.mode is RoutingMode.AUTO
    assert cfg.concurrency.globally == 2


def test_default_config_roundtrips(tmp_path):
    path = write_default_config(tmp_path / "config.yaml")
    cfg = load_config(path)
    assert cfg.agents["claude"].command == "claude"
    assert cfg.agents["antigravity"].command == "agy"
    # Limits are plan-specific and must stay unknown unless the user sets them.
    assert cfg.agents["claude"].window_limit_tokens is None


def test_global_concurrency_alias(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("concurrency:\n  global: 5\n")
    assert load_config(path).concurrency.globally == 5


@pytest.mark.parametrize("body", [
    "nonsense_key: 1",
    "mode: turbo",
    "routing:\n  bogus: true",
])
def test_invalid_config_raises(tmp_path, body):
    path = tmp_path / "c.yaml"
    path.write_text(body)
    with pytest.raises(ConfigError):
        load_config(path)
