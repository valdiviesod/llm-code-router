import pytest

from coderouter.config import SecurityConfig
from coderouter.logging import JsonFormatter
from coderouter.security.policy import CommandPolicy, Decision, redact_secrets


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
    text = "export API_KEY=abcdef1234567890 and token: " + "gh" + "p_" + "b" * 32
    cleaned = redact_secrets(text)
    assert "abcdef1234567890" not in cleaned
    assert "gh" + "p_" + "b" * 32 not in cleaned


def test_log_formatter_redacts_secret_fields():
    import logging
    record = logging.LogRecord("coderouter", logging.INFO, __file__, 1, "msg", None, None)
    record.fields = {"api_key": "supersecret", "agent": "claude"}
    output = JsonFormatter().format(record)
    assert "supersecret" not in output
    assert "claude" in output


# Assembled at runtime rather than written as literals. These are synthetic —
# no live credential is involved — but they match the real shapes closely
# enough that a literal here trips GitHub's push protection, which is exactly
# the behaviour `redact_secrets` is meant to have.
_A = "A" * 32


@pytest.mark.parametrize("secret", [
    "sk-" + "ant-api03-" + _A,
    "gh" + "p_" + _A,
    "gh" + "o_" + _A,
    "gh" + "s_" + _A,
    "github" + "_pat_11ABCDEFG0" + _A,
    "AIza" + "Sy" + "B" * 33,
    "xox" + "b-123456789012-1234567890123-" + _A,
    "AKIA" + "IOSFODNN7EXAMPLE",
    "ASIA" + "IOSFODNN7EXAMPLE",
    "eyJ" + "hbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + _A,
])
def test_known_credential_shapes_are_redacted(secret):
    assert secret not in redact_secrets(f"the agent printed {secret} to stdout")


def test_pem_private_key_block_is_redacted_whole():
    pem = ("-----BEGIN RSA PRIVATE KEY-----\n"
           "MIIEowIBAAKCAQEAtEXAMPLEKEYMATERIALGOESHERE\n"
           "abcdefghijklmnopqrstuvwxyz0123456789\n"
           "-----END RSA PRIVATE KEY-----")
    out = redact_secrets(f"cat id_rsa\n{pem}\ndone")
    assert "MIIEowIBAAKC" not in out
    assert "BEGIN RSA PRIVATE KEY" not in out
    assert "done" in out


def test_bearer_header_is_redacted():
    out = redact_secrets("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")
    assert "abcdefghijklmnopqrstuvwxyz123456" not in out


def test_credentials_in_a_url_are_redacted():
    token = "gh" + "p_" + "c" * 32
    out = redact_secrets(f"git clone https://someone:{token}@github.com/x/y.git")
    assert token not in out
    assert "github.com/x/y.git" in out, "only the credential is removed"


def test_ordinary_output_is_left_alone():
    text = "ran 12 tests, 0 failed; touched src/main.py and README.md"
    assert redact_secrets(text) == text
