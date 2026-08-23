"""Contract tests every adapter must pass.

Adding an agent means adding its class to ADAPTERS below. If it cannot satisfy
these, the core cannot drive it. No real CLI is invoked: execution is exercised
through the pure parse_result() path plus a stubbed subprocess.
"""

from __future__ import annotations

import json

import pytest

from v4ld1.agents.antigravity.adapter import AntigravityAdapter
from v4ld1.agents.base.adapter import AgentAdapter
from v4ld1.agents.base.registry import AgentRegistry
from v4ld1.agents.claude.adapter import ClaudeCodeAdapter
from v4ld1.config import AgentConfig, Config
from v4ld1.core.models import Capability, Task, UsageStatus

ADAPTERS = [ClaudeCodeAdapter, AntigravityAdapter]


@pytest.fixture(params=ADAPTERS, ids=lambda c: c.__name__)
def adapter(request) -> AgentAdapter:
    return request.param(AgentConfig())


def test_identity(adapter):
    assert adapter.id and isinstance(adapter.id, str)
    assert adapter.display_name
    assert adapter.default_command


def test_capabilities_declared(adapter):
    caps = adapter.capabilities.capabilities
    assert Capability.CODE_EDIT in caps
    assert all(isinstance(c, Capability) for c in caps)


async def test_models_never_raise(adapter):
    assert isinstance(await adapter.get_models(), list)


async def test_usage_defaults_to_unknown(adapter):
    info = await adapter.get_usage()
    assert info.status is UsageStatus.UNKNOWN


async def test_estimate_scales_with_prompt(adapter):
    small = await adapter.estimate(Task(prompt="hi"))
    large = await adapter.estimate(Task(prompt="hi " * 5000))
    assert large.total_tokens > small.total_tokens


async def test_health_check_reports_missing_binary(adapter):
    adapter.config.command = "definitely-not-a-real-binary"
    health = await adapter.health_check()
    assert not health.healthy
    assert health.remediation


def test_parse_success(adapter):
    payload = json.dumps({
        "is_error": False, "status": "SUCCESS",
        "result": "edited src/app.py", "response": "edited src/app.py",
        "session_id": "abc",
        "usage": {"input_tokens": 100, "output_tokens": 50},
    })
    result = adapter.parse_result(Task(prompt="p"), "m", 0, payload, "", 1.0)
    assert result.success
    assert result.output == "edited src/app.py"
    assert result.output_tokens == 50
    assert result.usage_status is UsageStatus.CONFIRMED
    assert result.session_id == "abc"
    assert "src/app.py" in result.files_changed


NATIVE_PAYLOADS = {
    # Captured from the real CLIs; these shapes are what the adapters must read.
    "claude": ({"is_error": False, "result": "pong", "session_id": "s1",
                "usage": {"input_tokens": 2, "cache_read_input_tokens": 10,
                          "cache_creation_input_tokens": 5, "output_tokens": 4},
                "total_cost_usd": 0.01}, 17, 4, "s1"),
    "antigravity": ({"conversation_id": "c1", "status": "SUCCESS", "response": "pong",
                     "usage": {"input_tokens": 100, "output_tokens": 20,
                               "thinking_tokens": 5, "cache_read_tokens": 50}},
                    150, 25, "c1"),
}


def test_parse_native_payload(adapter):
    payload, expect_in, expect_out, expect_session = NATIVE_PAYLOADS[adapter.id]
    result = adapter.parse_result(Task(prompt="p"), None, 0, json.dumps(payload), "", 1.0)
    assert result.success
    assert result.output.strip() == "pong"
    assert result.input_tokens == expect_in
    assert result.output_tokens == expect_out
    assert result.session_id == expect_session


def test_parse_reported_failure_status(adapter):
    payload = ({"is_error": True, "result": "nope"} if adapter.id == "claude"
               else {"status": "ERROR", "response": "nope"})
    result = adapter.parse_result(Task(prompt="p"), None, 0, json.dumps(payload), "", 1.0)
    assert not result.success


def test_parse_error_exit_code(adapter):
    result = adapter.parse_result(Task(prompt="p"), None, 1, "", "boom", 1.0)
    assert not result.success
    assert result.error == "boom"


def test_parse_non_json_output_is_not_fatal(adapter):
    result = adapter.parse_result(Task(prompt="p"), None, 0, "plain text reply", "", 1.0)
    assert result.success
    assert result.output == "plain text reply"
    assert result.usage_status is UsageStatus.ESTIMATED


def test_argv_is_non_interactive(adapter):
    argv = (adapter._argv(Task(prompt="p"), None)
            if isinstance(adapter, ClaudeCodeAdapter) else adapter._argv(None, "p"))
    assert any(a in ("-p", "--print=p") for a in argv), argv
    assert "--output-format" in argv and "json" in argv


async def test_cancel_is_safe_when_nothing_runs(adapter):
    await adapter.cancel("no-such-task")


def test_registry_discovers_both_shipped_adapters():
    registry = AgentRegistry(Config())
    assert {"claude", "antigravity"} <= set(registry.ids)


def test_registry_honours_disabled_agents():
    cfg = Config(agents={"claude": AgentConfig(enabled=False)})
    assert "claude" not in AgentRegistry(cfg).ids
