"""Tool registry and selection.

The registry collects tools from three sources, dedupes by name, and
exposes a `select()` method that picks a budgeted subset for a given
task. Selection is pure: no subprocess is started, no file is read.

A registry instance is built per-orchestrator. MCP server connections
are opened lazily by the adapter that needs them; the registry only
holds the *configuration* of which servers exist and which tools
they are expected to expose.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..agents.base.adapter import AgentAdapter
from ..config import MCPServerConfig
from ..core.models import Risk, Task
from ..logging import get_logger
from .builtin import builtin_tools
from .mcp_client import MCPClient
from .models import MCPServer, Tool

logger = get_logger("tools.registry")


@dataclass(slots=True)
class ToolRegistry:
    """Aggregates tools from builtin + adapter + MCP sources."""

    builtin: list[Tool] = field(default_factory=builtin_tools)
    adapter_tools: dict[str, list[Tool]] = field(default_factory=dict)
    mcp_servers: dict[str, MCPServer] = field(default_factory=dict)
    _mcp_clients: dict[str, MCPClient] = field(default_factory=dict)

    def register_adapter(self, adapter: AgentAdapter, tools: list[Tool]) -> None:
        for t in tools:
            t.source = t.source or f"adapter:{adapter.id}"
        self.adapter_tools[adapter.id] = list(tools)

    def register_mcp_server(self, server: MCPServer) -> None:
        self.mcp_servers[server.name] = server

    def available_for(self, adapter: AgentAdapter) -> list[Tool]:
        """Tools an adapter may see: builtin + adapter-native + MCP-discovered.

        MCP discovery is best-effort and may return []; the orchestrator
        continues. The MCP client is created on first use, so this method
        must be `async` even when no servers are configured.
        """
        return self.builtin + self.adapter_tools.get(adapter.id, [])

    async def list_mcp_tools(self) -> dict[str, list[Tool]]:
        """Discover tools from every configured MCP server.

        Returns a mapping of server name to discovered tools. A failing
        server maps to an empty list and is logged, never raised.
        """
        out: dict[str, list[Tool]] = {}
        for name, server in self.mcp_servers.items():
            client = self._mcp_clients.get(name) or MCPClient(server)
            self._mcp_clients[name] = client
            try:
                out[name] = await client.list_tools()
            except Exception as exc:  # noqa: BLE001 - tools must not break the run
                logger.warning("MCP server %r discovery failed: %s", name, exc)
                out[name] = []
        return out

    async def close(self) -> None:
        for client in self._mcp_clients.values():
            await client.close()
        self._mcp_clients.clear()

    def select(
        self,
        adapter: AgentAdapter,
        task: Task,
        max_tokens: int,
        *,
        discovered: list[Tool] | None = None,
    ) -> list[Tool]:
        """Pick a budgeted subset of tools for this task.

        Selection rules (in order):
        1. Always exclude any tool whose `risk` exceeds the task's risk
           (a LOW-risk task never gets a high-risk tool).
        2. Greedy fill by ascending `priority` then `token_cost`.
        3. Stop when adding the next tool would exceed `max_tokens`.
        """
        pool = self.available_for(adapter)
        if discovered:
            pool = pool + discovered
        task_risk = task.risk
        allowed: list[Tool] = []
        for tool in pool:
            if not _risk_ok(tool.risk, task_risk):
                continue
            if tool.kind == "shell" and task.risk is Risk.LOW:
                # Read-only tasks never get a shell tool, regardless of budget.
                continue
            allowed.append(tool)
        allowed.sort(key=lambda t: (t.priority, t.token_cost, t.name))
        chosen: list[Tool] = []
        used = 0
        for tool in allowed:
            cost = tool.token_cost or 1
            if used + cost > max_tokens:
                continue
            chosen.append(tool)
            used += cost
        return chosen

    @classmethod
    def from_config(
        cls, configs: list[MCPServerConfig]
    ) -> ToolRegistry:
        registry = cls()
        for cfg in configs:
            registry.register_mcp_server(MCPServer(
                name=cfg.name,
                command=cfg.command,
                args=tuple(cfg.args),
                env=dict(cfg.env),
            ))
        return registry


def _risk_ok(tool_risk: Risk, task_risk: Risk) -> bool:
    """A tool's risk must not exceed the task's risk.

    Ordering follows `Risk` rank: LOW < MEDIUM < HIGH. A high-risk tool
    is fine for a high-risk task, but a low-risk task may not run it.
    """
    order = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2}
    return order[tool_risk] <= order[task_risk]
