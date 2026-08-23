"""The one interface the core knows about.

Adding an agent means implementing this class and registering it. Nothing in
core/, routing/, scheduler/, tui/ or storage/ may branch on a concrete agent id.
"""

from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from ...config import AgentConfig
from ...core.models import (
    AgentCapabilities,
    AgentResult,
    HealthStatus,
    ModelInfo,
    Task,
    UsageEstimate,
    UsageInfo,
    UsageStatus,
)

# Rough bytes-per-token used only for pre-flight estimates. Real numbers always
# come back from the provider's own reported usage after a run.
BYTES_PER_TOKEN = 4


class AgentAdapter(ABC):
    """Base adapter. Subclasses translate Task -> CLI invocation -> AgentResult."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self._running: dict[str, asyncio.subprocess.Process] = {}

    # --- identity -------------------------------------------------------
    @property
    @abstractmethod
    def id(self) -> str:
        """Stable identifier, e.g. "claude". Used as config key and DB key."""

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> AgentCapabilities: ...

    @property
    def command(self) -> str:
        return self.config.command or self.default_command

    @property
    @abstractmethod
    def default_command(self) -> str:
        """Binary name to use when config does not override it."""

    # --- lifecycle ------------------------------------------------------
    @abstractmethod
    async def health_check(self) -> HealthStatus: ...

    @abstractmethod
    async def get_models(self) -> list[ModelInfo]: ...

    async def get_usage(self) -> UsageInfo:
        """Providers that expose no usage API return UNKNOWN and let the
        UsageManager's own accounting fill in ESTIMATED figures."""
        return UsageInfo(agent_id=self.id, windows=[], status=UsageStatus.UNKNOWN)

    async def estimate(self, task: Task) -> UsageEstimate:
        prompt_bytes = len(task.prompt.encode())
        if task.handoff:
            prompt_bytes += len(task.handoff.render().encode())
        context_bytes = 0
        for path in task.context_files:
            try:
                context_bytes += path.stat().st_size
            except OSError:
                continue
        input_tokens = (prompt_bytes + context_bytes) // BYTES_PER_TOKEN + 2_000
        output_tokens = int(input_tokens * (0.3 + 0.2 * task.complexity.rank))
        return UsageEstimate(input_tokens, output_tokens, confidence=0.4)

    @abstractmethod
    async def execute(
        self,
        task: Task,
        *,
        model: str | None = None,
        on_event: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentResult:
        """Run the task to completion. Must never raise for provider-side
        failures: return AgentResult(success=False, error=...) instead."""

    async def cancel(self, task_id: str) -> None:
        proc = self._running.pop(task_id, None)
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()

    # --- helpers for subclasses ----------------------------------------
    def binary_available(self) -> str | None:
        return shutil.which(self.command)

    async def _run(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        stdin_data: str | None = None,
        timeout: int | None = None,
        task_id: str | None = None,
    ) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if task_id:
            self._running[task_id] = proc
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(stdin_data.encode() if stdin_data is not None else None),
                timeout=timeout or self.config.timeout_s,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        finally:
            if task_id:
                self._running.pop(task_id, None)
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
