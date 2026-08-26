"""Command policy and secret protection.

Agents can run shell commands, so every command coderouter itself proposes is
classified before it runs. BLOCK is absolute; ASK requires a human.
"""

from __future__ import annotations

import re
from enum import Enum

from ..config import SecurityConfig

# Ordered because the specific patterns must win over the generic key=value one.
# Every pattern here was chosen for a credential that a coding agent plausibly
# prints: provider keys, VCS tokens, cloud keys, and anything a shell would
# expose through `env`. Over-redaction is the intended failure mode.
_SECRET_PATTERNS: tuple[str, ...] = (
    # PEM private keys — the whole block, not just the header.
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    # Anthropic, OpenAI and the sk- family.
    r"sk-ant-[A-Za-z0-9_\-]{16,}",
    r"sk-[A-Za-z0-9_\-]{16,}",
    # GitHub: personal, OAuth, user-to-server, server-to-server, refresh, fine-grained.
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    # Google / Firebase.
    r"AIza[0-9A-Za-z_\-]{35}",
    # Slack.
    r"xox[abposr]-[A-Za-z0-9\-]{10,}",
    # AWS access key ids, and secret keys when they are labelled.
    r"(?:AKIA|ASIA)[0-9A-Z]{16}",
    r"(?i:aws_secret_access_key\s*[:=]\s*)\S{20,}",
    # JSON Web Tokens.
    r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}",
    # Authorization headers.
    r"(?i:bearer\s+)[A-Za-z0-9._\-]{20,}",
    # Credentials embedded in a URL: scheme://user:secret@host
    r"(?i:[a-z][a-z0-9+.\-]*://)[^\s:/@]+:[^\s/@]+@",
    # Generic labelled secrets, last so the specific forms above win.
    r"(?i:(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|passwd|secret)"
    r"\s*[:=]\s*)[\"']?\S{8,}",
)

_SECRET_RE = re.compile("|".join(f"(?:{p})" for p in _SECRET_PATTERNS))


class Decision(str, Enum):
    SAFE = "safe"
    ASK = "ask"
    BLOCK = "block"


class CommandPolicy:
    def __init__(self, config: SecurityConfig):
        self._ask = [re.compile(p, re.IGNORECASE) for p in config.ask_patterns]
        self._block = [re.compile(p, re.IGNORECASE) for p in config.block_patterns]

    def classify(self, command: str) -> tuple[Decision, str]:
        for pattern in self._block:
            if pattern.search(command):
                return Decision.BLOCK, f"matches block rule {pattern.pattern!r}"
        for pattern in self._ask:
            if pattern.search(command):
                return Decision.ASK, f"matches ask rule {pattern.pattern!r}"
        return Decision.SAFE, "no policy match"


def redact_secrets(text: str) -> str:
    """Applied to everything before it reaches a log, the DB or another agent."""
    return _SECRET_RE.sub("<redacted>", text)
