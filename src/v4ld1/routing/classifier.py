"""Heuristic task classifier.

Deliberately rule-based: it is cheap, deterministic, testable, and costs zero
tokens. Anything smarter (an LLM pre-pass) can be plugged in later behind the
same classify() signature.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.models import Capability, Complexity, Risk

_TYPE_PATTERNS: list[tuple[str, str]] = [
    ("security", r"\b(security|auth\w*|vulnerab|xss|csrf|injection|jwt|oauth|encrypt)\b"),
    ("database", r"\b(database|sql|migration|schema|postgres|sqlite|index|query)\b"),
    ("testing", r"\b(test|tests|pytest|coverage|unit test|e2e)\b"),
    ("debugging", r"\b(debug|investigate|why does|root cause|trace)\b"),
    ("refactor", r"\b(refactor|clean ?up|rename|extract|simplify|deduplicate)\b"),
    ("architecture", r"\b(architect\w*|design|structure|redesign|plan the)\b"),
    ("documentation", r"\b(document|readme|docs|changelog|comment|typo)\b"),
    ("bug_fix", r"\b(fix|bug|broken|crash|error|regression|failing)\b"),
    ("review", r"\b(review|audit|inspect|critique)\b"),
    ("frontend", r"\b(frontend|ui|css|react|vue|component|styling|tailwind)\b"),
    ("backend", r"\b(backend|api|endpoint|server|route|handler)\b"),
    ("devops", r"\b(docker|ci|cd|pipeline|deploy|kubernetes|terraform)\b"),
    ("research", r"\b(research|compare|evaluate|investigate options|explore)\b"),
    ("feature", r"\b(implement|add|build|create|feature|support for)\b"),
]

_HIGH_SIGNALS = (
    r"\b(architect\w*|migrat\w*|refactor|distributed|concurren\w*|race condition"
    r"|end-to-end|full stack|entire|whole (?:app|system|codebase))\b"
)
_TRIVIAL_SIGNALS = r"\b(typo|rename|one-liner|comment|format|whitespace|bump version)\b"
_RISK_SIGNALS = (
    r"\b(production|prod|payment|billing|auth\w*|secret|credential|delete|drop"
    r"|migration|security)\b"
)

_TYPE_CAPABILITIES: dict[str, frozenset[Capability]] = {
    "architecture": frozenset({Capability.PLANNING, Capability.DEEP_REASONING}),
    "review": frozenset({Capability.REVIEW}),
    "debugging": frozenset({Capability.DEEP_REASONING, Capability.CODE_EDIT}),
    "security": frozenset({Capability.DEEP_REASONING, Capability.CODE_EDIT}),
}


@dataclass(slots=True)
class Classification:
    task_type: str
    complexity: Complexity
    risk: Risk
    required_capabilities: frozenset[Capability]
    reasons: list[str]


def classify(prompt: str) -> Classification:
    text = prompt.lower()
    reasons: list[str] = []

    task_type = "general"
    for name, pattern in _TYPE_PATTERNS:
        if re.search(pattern, text):
            task_type = name
            reasons.append(f"matched {name} vocabulary")
            break

    words = len(text.split())
    if re.search(_TRIVIAL_SIGNALS, text) and words < 25:
        complexity = Complexity.TRIVIAL
        reasons.append("trivial keyword, short prompt")
    elif re.search(_HIGH_SIGNALS, text) or words > 120:
        complexity = Complexity.HIGH
        reasons.append("architectural/large-scope signals")
    elif words < 12:
        complexity = Complexity.LOW
        reasons.append("short, narrow prompt")
    else:
        complexity = Complexity.MEDIUM
        reasons.append("default medium scope")

    risk = Risk.LOW
    if re.search(_RISK_SIGNALS, text):
        risk = Risk.HIGH if complexity.rank >= Complexity.HIGH.rank else Risk.MEDIUM
        reasons.append("touches sensitive area")

    if risk is Risk.HIGH and complexity is Complexity.HIGH:
        complexity = Complexity.CRITICAL
        reasons.append("high risk + high complexity escalates to critical")

    caps = frozenset({Capability.CODE_EDIT}) | _TYPE_CAPABILITIES.get(task_type, frozenset())
    return Classification(task_type, complexity, risk, caps, reasons)
