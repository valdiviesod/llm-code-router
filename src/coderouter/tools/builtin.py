"""The router's own safe tool surface.

These are tools the router *exposes* to the agent through the MCP
bridge, not tools the agent invokes through the router. They are
deliberately narrow: a small read/edit/grep surface over the
project tree, with destructive operations kept out by routing every
shell call through `security.policy.CommandPolicy`.

The agents get these tools by default; per-agent `mcp_servers` config
may add more. The router's `read_file` is `ContextBundle`-aware: when
the same path was already analysed it can be served from a cached
summary instead of re-reading the file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..security.policy import CommandPolicy, Decision
from .models import Tool


def builtin_tools() -> list[Tool]:
    return [
        Tool(
            name="read_file",
            description=(
                "Read a project file. Returns the contents up to a token budget. "
                "Files in vendor dirs (node_modules, .venv, etc.) are rejected."
            ),
            kind="read",
            risk="low",  # type: ignore[arg-type]
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project-relative path."}
                },
                "required": ["path"],
            },
            token_cost=80,
            priority=10,
            source="builtin",
        ),
        Tool(
            name="grep",
            description=(
                "Case-sensitive substring search across project files. Returns "
                "up to N matches with file:line:column anchors."
            ),
            kind="search",
            risk="low",  # type: ignore[arg-type]
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "max_results": {"type": "integer", "default": 50},
                },
                "required": ["pattern"],
            },
            token_cost=60,
            priority=20,
            source="builtin",
        ),
        Tool(
            name="edit_file",
            description=(
                "Patch a project file. The agent supplies a unified diff; "
                "the router rejects the edit if the diff touches BLOCK-listed "
                "shell commands or paths outside the project root."
            ),
            kind="write",
            risk="medium",  # type: ignore[arg-type]
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "diff": {"type": "string"},
                },
                "required": ["path", "diff"],
            },
            token_cost=120,
            priority=30,
            source="builtin",
        ),
        Tool(
            name="run_command",
            description=(
                "Run a shell command in the project root. Commands are "
                "classified by the security policy; BLOCK commands are never "
                "executed, ASK commands require user approval, SAFE commands run."
            ),
            kind="shell",
            risk="high",  # type: ignore[arg-type]
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            token_cost=100,
            priority=40,
            source="builtin",
        ),
    ]


# Vendor directories the router refuses to walk, mirroring
# context.manager.IGNORED_DIRS so the agent cannot use read_file to
# read into a vendored tree.
_IGNORED_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", ".next", ".worktrees",
})


@dataclass(slots=True)
class BuiltinToolHost:
    """In-process executor for the router's builtin tools.

    The agent invokes these through the MCP bridge; the host enforces
    the security policy and the project-root boundary. The host is
    deliberately separate from `tools.mcp_client.MCPClient` so the
    builtin surface can be unit-tested without subprocesses.
    """

    project_root: Path
    policy: CommandPolicy
    max_bytes: int = 40_000

    def _resolve(self, rel: str) -> Path | None:
        path = (self.project_root / rel).resolve()
        try:
            path.relative_to(self.project_root)
        except ValueError:
            return None
        if any(part in _IGNORED_DIRS for part in path.parts):
            return None
        return path

    def read_file(self, rel: str) -> dict[str, Any]:
        path = self._resolve(rel)
        if path is None or not path.is_file():
            return {"ok": False, "error": "not_found"}
        try:
            text = path.read_text(errors="replace")[: self.max_bytes]
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "content": text}

    def run_command(self, command: str) -> dict[str, Any]:
        decision, reason = self.policy.classify(command)
        if decision is Decision.BLOCK:
            return {"ok": False, "error": f"blocked: {reason}"}
        if decision is Decision.ASK:
            return {"ok": False, "error": f"requires_approval: {reason}"}
        return {"ok": True, "decision": decision.value, "reason": reason}
