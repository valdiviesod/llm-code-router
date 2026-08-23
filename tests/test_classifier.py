import pytest

from coderouter.core.models import Capability, Complexity, Risk
from coderouter.routing.classifier import classify


@pytest.mark.parametrize("prompt,expected", [
    ("fix the typo in the readme", "documentation"),
    ("implement JWT authentication for the API", "security"),
    ("refactor the payment module", "refactor"),
    ("write tests for the parser", "testing"),
])
def test_task_type(prompt, expected):
    assert classify(prompt).task_type == expected


def test_trivial_prompt():
    c = classify("fix typo")
    assert c.complexity is Complexity.TRIVIAL


def test_architecture_prompt_is_high_and_needs_reasoning():
    c = classify("redesign the architecture of the whole system for multi tenancy")
    assert c.complexity.rank >= Complexity.HIGH.rank
    assert Capability.DEEP_REASONING in c.required_capabilities


def test_sensitive_scope_escalates_risk():
    c = classify("migrate the production database schema across the entire system")
    assert c.risk is Risk.HIGH
    assert c.complexity is Complexity.CRITICAL
