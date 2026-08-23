import pytest

from v4ld1.config import SecurityConfig
from v4ld1.logging import JsonFormatter
from v4ld1.security.policy import CommandPolicy, Decision, redact_secrets


@pytest.fixture
def policy():
    return CommandPolicy(SecurityConfig())


@pytest.mark.parametrize("command", ["rm -rf /", "DROP DATABASE app;", "mkfs.ext4 /dev/sda"])
def test_destructive_commands_are_blocked(policy, command):
    assert policy.classify(command)[0] is Decision.BLOCK


@pytest.mark.parametrize("command", ["git push origin main", "sudo apt install x"])
def test_risky_commands_require_approval(policy, command):
    assert policy.classify(command)[0] is Decision.ASK


@pytest.mark.parametrize("command", ["ls -la", "pytest -q", "git status"])
def test_ordinary_commands_are_safe(policy, command):
    assert policy.classify(command)[0] is Decision.SAFE


def test_secrets_are_redacted():
    text = "export API_KEY=abcdef1234567890 and token: ghp_abcdefghijklmnopqrst12"
    cleaned = redact_secrets(text)
    assert "abcdef1234567890" not in cleaned
    assert "ghp_abcdefghijklmnopqrst12" not in cleaned


def test_log_formatter_redacts_secret_fields():
    import logging
    record = logging.LogRecord("v4ld1", logging.INFO, __file__, 1, "msg", None, None)
    record.fields = {"api_key": "supersecret", "agent": "claude"}
    output = JsonFormatter().format(record)
    assert "supersecret" not in output
    assert "claude" in output
