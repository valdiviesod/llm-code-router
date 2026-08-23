"""Command policy and secret protection.

Agents can run shell commands, so every command v4ld1 itself proposes is
classified before it runs. BLOCK is absolute; ASK requires a human.
"""

from __future__ import annotations

import re
from enum import Enum

from ..config import SecurityConfig

_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
    r"(?i:(?:api[_-]?key|token|password|secret)\s*[:=]\s*)\S{8,})"
)


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
