"""Real-CLI tests. Excluded by default; run with `pytest -m provider`.

These cost real quota and require authenticated CLIs, which is exactly why they
are never part of the ordinary suite.
"""

import pytest

from coderouter.agents.antigravity.adapter import AntigravityAdapter
from coderouter.agents.claude.adapter import ClaudeCodeAdapter
from coderouter.config import AgentConfig
from coderouter.core.models import Task

pytestmark = pytest.mark.provider


@pytest.mark.parametrize("cls", [ClaudeCodeAdapter, AntigravityAdapter])
async def test_health_check_against_real_binary(cls):
    adapter = cls(AgentConfig())
    if not adapter.binary_available():
        pytest.skip(f"{adapter.command} not installed")
    health = await adapter.health_check()
    assert health.healthy, health.detail


@pytest.mark.parametrize("cls", [ClaudeCodeAdapter, AntigravityAdapter])
async def test_execute_minimal_prompt(cls, tmp_path):
    adapter = cls(AgentConfig(timeout_s=180))
    if not adapter.binary_available():
        pytest.skip(f"{adapter.command} not installed")
    result = await adapter.execute(
        Task(prompt="Reply with exactly: pong", project_root=tmp_path))
    assert result.success, result.error
    assert "pong" in result.output.lower()
    assert result.total_tokens > 0
