"""Tool domain types.

A `Tool` is something an agent can call. Tools are either:
- Built into the router (read_file, edit_file, run_command).
- Declared by an adapter (an agent's native capabilities).
- Discovered from an MCP server (third-party process speaking JSON-RPC).

The router reasons about *which* tools to expose for a given task, not
*how* to call them — the agent invokes them through the MCP bridge or
its own native tool system, never through the router directly. The
router's job is to keep the selection within a token budget and to
make sure the chosen agent actually has the capabilities the
selected tools require.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..core.models import Risk

ToolKind = Literal["read", "write", "shell", "search", "network", "other"]


@dataclass(slots=True)
class Tool:
    """A callable capability.

    `kind` is coarse: `read` is safe to call against untrusted code;
    `write` and `shell` mutate state. `risk` is a finer label that the
    selector uses to filter tools for tasks whose risk profile forbids
    mutation. `token_cost` is the router's estimate of how many input
    tokens the tool's description will add to the agent's prompt; the
    selector treats it as the unit of budget.
    """

    name: str
    description: str = ""
    kind: ToolKind = "other"
    risk: Risk = Risk.LOW
    input_schema: dict[str, Any] = field(default_factory=dict)
    token_cost: int = 0
    priority: int = 0
    source: str = ""  # "builtin", "adapter:<id>", "mcp:<server>"


@dataclass(slots=True)
class MCPServer:
    """Configuration for a Model Context Protocol server.

    The server is spawned lazily on first use and torn down when the
    adapter shuts down. `command` plus `args` is what `asyncio.create_subprocess_exec`
    takes; `env` overrides are merged on top of `os.environ`.
    """

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    transport: Literal["stdio"] = "stdio"
