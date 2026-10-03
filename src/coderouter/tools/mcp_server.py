"""MCP stdio server exposing the router's builtin tools.

This is the *bridge* half of the tools subsystem: `router mcp-serve` speaks
the same line-delimited JSON-RPC the `MCPClient` speaks, and answers
`tools/list` / `tools/call` from the `BuiltinToolHost` — so an agent CLI with
MCP support (Claude Code's `--mcp-config`) can call the router's read /
grep / edit / run_command surface instead of its own unbounded equivalents.

`--allow` is what makes the tool selector's budget real: the orchestrator
starts one server per run advertising only the tools that run selected, and
a call to anything else is refused by the host.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

from .. import __version__
from ..config import Config
from ..security.permissions import PermissionsEngine
from ..security.policy import CommandPolicy
from ..security.sandbox import Sandbox
from .builtin import BuiltinToolHost, builtin_tools
from .models import Tool

#: MCP revision this server speaks; mirrors the client's constant.
PROTOCOL_VERSION = "2024-11-05"

_ALLOWED = ("read_file", "grep", "edit_file", "run_command")


def _tool_spec(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": tool.input_schema,
    }


class BuiltinMCPServer:
    """Serves the builtin tools over stdio JSON-RPC (blocking loop)."""

    def __init__(
        self,
        root: Path,
        allowed: list[str] | None = None,
        *,
        config: Config | None = None,
        auto_approve: bool = False,
    ):
        cfg = config or Config()
        self._allowed_names = frozenset(
            n for n in (allowed or list(_ALLOWED)) if n in _ALLOWED
        )
        policy = CommandPolicy(cfg.security)
        permissions = PermissionsEngine(cfg.permissions)
        sandbox = Sandbox(cfg.sandbox)
        approver = (lambda command, reason: True) if auto_approve else None
        self.host = BuiltinToolHost(
            project_root=root,
            policy=policy,
            permissions=permissions,
            sandbox=sandbox,
            approver=approver,
            allowed_tools=self._allowed_names,
        )

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """One request in, one response out (or None for notifications)."""
        method = message.get("method", "")
        msg_id = message.get("id")
        if method == "initialize":
            return self._ok(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "coderouter", "version": __version__},
            })
        if method.startswith("notifications/"):
            return None
        if method == "tools/list":
            tools = [t for t in builtin_tools() if t.name in self._allowed_names]
            return self._ok(msg_id, {"tools": [_tool_spec(t) for t in tools]})
        if method == "tools/call":
            params = message.get("params") or {}
            name = str(params.get("name", ""))
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            result = self.host.call(name, arguments)
            text = json.dumps(result)
            return self._ok(msg_id, {
                "content": [{"type": "text", "text": text}],
                "isError": not result.get("ok", False),
            })
        if msg_id is not None:
            return self._error(msg_id, -32601, f"unknown method: {method}")
        return None

    @staticmethod
    def _ok(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}

    def serve(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        """The blocking stdio loop. One JSON object per line, per the wire
        format the client documents."""
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # stray chatter on stdin, not a request
            if not isinstance(message, dict):
                continue
            response = self.handle(message)
            if response is not None:
                stdout.write(json.dumps(response) + "\n")
                stdout.flush()


def mcp_config_for(allowed: list[str], root: Path) -> dict[str, Any]:
    """The `--mcp-config` payload an MCP-capable agent CLI is pointed at.

    The server is invoked through the same interpreter that is running the
    router, so the bridge works in any environment the router itself works
    in — no PATH assumptions about a `router` binary.
    """
    return {
        "mcpServers": {
            "coderouter": {
                "command": sys.executable,
                "args": ["-m", "coderouter.tools.mcp_server",
                         "--root", str(root),
                         "--allow", ",".join(allowed)],
            }
        }
    }


def main(argv: list[str] | None = None) -> int:
    """`python -m coderouter.tools.mcp_server` — the MCP bridge process.

    `--yes` auto-approves every ASK verdict. It exists because a server
    running as an agent CLI's child cannot prompt a human; installing it is
    an explicit, visible act of trust in the config that spawns it, never a
    default.
    """
    import argparse

    parser = argparse.ArgumentParser("coderouter-mcp-serve")
    parser.add_argument("--root", default=".", help="project root to serve")
    parser.add_argument("--allow", default=",".join(_ALLOWED),
                        help="comma-separated tool names to advertise")
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("--yes", action="store_true",
                        help="auto-approve ASK verdicts (explicit trust)")
    args = parser.parse_args(argv)

    from ..config import load_config

    config = load_config(Path(args.config) if args.config else None)
    server = BuiltinMCPServer(
        Path(args.root).resolve(),
        [t for t in args.allow.split(",") if t],
        config=config,
        auto_approve=args.yes,
    )
    server.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
