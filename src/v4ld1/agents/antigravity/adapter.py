"""Antigravity CLI adapter.

Verified against agy 1.1.17. Non-interactive execution uses
`agy -p --output-format json`. `agy models` lists real model ids. Like Claude
Code, it exposes no quota endpoint, so usage is derived from run accounting.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from ...core.models import (
    AgentCapabilities,
    AgentResult,
    Capability,
    Completion,
    HealthStatus,
    ModelInfo,
    Task,
    UsageStatus,
)
from ..base.adapter import AgentAdapter
from ..base.registry import register
from ..claude.adapter import _files_from_text

_CAPS = AgentCapabilities(
    capabilities=frozenset({
        Capability.CODE_EDIT, Capability.SHELL, Capability.LONG_CONTEXT,
        Capability.PLANNING, Capability.MCP, Capability.STREAMING,
        Capability.CANCELLATION, Capability.MODEL_SELECTION,
        Capability.STRUCTURED_COMPLETION,
    }),
    max_context_tokens=1_000_000,
)


@register
class AntigravityAdapter(AgentAdapter):
    @property
    def id(self) -> str:
        return "antigravity"

    @property
    def display_name(self) -> str:
        return "Antigravity"

    @property
    def default_command(self) -> str:
        return "agy"

    @property
    def capabilities(self) -> AgentCapabilities:
        return _CAPS

    async def health_check(self) -> HealthStatus:
        binary = self.binary_available()
        if not binary:
            return HealthStatus(
                self.id, False, f"{self.command!r} not found on PATH",
                remediation="Install the Antigravity CLI and re-run `v4ld1 doctor`",
            )
        try:
            code, out, err = await self._run([binary, "--version"], timeout=30)
        except Exception as exc:  # noqa: BLE001
            return HealthStatus(self.id, False, str(exc))
        if code != 0:
            return HealthStatus(self.id, False, err.strip() or "version check failed")
        return HealthStatus(self.id, True, "ok", version=out.strip() or None)

    async def get_models(self) -> list[ModelInfo]:
        binary = self.binary_available()
        if not binary:
            return []
        # `agy models` hits the network and occasionally returns empty on a
        # transient blip. One retry keeps a network hiccup from being reported
        # to the user as an authentication problem.
        out = ""
        for attempt in range(2):
            try:
                code, out, _ = await self._run([binary, "models"], timeout=60)
            except Exception:  # noqa: BLE001
                return []
            if code == 0 and "\t" in out:
                break
            if attempt == 0:
                await asyncio.sleep(1)
        models: list[ModelInfo] = []
        for line in out.splitlines():
            if "\t" not in line:
                continue
            mid, _, name = line.partition("\t")
            models.append(ModelInfo(mid.strip(), name.strip(), self.id))
        return models

    async def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        schema: dict | None = None,
        model: str | None = None,
        timeout: int = 120,
    ) -> Completion | None:
        """One-shot question, using this CLI's own schema enforcement.

        `--json-schema` takes a schema string or a path and makes the CLI return
        a validated `structured_output` object, so the answer does not have to
        survive prose parsing. Verified against agy 1.1.17: a trivial call still
        bills ~23k tokens, so this is cheap relative to a task, not cheap.

        Go flag parsing means every flag has to precede the attached
        `--print=<prompt>` form, exactly as in execute().
        """
        binary = self.binary_available()
        if not binary:
            return None
        with tempfile.TemporaryDirectory() as sandbox:
            argv = [binary, "--output-format", "json", "--disable-slash-commands"]
            if schema:
                schema_path = Path(sandbox) / "schema.json"
                schema_path.write_text(json.dumps(schema))
                argv += ["--json-schema", str(schema_path)]
            model = model or self.config.default_model
            if model:
                argv += ["--model", model]
            text = f"{system}\n\n{prompt}" if system else prompt
            argv.append(f"--print={text}")
            try:
                code, out, _ = await self._run(argv, cwd=sandbox, timeout=timeout)
            except (TimeoutError, OSError):
                return None
        if code != 0:
            return None
        payload: dict = {}
        for line in reversed(out.strip().splitlines()):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        if not payload or str(payload.get("status", "SUCCESS")).upper() != "SUCCESS":
            return None
        usage = payload.get("usage") or {}
        structured = payload.get("structured_output")
        return Completion(
            text=str(payload.get("response") or ""),
            agent_id=self.id,
            model=payload.get("model") or model,
            input_tokens=(int(usage.get("input_tokens", 0) or 0)
                          + int(usage.get("cache_read_tokens", 0) or 0)),
            output_tokens=(int(usage.get("output_tokens", 0) or 0)
                           + int(usage.get("thinking_tokens", 0) or 0)),
            structured=structured if isinstance(structured, dict) else None,
        )

    def _argv(self, model: str | None, prompt: str) -> list[str]:
        # agy uses Go flag parsing: --print takes its value attached, and any
        # other flag must come before it or it is swallowed as the prompt.
        argv = [self.command, "--output-format", "json"]
        model = model or self.config.default_model
        if model:
            argv += ["--model", model]
        argv += self.config.extra_args
        argv.append(f"--print={prompt}")
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
        argv = self._argv(model, prompt)
        try:
            code, out, err = await self._run(
                argv, cwd=str(task.project_root), task_id=task.id
            )
        except TimeoutError:
            return AgentResult(
                task.id, self.id, model, False, "", duration_s=time.monotonic() - started,
                error=f"timed out after {self.config.timeout_s}s",
            )
        return self.parse_result(task, model, code, out, err, time.monotonic() - started)

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
        text = str(payload.get("response") or payload.get("result") or out.strip())
        usage = payload.get("usage") or {}
        status = str(payload.get("status", "")).upper()
        is_error = code != 0 or (status not in ("", "SUCCESS"))
        return AgentResult(
            task_id=task.id,
            agent_id=self.id,
            model=payload.get("model") or model,
            success=not is_error,
            output=text,
            files_changed=_files_from_text(text),
            input_tokens=(int(usage.get("input_tokens", 0) or 0)
                          + int(usage.get("cache_read_tokens", 0) or 0)),
            output_tokens=(int(usage.get("output_tokens", 0) or 0)
                           + int(usage.get("thinking_tokens", 0) or 0)),
            usage_status=UsageStatus.CONFIRMED if usage else UsageStatus.ESTIMATED,
            duration_s=duration,
            error=(err.strip() or text) if is_error else None,
            session_id=payload.get("conversation_id") or payload.get("session_id"),
        )
