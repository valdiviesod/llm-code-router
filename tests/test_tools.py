"""Tests for the tools subsystem."""

from __future__ import annotations

from pathlib import Path

from coderouter.config import SecurityConfig
from coderouter.core.models import (
    AgentCapabilities,
    Capability,
    Risk,
    Task,
)
from coderouter.security.policy import CommandPolicy
from coderouter.tools import (
    BuiltinToolHost,
    builtin_tools,
)
from coderouter.tools.models import MCPServer, Tool
from coderouter.tools.registry import ToolRegistry
from coderouter.tools.selector import ToolSelector


class _StaticAdapter:
    """Stand-in for an AgentAdapter that the selector can iterate over."""

    def __init__(self, caps: frozenset[Capability] = frozenset({Capability.CODE_EDIT})):
        self._caps = AgentCapabilities(capabilities=caps)

    @property
    def id(self) -> str:
        return "static"

    @property
    def capabilities(self) -> AgentCapabilities:
        return self._caps


def test_builtin_tools_are_returned():
    tools = builtin_tools()
    names = {t.name for t in tools}
    assert {"read_file", "grep", "edit_file", "run_command"} <= names


def test_selector_skips_shell_for_low_risk_task():
    registry = ToolRegistry()
    selector = ToolSelector(registry, default_budget_tokens=10_000)
    adapter = _StaticAdapter()
    task = Task(prompt="x", task_type="general", risk=Risk.LOW)
    chosen = selector.select(adapter, task)
    names = {t.name for t in chosen}
    assert "run_command" not in names
    assert "read_file" in names


def test_selector_includes_shell_for_high_risk_task():
    registry = ToolRegistry()
    selector = ToolSelector(registry, default_budget_tokens=10_000)
    adapter = _StaticAdapter()
    task = Task(prompt="x", task_type="general", risk=Risk.HIGH)
    chosen = selector.select(adapter, task)
    names = {t.name for t in chosen}
    assert "run_command" in names


def test_selector_respects_token_budget():
    registry = ToolRegistry()
    # A budget so small we can only fit one builtin tool.
    selector = ToolSelector(registry, default_budget_tokens=80)
    adapter = _StaticAdapter()
    task = Task(prompt="x", task_type="general", risk=Risk.HIGH)
    chosen = selector.select(adapter, task)
    assert len(chosen) == 1


def test_selector_orders_by_priority():
    registry = ToolRegistry()
    registry.builtin = [
        Tool(name="high", priority=1, kind="read", risk="low", token_cost=10),  # type: ignore[arg-type]
        Tool(name="low", priority=99, kind="read", risk="low", token_cost=10),  # type: ignore[arg-type]
    ]
    selector = ToolSelector(registry, default_budget_tokens=10_000)
    adapter = _StaticAdapter()
    task = Task(prompt="x", task_type="general", risk=Risk.LOW)
    chosen = selector.select(adapter, task)
    assert chosen[0].name == "high"


def test_selector_includes_native_and_discovered():
    registry = ToolRegistry()
    selector = ToolSelector(registry, default_budget_tokens=10_000)
    adapter = _StaticAdapter()
    task = Task(prompt="x", task_type="general", risk=Risk.LOW)
    discovered = [
        Tool(name="custom", kind="read", risk="low", token_cost=10),  # type: ignore[arg-type]
    ]
    chosen = selector.select(adapter, task, discovered=discovered)
    assert {t.name for t in chosen} >= {"custom", "read_file"}


def test_builtin_host_rejects_path_outside_root(tmp_path: Path):
    host = BuiltinToolHost(project_root=tmp_path, policy=CommandPolicy(SecurityConfig()))
    out = tmp_path.parent / "secret.txt"
    out.write_text("nope")
    # Try to read a file outside the project root.
    out_path = out.relative_to(tmp_path.parent)  # type: ignore[arg-type]
    result = host.read_file(str(out_path))
    assert result["ok"] is False


def test_builtin_host_reads_inside_root(tmp_path: Path):
    host = BuiltinToolHost(project_root=tmp_path, policy=CommandPolicy(SecurityConfig()))
    f = tmp_path / "ok.py"
    f.write_text("print('hello')")
    result = host.read_file("ok.py")
    assert result["ok"] is True
    assert "print" in result["content"]


def test_builtin_host_blocks_dangerous_command():
    host = BuiltinToolHost(project_root=Path("/tmp"), policy=CommandPolicy(SecurityConfig()))
    result = host.run_command("rm -rf /")
    assert result["ok"] is False
    assert "blocked" in result["error"]


def test_builtin_host_flags_ask_command():
    host = BuiltinToolHost(project_root=Path("/tmp"), policy=CommandPolicy(SecurityConfig()))
    result = host.run_command("git push origin main")
    assert result["ok"] is False
    assert "requires_approval" in result["error"]


def test_mcp_server_dataclass():
    server = MCPServer(name="fs", command="fs-mcp", args=("--stdio",))
    assert server.name == "fs"
    assert server.transport == "stdio"


def test_tool_registry_register_mcp_server():
    registry = ToolRegistry()
    server = MCPServer(name="fs", command="fs-mcp")
    registry.register_mcp_server(server)
    assert "fs" in registry.mcp_servers


def test_tool_registry_default_adapter_tools_empty():
    registry = ToolRegistry()
    adapter = _StaticAdapter()
    assert registry.available_for(adapter) == builtin_tools()


def test_tool_registry_register_adapter_tools():
    registry = ToolRegistry()
    adapter = _StaticAdapter()
    custom = [Tool(name="custom", kind="read", risk="low", token_cost=10)]  # type: ignore[arg-type]
    registry.register_adapter(adapter, custom)
    pool = registry.available_for(adapter)
    assert {t.name for t in pool} >= {"custom", "read_file"}
