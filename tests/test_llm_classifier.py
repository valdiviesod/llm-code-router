from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from conftest import FakeAdapter

from v4ld1.agents.base.registry import AgentRegistry
from v4ld1.config import AgentConfig
from v4ld1.core.models import (
    Capability,
    Completion,
    Complexity,
    Risk,
)
from v4ld1.routing.llm_classifier import (
    LLMClassifier,
    _parse,
    _strip_fence,
)
from v4ld1.usage.manager import UsageManager


class RaisingFakeAdapter(FakeAdapter):
    """Adapter that raises if complete() is called."""

    async def complete(self, prompt, *, system="", schema=None, model=None, timeout=120):
        raise AssertionError("complete() must not be called")


def test_strip_fence():
    assert _strip_fence('{"a": 1}') == '{"a": 1}'
    assert _strip_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_fence('```\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_fence('Here is the json: {"a": 1} hope it helps') == '{"a": 1}'


def test_parse_valid_payload():
    payload = {
        "task_type": "bug_fix",
        "complexity": "low",
        "risk": "medium",
        "confidence": 0.85,
        "reason": "fixing a bug in login handler",
    }
    c = _parse(payload, "fix login bug")
    assert c is not None
    assert c.task_type == "bug_fix"
    assert c.complexity is Complexity.LOW
    assert c.risk is Risk.MEDIUM
    assert c.confidence == 0.85
    assert c.source == "llm"
    assert "fixing a bug in login handler" in c.reasons[0]
    assert Capability.CODE_EDIT in c.required_capabilities


def test_parse_rejects_unknown_task_type():
    payload = {
        "task_type": "unknown_cosmic_task",
        "complexity": "low",
        "risk": "low",
        "confidence": 0.9,
        "reason": "weird task",
    }
    assert _parse(payload, "prompt") is None


def test_parse_rejects_out_of_enum_complexity():
    payload = {
        "task_type": "bug_fix",
        "complexity": "super_impossible",
        "risk": "low",
        "confidence": 0.9,
    }
    assert _parse(payload, "prompt") is None


def test_parse_rejects_out_of_enum_risk():
    payload = {
        "task_type": "bug_fix",
        "complexity": "low",
        "risk": "catastrophic",
        "confidence": 0.9,
    }
    assert _parse(payload, "prompt") is None


def test_parse_rejects_missing_key():
    payload = {
        "task_type": "bug_fix",
        # missing complexity and risk
        "confidence": 0.9,
    }
    assert _parse(payload, "prompt") is None


async def test_classifier_heuristic_mode_makes_zero_complete_calls(config, db):
    config.routing.classifier = "heuristic"
    adapter = RaisingFakeAdapter(config.agent("fake"), "fake",
                                caps=frozenset({Capability.STRUCTURED_COMPLETION}))
    registry = AgentRegistry(config)
    registry._adapters = {"fake": adapter}
    usage = UsageManager(config, db)
    classifier = LLMClassifier(config, registry, usage, db)

    c = await classifier.classify("borra la tabla de usuarios en produccion")
    assert c.source == "heuristic"
    assert len(adapter.complete_calls) == 0


async def test_classifier_auto_skips_model_when_heuristic_confident(config, db):
    config.routing.classifier = "auto"
    config.routing.classifier_threshold = 0.6
    adapter = RaisingFakeAdapter(config.agent("fake"), "fake",
                                caps=frozenset({Capability.STRUCTURED_COMPLETION}))
    registry = AgentRegistry(config)
    registry._adapters = {"fake": adapter}
    usage = UsageManager(config, db)
    classifier = LLMClassifier(config, registry, usage, db)

    # English prompt matches patterns -> confidence >= 0.6
    c = await classifier.classify("fix the typo in the readme docs")
    assert c.source == "heuristic"
    assert c.confidence >= 0.6
    assert len(adapter.complete_calls) == 0


async def test_classifier_auto_calls_model_when_unsure(config, db):
    config.routing.classifier = "auto"
    config.routing.classifier_threshold = 0.7
    completion_data = {
        "task_type": "database",
        "complexity": "critical",
        "risk": "high",
        "confidence": 0.95,
        "reason": "dropping production database table is critical risk",
    }
    adapter = FakeAdapter(
        config.agent("fake"), "fake",
        caps=frozenset({Capability.CODE_EDIT, Capability.STRUCTURED_COMPLETION}),
        completion=Completion(
            text=json.dumps(completion_data),
            agent_id="fake",
            input_tokens=16000,
            output_tokens=100,
            structured=completion_data,
        )
    )
    registry = AgentRegistry(config)
    registry._adapters = {"fake": adapter}
    usage = UsageManager(config, db)
    classifier = LLMClassifier(config, registry, usage, db)

    prompt = "borra la tabla de usuarios en produccion"
    c = await classifier.classify(prompt)
    assert c.source == "llm"
    assert c.risk is Risk.HIGH
    assert c.complexity is Complexity.CRITICAL
    assert c.task_type == "database"
    assert len(adapter.complete_calls) == 1


async def test_model_call_none_leaves_heuristic_result(config, db):
    config.routing.classifier = "llm"
    adapter = FakeAdapter(
        config.agent("fake"), "fake",
        caps=frozenset({Capability.CODE_EDIT, Capability.STRUCTURED_COMPLETION}),
        completion=None,
    )
    registry = AgentRegistry(config)
    registry._adapters = {"fake": adapter}
    usage = UsageManager(config, db)
    classifier = LLMClassifier(config, registry, usage, db)

    prompt = "borra la base de datos"
    c = await classifier.classify(prompt)
    assert c.source == "heuristic"
    assert any("model classification unavailable" in r for r in c.reasons)


async def test_agent_selection_picks_lowest_quota_pressure(config, db):
    config.routing.classifier = "llm"
    # Agent a1 is near limit (high pressure)
    config.agents["a1"] = AgentConfig(command="true", window_limit_tokens=1000)
    config.agents["a2"] = AgentConfig(command="true", window_limit_tokens=10000)

    db.record_usage("a1", 900, "confirmed", kind="run")
    db.record_usage("a2", 100, "confirmed", kind="run")

    completion_payload = {
        "task_type": "bug_fix",
        "complexity": "low",
        "risk": "low",
        "confidence": 0.9,
        "reason": "simple fix",
    }
    a1 = FakeAdapter(
        config.agent("a1"), "a1",
        caps=frozenset({Capability.CODE_EDIT, Capability.STRUCTURED_COMPLETION}),
        completion=Completion(text=json.dumps(completion_payload), agent_id="a1", input_tokens=100, output_tokens=10),
    )
    a2 = FakeAdapter(
        config.agent("a2"), "a2",
        caps=frozenset({Capability.CODE_EDIT, Capability.STRUCTURED_COMPLETION}),
        completion=Completion(text=json.dumps(completion_payload), agent_id="a2", input_tokens=100, output_tokens=10),
    )
    registry = AgentRegistry(config)
    registry._adapters = {"a1": a1, "a2": a2}
    usage = UsageManager(config, db)
    classifier = LLMClassifier(config, registry, usage, db)

    await classifier.classify("spanish prompt sin contexto claro")
    # a2 should have been picked because pressure is much lower
    assert len(a2.complete_calls) == 1
    assert len(a1.complete_calls) == 0


async def test_agent_selection_skips_agents_without_capability(config, db):
    config.routing.classifier = "llm"
    a1 = FakeAdapter(
        config.agent("fake"), "a1",
        caps=frozenset({Capability.CODE_EDIT}),  # no STRUCTURED_COMPLETION
    )
    completion_payload = {
        "task_type": "bug_fix",
        "complexity": "low",
        "risk": "low",
        "confidence": 0.9,
        "reason": "simple fix",
    }
    a2 = FakeAdapter(
        config.agent("other"), "a2",
        caps=frozenset({Capability.CODE_EDIT, Capability.STRUCTURED_COMPLETION}),
        completion=Completion(text=json.dumps(completion_payload), agent_id="a2", input_tokens=100, output_tokens=10),
    )
    registry = AgentRegistry(config)
    registry._adapters = {"a1": a1, "a2": a2}
    usage = UsageManager(config, db)
    classifier = LLMClassifier(config, registry, usage, db)

    picked = await classifier._pick_agent()
    assert picked is not None
    assert picked.id == "a2"


async def test_classifier_tokens_recorded_in_usage_and_counted_in_tokens_since(config, db):
    config.routing.classifier = "llm"
    completion_payload = {
        "task_type": "feature",
        "complexity": "medium",
        "risk": "low",
        "confidence": 0.8,
        "reason": "adding a feature",
    }
    adapter = FakeAdapter(
        config.agent("fake"), "fake",
        caps=frozenset({Capability.CODE_EDIT, Capability.STRUCTURED_COMPLETION}),
        completion=Completion(
            text=json.dumps(completion_payload),
            agent_id="fake",
            input_tokens=16000,
            output_tokens=150,
            structured=completion_payload,
        ),
    )
    registry = AgentRegistry(config)
    registry._adapters = {"fake": adapter}
    usage = UsageManager(config, db)
    classifier = LLMClassifier(config, registry, usage, db)

    since = datetime.now(UTC) - timedelta(minutes=5)
    await classifier.classify("un prompt nuevo")

    # Verify usage_events has kind='classification'
    row = db.conn.execute("SELECT kind, tokens FROM usage_events WHERE agent_id='fake'").fetchone()
    assert row is not None
    assert row["kind"] == "classification"
    assert row["tokens"] == 16150

    # tokens_since must include classifier tokens
    assert db.tokens_since("fake", since) == 16150


async def test_classification_caching(config, db):
    config.routing.classifier = "llm"
    completion_payload = {
        "task_type": "refactor",
        "complexity": "medium",
        "risk": "low",
        "confidence": 0.88,
        "reason": "refactoring logic",
    }
    adapter = FakeAdapter(
        config.agent("fake"), "fake",
        caps=frozenset({Capability.CODE_EDIT, Capability.STRUCTURED_COMPLETION}),
        completion=Completion(
            text=json.dumps(completion_payload),
            agent_id="fake",
            input_tokens=1000,
            output_tokens=50,
            structured=completion_payload,
        ),
    )
    registry = AgentRegistry(config)
    registry._adapters = {"fake": adapter}
    usage = UsageManager(config, db)
    classifier = LLMClassifier(config, registry, usage, db)

    prompt = "refactoriza este modulo complejo"
    c1 = await classifier.classify(prompt)
    assert c1.source == "llm"
    assert len(adapter.complete_calls) == 1

    # Second call should read cache and not call adapter
    c2 = await classifier.classify(prompt)
    assert c2.source == "llm"
    assert c2.task_type == "refactor"
    assert any("cached model classification" in r for r in c2.reasons)
    assert len(adapter.complete_calls) == 1
