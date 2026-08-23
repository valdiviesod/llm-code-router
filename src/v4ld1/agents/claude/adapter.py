"""Claude Code adapter.

Verified against Claude Code 2.1.239. Non-interactive execution uses
`claude -p --output-format json`, which returns a single JSON object carrying
the result text, session id and a `usage` block with real token counts. There is
no CLI command that reports subscription quota, so get_usage() stays UNKNOWN and
the UsageManager derives ESTIMATED windows from those per-run token counts.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from collections.abc import Awaitable, Callable

from ...core.models import (
    AgentCapabilities,
    AgentResult,
    Capability,
    Completion,
    HealthStatus,
    ModelInfo,
    Task,
    ToolCall,
    UsageStatus,
)
from ..base.adapter import AgentAdapter
from ..base.registry import register

_CAPS = AgentCapabilities(
    capabilities=frozenset({
        Capability.CODE_EDIT, Capability.SHELL, Capability.LONG_CONTEXT,
        Capability.DEEP_REASONING, Capability.PLANNING, Capability.REVIEW,
        Capability.MCP, Capability.STREAMING, Capability.CANCELLATION,
        Capability.MODEL_SELECTION, Capability.STRUCTURED_COMPLETION,
    }),
    max_context_tokens=200_000,
)

# Model aliases accepted by `--model`. Concrete ids are resolved by the CLI
# itself; we only advertise the aliases so the router has something to pick.
_MODELS = [
    ("opus", "Claude Opus"),
    ("sonnet", "Claude Sonnet"),
    ("haiku", "Claude Haiku"),
]


@register
class ClaudeCodeAdapter(AgentAdapter):
    @property
    def id(self) -> str:
        return "claude"

    @property
    def display_name(self) -> str:
        return "Claude Code"

    @property
    def default_command(self) -> str:
        return "claude"

    @property
    def capabilities(self) -> AgentCapabilities:
        return _CAPS

    async def health_check(self) -> HealthStatus:
        binary = self.binary_available()
        if not binary:
            return HealthStatus(
                self.id, False, f"{self.command!r} not found on PATH",
                remediation="Install Claude Code: https://claude.com/claude-code",
            )
        try:
            code, out, err = await self._run([binary, "--version"], timeout=30)
        except Exception as exc:  # noqa: BLE001 - health check must never raise
            return HealthStatus(self.id, False, str(exc))
        if code != 0:
            return HealthStatus(self.id, False, err.strip() or "version check failed")
        version = out.strip().split()[0] if out.strip() else None
        return HealthStatus(self.id, True, "ok", version=version)

    async def get_models(self) -> list[ModelInfo]:
        return [ModelInfo(mid, name, self.id, supports_reasoning_effort=True)
                for mid, name in _MODELS]

    async def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        schema: dict | None = None,
        model: str | None = None,
        timeout: int = 120,
    ) -> Completion | None:
        """One-shot question, kept as cheap as this CLI allows.

        Measured against Claude Code 2.1.239: a bare `-p` call already bills
        ~31k cached input tokens, because the CLI ships its full tool preamble
        and discovers project memory from the working directory. Running from an
        empty temporary directory with `--strict-mcp-config` and
        `--no-session-persistence` cuts that to ~16k. It is not free and the
        caller is expected to know that.

        `--bare` would be cheaper still and is deliberately not used: it forces
        ANTHROPIC_API_KEY authentication, and the entire point here is to spend
        the subscription the user already pays for.

        This CLI has no schema flag, so `schema` only shapes the instructions
        and `structured` stays None; the caller validates the parsed JSON.
        """
        binary = self.binary_available()
        if not binary:
            return None
        argv = [binary, "-p", "--output-format", "json",
                "--strict-mcp-config", "--no-session-persistence"]
        model = model or self.config.default_model
        if model:
            argv += ["--model", model]
        if system:
            argv += ["--system-prompt", system]
        argv.append(prompt)
        # An empty cwd keeps CLAUDE.md discovery — and the repository itself —
        # out of a call that has no business reading either.
        with tempfile.TemporaryDirectory() as sandbox:
            try:
                code, out, _ = await self._run(argv, cwd=sandbox, timeout=timeout)
            except (TimeoutError, OSError):
                return None
        if code != 0:
            return None
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or payload.get("is_error"):
            return None
        usage = payload.get("usage") or {}
        return Completion(
            text=str(payload.get("result") or ""),
            agent_id=self.id,
            model=model,
            input_tokens=(int(usage.get("input_tokens", 0) or 0)
                          + int(usage.get("cache_read_input_tokens", 0) or 0)
                          + int(usage.get("cache_creation_input_tokens", 0) or 0)),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
        )

    def _argv(self, task: Task, model: str | None) -> list[str]:
        argv = [self.command, "-p", "--output-format", "json"]
        model = model or self.config.default_model
        if model:
            argv += ["--model", model]
        argv += self.config.extra_args
        return argv

    async def execute(
        self,
        task: Task,
        *,
        model: str | None = None,
        on_event: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentResult:
        prompt = task.prompt if not task.handoff else f"{task.handoff.render()}\n\n{task.prompt}"
        started = time.monotonic()
        argv = self._argv(task, model) + [prompt]
        try:
            code, out, err = await self._run(
                argv, cwd=str(task.project_root), stdin_data="", task_id=task.id
            )
        except TimeoutError:
            return AgentResult(
                task.id, self.id, model, False, "", duration_s=time.monotonic() - started,
                error=f"timed out after {self.config.timeout_s}s",
            )
        return self.parse_result(task, model, code, out, err, time.monotonic() - started)

    # Parsing is a pure function so contract tests can exercise it without a CLI.
    def parse_result(
        self, task: Task, model: str | None, code: int, out: str, err: str, duration: float
    ) -> AgentResult:
        payload: dict = {}
        for line in reversed(out.strip().splitlines()):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        text = str(payload.get("result", out.strip()))
        usage = payload.get("usage") or {}
        is_error = bool(payload.get("is_error")) or code != 0
        return AgentResult(
            task_id=task.id,
            agent_id=self.id,
            model=payload.get("model") or model,
            success=not is_error,
            output=text,
            files_changed=_files_from_text(text),
            tool_calls=[ToolCall(name=t) for t in payload.get("tools_used", []) or []],
            input_tokens=(
                int(usage.get("input_tokens", 0) or 0)
                + int(usage.get("cache_read_input_tokens", 0) or 0)
                + int(usage.get("cache_creation_input_tokens", 0) or 0)
            ),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            usage_status=UsageStatus.CONFIRMED if usage else UsageStatus.ESTIMATED,
            duration_s=duration,
            cost_usd=payload.get("total_cost_usd"),
            error=(err.strip() or text) if is_error else None,
            session_id=payload.get("session_id"),
        )


_FILE_RE = re.compile(r"(?:^|\s)([\w./-]+\.[A-Za-z0-9]{1,8})(?::\d+)?\b")


def _files_from_text(text: str) -> list[str]:
    """Best-effort file extraction from prose. Authoritative diffs come from
    the ValidationEngine reading git, not from parsing agent output."""
    seen: list[str] = []
    for match in _FILE_RE.finditer(text):
        path = match.group(1)
        if path.startswith(("http", "www.")):
            continue
        if path not in seen:
            seen.append(path)
    return seen[:50]
