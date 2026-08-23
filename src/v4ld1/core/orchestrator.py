"""Application core: analyse -> plan -> route -> execute -> validate -> learn.

This is the only place that owns the end-to-end flow. It talks to adapters
exclusively through the registry and the AgentAdapter interface, so adding an
agent never touches this file.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..agents.base.registry import AgentRegistry
from ..config import Config
from ..context.manager import ContextManager
from ..errors import NoViableAgent
from ..git.worktree import WorktreeManager
from ..logging import get_logger, log
from ..routing.classifier import classify
from ..routing.engine import RoutingEngine
from ..security.policy import redact_secrets
from ..storage.db import Database
from ..usage.manager import UsageManager
from ..validation.engine import CheckResult, ValidationEngine
from .models import (
    AgentResult,
    HandoffPackage,
    RoutingDecision,
    RoutingMode,
    Task,
    TaskState,
    new_id,
)
from .task_graph import TaskGraph, build_graph

logger = get_logger("orchestrator")

Event = Callable[[str, dict], Awaitable[None]]


@dataclass(slots=True)
class TaskOutcome:
    task: Task
    decision: RoutingDecision | None
    result: AgentResult | None
    checks: list[CheckResult] = field(default_factory=list)
    handoff: HandoffPackage | None = None


class Orchestrator:
    def __init__(self, config: Config, db: Database, registry: AgentRegistry | None = None):
        self.config = config
        self.db = db
        self.registry = registry or AgentRegistry(config)
        self.usage = UsageManager(config, db)
        self.router = RoutingEngine(config, self.registry, self.usage, db)
        self.context = ContextManager(config.token_saving)
        self.validation = ValidationEngine()
        self._semaphore = asyncio.Semaphore(config.concurrency.globally)

    # --- analysis -------------------------------------------------------
    def analyze(self, prompt: str, project_root: Path) -> Task:
        c = classify(prompt)
        task = Task(
            prompt=prompt,
            project_root=project_root,
            task_type=c.task_type,
            complexity=c.complexity,
            risk=c.risk,
            required_capabilities=c.required_capabilities,
        )
        bundle = self.context.select(task)
        task.context_files = bundle.files
        log(logger, logging.INFO, "task classified", task_id=task.id,
            type=c.task_type, complexity=c.complexity.value, risk=c.risk.value,
            context_files=len(bundle.files), reasons=c.reasons)
        return task

    def plan(self, task: Task) -> TaskGraph:
        return build_graph(task)

    # --- execution ------------------------------------------------------
    async def run_task(
        self,
        task: Task,
        *,
        mode_override: RoutingMode | None = None,
        isolate: bool = False,
        on_event: Event | None = None,
    ) -> TaskOutcome:
        async def emit(kind: str, payload: dict) -> None:
            if on_event:
                await on_event(kind, payload)

        self.db.save_task(task)
        decision = await self.router.decide(task, mode_override=mode_override)
        self.db.save_decision(decision)
        await emit("routed", {"task": task.id, "agent": decision.selected_agent,
                              "reason": decision.reason})

        adapter = self.registry.get(decision.selected_agent)
        if adapter is None:  # pragma: no cover - registry just produced this id
            raise NoViableAgent(decision.selected_agent)

        task.state = TaskState.RUNNING
        self.db.save_task(task)

        worktrees = WorktreeManager(task.project_root)
        async with self._semaphore:
            if isolate:
                async with worktrees.isolated(task.id, adapter.id) as wt:
                    workdir = wt.path if wt else task.project_root
                    result = await self._execute(adapter, task, decision, workdir)
            else:
                result = await self._execute(adapter, task, decision, task.project_root)

        run_id = new_id("run")
        result.output = redact_secrets(result.output)
        self.db.save_run(run_id, task, result)
        self.db.audit("execute", task_id=task.id, agent_id=adapter.id,
                      decision=decision.reason, detail=f"success={result.success}")
        await emit("executed", {"task": task.id, "success": result.success,
                                "tokens": result.total_tokens})

        checks: list[CheckResult] = []
        # Validation costs real wall-clock time, so it only runs when the tree
        # actually changed. No diff, no checks.
        _, changed = await self.validation.git_diff(task.project_root)
        if result.success and (changed or result.files_changed):
            checks = await self.validation.run(task.project_root)
            self.db.save_validation(run_id, checks)
            await emit("validated", {"task": task.id,
                                     "failed": [c.name for c in checks if not c.passed]})

        failed_checks = [c for c in checks if not c.passed and not c.skipped]
        succeeded = result.success and not failed_checks
        task.state = TaskState.DONE if succeeded else TaskState.FAILED
        self.db.save_task(task)

        outcome = TaskOutcome(task, decision, result, checks,
                              self.build_handoff(task, result, checks))

        if not succeeded and self.config.routing.escalation:
            retried = await self._self_heal(task, outcome, on_event=on_event)
            if retried is not None:
                return retried
        return outcome

    async def _execute(self, adapter, task: Task, decision: RoutingDecision, workdir: Path):
        original_root = task.project_root
        task.project_root = workdir
        try:
            return await adapter.execute(task, model=decision.selected_model)
        finally:
            task.project_root = original_root

    async def _self_heal(
        self, task: Task, outcome: TaskOutcome, *, on_event: Event | None
    ) -> TaskOutcome | None:
        """One escalation attempt on a different agent, carrying a compact
        failure report. Never re-sends the identical prompt."""
        if task.attempt + 1 >= self.config.routing.max_attempts:
            return None
        others = [a for a in self.registry.available()
                  if outcome.decision and a.id != outcome.decision.selected_agent]
        if not others:
            return None

        retry = Task(
            prompt=task.prompt,
            parent_id=task.id,
            project_root=task.project_root,
            task_type=task.task_type,
            complexity=task.complexity,
            risk=task.risk,
            required_capabilities=task.required_capabilities,
            context_files=task.context_files,
            forced_agent=others[0].id,
            handoff=outcome.handoff,
            attempt=task.attempt + 1,
        )
        log(logger, logging.WARNING, "escalating after failure",
            task_id=task.id, to_agent=others[0].id)
        self.db.audit("escalate", task_id=task.id, agent_id=others[0].id,
                      detail="previous attempt failed")
        return await self.run_task(retry, on_event=on_event)

    def build_handoff(
        self, task: Task, result: AgentResult, checks: list[CheckResult]
    ) -> HandoffPackage:
        failed = [c for c in checks if not c.passed and not c.skipped]
        return HandoffPackage(
            objective=task.prompt[:400],
            files_changed=result.files_changed[:20],
            findings=[self.context.summarize_output(result.output, 600)] if result.output else [],
            tests="; ".join(f"{c.name}: {'pass' if c.passed else 'FAIL'}"
                            for c in checks if not c.skipped),
            errors=([result.error] if result.error else []) + [c.detail[:300] for c in failed],
            remaining_work=[f"re-run {c.name} until it passes" for c in failed],
            risks=task.risk.value,
        )

    # --- graph execution ------------------------------------------------
    async def run_graph(
        self, graph: TaskGraph, *, mode_override: RoutingMode | None = None,
        on_event: Event | None = None,
    ) -> list[TaskOutcome]:
        """Run ready tasks in parallel, honouring dependencies and concurrency.

        Parallel siblings always get isolated worktrees; a single ready task
        works in place because there is nobody to collide with."""
        outcomes: list[TaskOutcome] = []
        previous_handoff: HandoffPackage | None = None
        while not graph.finished():
            ready = graph.ready()
            if not ready:
                break
            isolate = len(ready) > 1
            for task in ready:
                if previous_handoff and not task.handoff:
                    task.handoff = previous_handoff
            results = await asyncio.gather(*[
                self.run_task(t, mode_override=mode_override, isolate=isolate,
                              on_event=on_event)
                for t in ready
            ], return_exceptions=True)
            for task, outcome in zip(ready, results, strict=True):
                if isinstance(outcome, BaseException):
                    task.state = TaskState.FAILED
                    log(logger, logging.ERROR, "task raised", task_id=task.id,
                        error=str(outcome))
                    continue
                outcomes.append(outcome)
                previous_handoff = outcome.handoff
        return outcomes
