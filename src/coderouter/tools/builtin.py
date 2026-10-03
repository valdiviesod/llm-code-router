"""The router's own safe tool surface.

These are tools the router *exposes* to the agent through the MCP bridge
(`router mcp-serve`), not tools the agent invokes through the router's good
intent. They are deliberately narrow: a small read/edit/grep surface over the
project tree, and one shell tool whose every call passes four gates before a
process exists:

1. the **permissions engine** (allow/deny/ask per capability),
2. the **command policy** (BLOCK/ASK/SAFE on the command string),
3. the **sandbox** (path boundary, rlimits, optional network namespace),
4. the **approval flow** (an ASK verdict from either engine needs a human).

Every output is bounded by `output_bounds` — collapsed repeats for logs,
changed-files-first for edits — because the cheapest token is the one never
sent.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import Risk
from ..security.permissions import PermissionsEngine
from ..security.policy import CommandPolicy, Decision
from ..security.sandbox import Sandbox, SandboxConfig
from .models import Tool
from .output_bounds import bound_output

#: An ASK verdict is satisfied by a human saying yes. (command, reason) -> bool.
Approver = Callable[[str, str], bool]

_GIT_PUSH = re.compile(r"\bgit\s+push\b")

#: Cap on files scanned per grep and bytes read per file. A search that would
#: need more than this is a vendored tree, and vendored trees are refused
#: outright by the walk anyway.
_GREP_MAX_FILES = 20_000
_GREP_MAX_FILE_BYTES = 2_000_000


def builtin_tools() -> list[Tool]:
    return [
        Tool(
            name="read_file",
            description=(
                "Read a project file. Returns the contents up to a token budget. "
                "Files in vendor dirs (node_modules, .venv, etc.) are rejected."
            ),
            kind="read",
            risk=Risk.LOW,
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
            risk=Risk.LOW,
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
                "Patch a project file by exact replacement: `old_string` must "
                "appear exactly once in the file and is replaced by "
                "`new_string`. Paths outside the project root are rejected, as "
                "is any write the permissions engine denies."
            ),
            kind="write",
            risk=Risk.MEDIUM,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
            token_cost=120,
            priority=30,
            source="builtin",
        ),
        Tool(
            name="run_command",
            description=(
                "Run a shell command in the project root, bounded: commands "
                "are classified by the security policy and the permissions "
                "engine (BLOCK is never executed, ASK needs approval), run "
                "under resource limits, and their output is truncated. "
                "Returns the exit code and bounded combined output."
            ),
            kind="shell",
            risk=Risk.HIGH,
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

_DECISION_RANK = {Decision.SAFE: 0, Decision.ASK: 1, Decision.BLOCK: 2}


@dataclass(slots=True)
class BuiltinToolHost:
    """In-process executor for the router's builtin tools.

    The agent invokes these through the MCP bridge; the host enforces the
    security policy, the permissions engine, the project-root boundary and
    the sandbox. The host is deliberately separate from
    `tools.mcp_client.MCPClient` so the builtin surface can be unit-tested
    without subprocesses.
    """

    project_root: Path
    policy: CommandPolicy
    permissions: PermissionsEngine | None = None
    sandbox: Sandbox | None = None
    #: Satisfies ASK verdicts. None means every ASK is denied: refusing
    #: unattended is the safe default; the interactive CLI installs a
    #: prompting approver.
    approver: Approver | None = None
    max_bytes: int = 40_000
    command_timeout_s: int = 120
    #: When set (the MCP bridge does this per run), only these tool names
    #: are callable — this is what makes the selector's budget mean something.
    allowed_tools: frozenset[str] = field(default_factory=frozenset)

    def _sandbox(self) -> Sandbox:
        return self.sandbox or Sandbox(SandboxConfig())

    def _permission(self, capability: str, detail: str = "") -> tuple[Decision, str]:
        if self.permissions is None:
            return Decision.SAFE, f"no permissions engine; {capability} ungated"
        return self.permissions.check(capability, detail)

    def _resolve(self, rel: str) -> Path | None:
        root = self.project_root.resolve()
        path = (root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None
        if any(part in _IGNORED_DIRS for part in path.parts):
            return None
        return path

    # --- dispatch (the MCP bridge entry point) --------------------------

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one builtin tool by name. Unknown names and tools outside
        this run's selection are refused before anything executes."""
        if name not in {"read_file", "grep", "edit_file", "run_command"}:
            return {"ok": False, "error": f"unknown tool: {name}"}
        if self.allowed_tools and name not in self.allowed_tools:
            return {"ok": False,
                    "error": f"tool {name!r} is not in this run's selection"}
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "read_file": lambda a: self.read_file(a.get("path", "")),
            "grep": lambda a: self.grep(a.get("pattern", ""),
                                        int(a.get("max_results", 50) or 50)),
            "edit_file": lambda a: self.edit_file(
                a.get("path", ""), a.get("old_string", ""), a.get("new_string", "")),
            "run_command": lambda a: self.run_command(a.get("command", "")),
        }
        try:
            return handlers[name](arguments)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": f"bad arguments: {exc}"}

    # --- tools ----------------------------------------------------------

    def read_file(self, rel: str) -> dict[str, Any]:
        decision, why = self._permission("filesystem.read", rel)
        if decision is Decision.BLOCK:
            return {"ok": False, "error": f"denied: {why}"}
        path = self._resolve(rel)
        if path is None or not path.is_file():
            return {"ok": False, "error": "not_found"}
        try:
            text = path.read_text(errors="replace")[: self.max_bytes]
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "content": text}

    def grep(self, pattern: str, max_results: int = 50) -> dict[str, Any]:
        """Substring search with file:line:column anchors, project-wide.

        Vendored trees are skipped, binary files are skipped, and the result
        is capped at `max_results` matches with a `truncated` flag so the
        caller knows the search was cut short rather than exhaustive.
        """
        if not pattern:
            return {"ok": False, "error": "empty pattern"}
        decision, why = self._permission("filesystem.read", pattern)
        if decision is Decision.BLOCK:
            return {"ok": False, "error": f"denied: {why}"}
        root = self.project_root.resolve()
        matches: list[dict[str, Any]] = []
        scanned = 0
        truncated = False
        for path in self._walk(root):
            scanned += 1
            if scanned > _GREP_MAX_FILES:
                truncated = True
                break
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:1024] or len(raw) > _GREP_MAX_FILE_BYTES:
                continue
            text = raw.decode(errors="replace")
            rel = str(path.relative_to(root))
            for lineno, line in enumerate(text.splitlines(), start=1):
                col = line.find(pattern)
                if col >= 0:
                    matches.append({
                        "file": rel, "line": lineno, "col": col + 1,
                        "text": line.strip()[:200],
                    })
                    if len(matches) >= max_results:
                        truncated = True
                    if truncated:
                        break
            if truncated:
                break
        rendered = "\n".join(
            f"{m['file']}:{m['line']}:{m['col']}: {m['text']}" for m in matches)
        return {
            "ok": True, "matches": matches, "count": len(matches),
            "truncated": truncated,
            "output": bound_output(rendered, "prose", 4_000),
        }

    def edit_file(self, rel: str, old_string: str, new_string: str) -> dict[str, Any]:
        """Exact-match replacement. `old_string` must be unique in the file —
        a precise edit the caller can reason about, not a regex lottery."""
        decision, why = self._permission("filesystem.write", rel)
        if decision is Decision.BLOCK:
            return {"ok": False, "error": f"denied: {why}"}
        if decision is Decision.ASK and not self._approve(f"edit {rel}", why):
            return {"ok": False, "error": f"requires_approval: {why}"}
        path = self._resolve(rel)
        if path is None or not path.is_file():
            return {"ok": False, "error": "not_found"}
        if not old_string:
            return {"ok": False, "error": "old_string is empty"}
        try:
            text = path.read_text(errors="replace")
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        count = text.count(old_string)
        if count == 0:
            return {"ok": False, "error": "old_string not found in file"}
        if count > 1:
            return {"ok": False,
                    "error": f"old_string appears {count} times; make it unique"}
        try:
            path.write_text(text.replace(old_string, new_string, 1))
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        added = new_string.count("\n") + 1
        removed = old_string.count("\n") + 1
        return {
            "ok": True, "path": rel,
            "output": bound_output(
                f"changed {rel}: -{removed}/+{added} lines", "diff", 1_000),
        }

    def run_command(self, command: str) -> dict[str, Any]:
        """Execute a shell command through every gate, bounded.

        BLOCK from either the command policy or the permissions engine is
        final. ASK from either needs the approver; with no approver
        configured the command is refused — an unattended router must not
        invent a yes. SAFE commands run inside the sandbox with their output
        collapsed and truncated.
        """
        if not command.strip():
            return {"ok": False, "error": "empty command"}
        verdicts: list[tuple[Decision, str]] = [self.policy.classify(command)]
        verdicts.append(self._permission("shell.execute", command))
        if _GIT_PUSH.search(command):
            verdicts.append(self._permission("git.push", command))
        decision, reason = max(verdicts, key=lambda v: _DECISION_RANK[v[0]])
        # The reason has its own prefix (matches block rule, permissions rule
        # for X, ...). Re-prefix to a single, predictable form so callers
        # can branch on it.
        if decision is Decision.BLOCK:
            return {"ok": False, "error": f"blocked: {reason}"}
        if decision is Decision.ASK and not self._approve(command, reason):
            return {"ok": False, "error": f"requires_approval: {reason}"}
        code, out, err = self._sandbox().run(
            ["/bin/sh", "-c", command],
            cwd=self.project_root, timeout_s=self.command_timeout_s,
        )
        output = bound_output(out if out.strip() else err, "log", 4_000)
        return {
            "ok": code == 0, "exit_code": code, "command": command,
            "output": output,
        }

    def _approve(self, command: str, reason: str) -> bool:
        return bool(self.approver and self.approver(command, reason))

    def _walk(self, root: Path):
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in _IGNORED_DIRS for part in path.parts):
                continue
            yield path
