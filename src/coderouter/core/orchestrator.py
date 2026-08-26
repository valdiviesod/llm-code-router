"""Application core: analyse -> plan -> route -> execute -> validate -> learn.

This is the only place that owns the end-to-end flow. It talks to adapters
exclusively through the registry and the AgentAdapter interface, so adding an
agent never touches this file.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..agents.base.registry import AgentRegistry
from ..config import Config
from ..context.manager import ContextManager
from ..errors import NoViableAgent
from ..git.worktree import IntegrationResult, Worktree, WorktreeManager
from ..logging import get_logger, log
from ..memory import Learner, MemoryInjector, MemoryStore
from ..routing.engine import RoutingEngine
from ..routing.llm_classifier import LLMClassifier
from ..scheduler import BatchingPolicy, SpeculativeConfig, is_speculative_eligible, race_attempts
from ..scheduler.policy import SchedulingPolicy
from ..security.policy import redact_secrets
from ..skills import SkillConfig, SkillInjector, SkillRegistry
from ..storage.db import Database
from ..tools import MCPServer, ToolRegistry, ToolSelector
from ..usage.manager import UsageManager
from ..usage.quota_pool import QuotaBook, QuotaPool
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
        self.classifier = LLMClassifier(config, self.registry, self.usage, db)
        self.quota_book = self._build_quota_book(config, db, self.registry)
        self.router = RoutingEngine(config, self.registry, self.usage, db,
                                    quota_book=self.quota_book)
        self.context = ContextManager(config.token_saving)
        self.validation = ValidationEngine()
        self._semaphore = asyncio.Semaphore(config.concurrency.globally)
        self.skill_registry = self._load_skill_registry(config, project_root=None)
        self.skill_injector = SkillInjector(
            budget_tokens=config.skills.budget_tokens
        )
        self.tool_registry = ToolRegistry()
        self.tool_selector = ToolSelector(self.tool_registry)
        self.scheduling_policy: SchedulingPolicy = BatchingPolicy()
        self.speculative_config = SpeculativeConfig(
            enabled=config.routing.speculative,
        )
        self.memory_store = MemoryStore(db)
        self.memory_learner = Learner(
            db, self.memory_store,
            config=None,  # Learner reads thresholds from its own default
        )
        self.memory_injector = MemoryInjector(
            self.memory_store, budget_tokens=config.memory.budget_tokens,
        )

    @staticmethod
    def _load_skill_registry(
        config: Config, project_root: Path | None
    ) -> SkillRegistry:
        """Build a registry from the configured search paths.

        The project path is resolved relative to `project_root` when given,
        else to the current working directory. The user-global path is taken
        verbatim from the config. If skills are disabled, the registry is
        empty but well-formed so the rest of the orchestrator can ignore the
        flag without branching everywhere.
        """
        if not config.skills.enabled:
            return SkillRegistry()
        project = (project_root or Path.cwd()) / config.skills.project_search_path
        user = config.skills.user_search_path
        return SkillRegistry.from_paths([project, user])

    @staticmethod
    def _build_quota_book(
        config: Config, db: Database, registry: AgentRegistry
    ) -> QuotaBook:
        """Translate `Config.quota_pools` (raw) into a `QuotaBook` (live).

        Every registered agent that does not appear in any configured pool
        ends up with one implicit pool, sized by its own `AgentConfig`
        limits. This is the bit that keeps the unconfigured case identical
        to v0.1.0: the implicit pool is the per-agent accounting.
        """
        # The *live* registry, not a fresh one: an injected registry (tests,
        # plugin-provided adapters) would otherwise get no pools at all, and
        # its usage would silently escape quota accounting.
        agent_ids = list(registry.ids)
        pools = [
            QuotaPool(
                id=cfg.id,
                kind=cfg.kind,  # type: ignore[arg-type]
                tier=cfg.tier,  # type: ignore[arg-type]
                agent_ids=tuple(cfg.agent_ids),
                window_hours=cfg.window_hours,
                limit_tokens=cfg.limit_tokens,
                weekly_limit_tokens=cfg.weekly_limit_tokens,
                reserve_percent=cfg.reserve_percent,
                enabled=cfg.enabled,
            )
            for cfg in config.quota_pools if cfg.id
        ]
        # Implicit per-agent pools: a single-agent pool that mirrors the
        # legacy AgentConfig limits, so v0.1.0 accounting carries over
        # verbatim when the user has not configured `quota_pools:`.
        for aid in agent_ids:
            agent_cfg = config.agent(aid)
            pools.append(QuotaPool(
                id=f"_implicit:{aid}",
                kind="subscription",
                tier="standard",
                agent_ids=(aid,),
                window_hours=agent_cfg.window_hours,
                limit_tokens=agent_cfg.window_limit_tokens,
                weekly_limit_tokens=agent_cfg.weekly_limit_tokens,
                reserve_percent=agent_cfg.reserve_percent,
                enabled=True,
            ))
        return QuotaBook.build(pools, agent_ids, db)

    # --- analysis -------------------------------------------------------
    async def analyze(self, prompt: str, project_root: Path) -> Task:
        c = await self.classifier.classify(prompt)
        task = Task(
            prompt=prompt,
            project_root=project_root,
            task_type=c.task_type,
            complexity=c.complexity,
            risk=c.risk,
            required_capabilities=c.required_capabilities,
            classification_source=c.source,
            classification_confidence=c.confidence,
        )
        bundle = await asyncio.to_thread(self.context.select, task)
        task.context_files = bundle.files
        log(logger, logging.INFO, "task classified", task_id=task.id,
            type=c.task_type, complexity=c.complexity.value, risk=c.risk.value,
            context_files=len(bundle.files), reasons=c.reasons,
            source=c.source, confidence=c.confidence)
        if self.config.memory.enabled:
            block = self.memory_injector.build(task)
            if block.block:
                task.prompt = self.memory_injector.render_into_prompt(task.prompt, block)
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

        self._inject_skills(task, adapter)
        await self._select_tools(task, adapter)
        self.db.save_task(task)

        # Claim the estimated tokens before executing. Between the routing
        # decision and the usage event, the spend is invisible to SQL; with
        # parallel tasks that window is where a pool gets overspent.
        reservation = self.quota_book.reserve(
            adapter.id, decision.estimated_usage.total_tokens,
            complexity=task.complexity,
            user_override=task.forced_agent is not None,
            task_id=task.id,
        )

        worktrees = WorktreeManager(task.project_root)
        run_id = new_id("run")
        checks: list[CheckResult] = []
        integration: IntegrationResult | None = None
        try:
            async with self._semaphore:
                if isolate:
                    async with worktrees.isolated(task.id, adapter.id) as wt:
                        workdir = wt.path if wt else task.project_root
                        result = await self._execute(adapter, task, decision, workdir)
                        # Validate *inside* the worktree, before merging. The
                        # attempt is what is being judged; only work that
                        # passes its own checks is merged back.
                        checks = await self._validate(workdir, result, run_id,
                                                      task, emit=emit)
                        if wt is not None:
                            integration = await self._integrate(
                                worktrees, wt, task, adapter.id, result, checks, emit=emit,
                            )
                else:
                    result = await self._execute(adapter, task, decision, task.project_root)
                    checks = await self._validate(task.project_root, result, run_id,
                                                  task, emit=emit)
        except BaseException:
            # Cancelled or crashed: the tokens were never spent, so the hold
            # must not outlive the attempt.
            if reservation is not None:
                self.quota_book.release(reservation)
            raise

        result.output = redact_secrets(result.output)
        self.db.save_run(run_id, task, result)
        # Settle only once the spend is on the books, so the hold is never
        # dropped before the SQL total that replaces it exists.
        if reservation is not None:
            self.quota_book.commit(reservation, result.total_tokens)
        self.db.audit("execute", task_id=task.id, agent_id=adapter.id,
                      decision=decision.reason, detail=f"success={result.success}")
        await emit("executed", {"task": task.id, "success": result.success,
                                "tokens": result.total_tokens})

        failed_checks = [c for c in checks if not c.passed and not c.skipped]
        succeeded = result.success and not failed_checks
        # Work that could not be merged back did not happen, however well the
        # agent reported it going.
        if integration is not None and integration.had_changes and not integration.merged:
            succeeded = False
            if result.error is None:
                result.error = f"changes not integrated: {integration.detail}"
        task.state = TaskState.DONE if succeeded else TaskState.FAILED
        self.db.save_task(task)

        outcome = TaskOutcome(task, decision, result, checks,
                              self.build_handoff(task, result, checks))

        if self.config.memory.enabled and self.config.memory.auto_learn:
            try:
                self.memory_learner.observe(str(task.project_root.resolve()))
            except Exception as exc:  # noqa: BLE001 - learning must never fail a run
                log(logger, logging.WARNING, "learner skipped",
                    task_id=task.id, error=str(exc))

        if not succeeded and self.config.routing.escalation:
            retried = await self._self_heal(
                task, outcome, mode_override=mode_override, isolate=isolate,
                on_event=on_event,
            )
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

    def _inject_skills(self, task: Task, adapter) -> None:
        """Build the skills block for the chosen agent and fold it into the prompt.

        Per-agent allow/deny rules come from the config. The block is appended
        to the task prompt; `task.skill_ids` is recorded for the audit trail.
        A disabled skills config or an empty registry is a no-op.
        """
        if not self.config.skills.enabled or len(self.skill_registry) == 0:
            return
        rules = self.config.agent_skill_config(adapter.id)
        config = SkillConfig(
            allowlist=frozenset(rules.allowlist),
            denylist=frozenset(rules.denylist),
        )
        block = self.skill_injector.build(
            task,
            self.skill_registry,
            adapter.capabilities.capabilities,
            config=config,
        )
        if not block.skill_ids:
            return
        task.prompt = self.skill_injector.render_into_prompt(task.prompt, block)
        task.skill_ids = list(block.skill_ids)
        log(logger, logging.INFO, "skills applied", task_id=task.id,
            agent_id=adapter.id, skills=",".join(block.skill_ids))

    async def _select_tools(self, task: Task, adapter) -> None:
        """Pick a budgeted set of tools for this task and adapter.

        Discovers MCP server tools first (best-effort), then runs the
        selector. The chosen tool ids are recorded on the task; the
        full tool list is not persisted at this point because the
        adapter is what actually decides which tools to advertise in
        its prompt — the router's job is just to keep the picker honest.
        """
        agent_cfg = self.config.agent(adapter.id)
        for server_cfg in agent_cfg.mcp_servers:
            self.tool_registry.register_mcp_server(MCPServer(
                name=server_cfg.name,
                command=server_cfg.command,
                args=tuple(server_cfg.args),
                env=dict(server_cfg.env),
            ))
        discovered: list = []
        if self.tool_registry.mcp_servers:
            grouped = await self.tool_registry.list_mcp_tools()
            for tools in grouped.values():
                discovered.extend(tools)
        native = await adapter.tools()
        if native:
            self.tool_registry.register_adapter(adapter, native)
        chosen = self.tool_selector.select(
            adapter, task, budget_tokens=agent_cfg.tool_budget_tokens,
            discovered=discovered,
        )
        task.selected_tool_ids = [t.name for t in chosen]
        if chosen:
            log(logger, logging.INFO, "tools selected", task_id=task.id,
                agent_id=adapter.id, tools=",".join(t.name for t in chosen))

    async def _self_heal(
        self, task: Task, outcome: TaskOutcome, *,
        mode_override: RoutingMode | None = None,
        isolate: bool = False,
        on_event: Event | None,
    ) -> TaskOutcome | None:
        """One escalation attempt on a different agent, carrying a compact
        failure report. Never re-sends the identical prompt.

        `isolate` is carried over deliberately: an escalation of an isolated
        run that wrote straight into the project tree would put two agents in
        one working tree, which is exactly what ADR-004 forbids."""
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
            classification_source=task.classification_source,
            classification_confidence=task.classification_confidence,
        )
        log(logger, logging.WARNING, "escalating after failure",
            task_id=task.id, to_agent=others[0].id)
        self.db.audit("escalate", task_id=task.id, agent_id=others[0].id,
                      detail="previous attempt failed")
        return await self.run_task(retry, mode_override=mode_override,
                                   isolate=isolate, on_event=on_event)

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

    async def _validate(
        self, workdir: Path, result: AgentResult, run_id: str, task: Task,
        *, emit: Event,
    ) -> list[CheckResult]:
        """Run the applicable checks against the tree the agent actually wrote.

        Validation costs real wall-clock time, so it only runs when something
        changed. `workdir` is the worktree when the run is isolated — checking
        the project root there would always see an unchanged tree and silently
        skip every check.
        """
        if not result.success:
            return []
        _, changed = await self.validation.git_diff(workdir)
        if not (changed or result.files_changed):
            return []
        checks = await self.validation.run(workdir)
        self.db.save_validation(run_id, checks)
        await emit("validated", {"task": task.id,
                                 "failed": [c.name for c in checks if not c.passed]})
        return checks

    async def _integrate(
        self, worktrees: WorktreeManager, wt: Worktree, task: Task, agent_id: str,
        result: AgentResult, checks: list[CheckResult], *, emit: Event,
    ) -> IntegrationResult:
        """Merge a successful attempt back, or discard a failed one.

        A conflicted merge keeps its branch: that branch is the only copy of
        the work, and throwing it away to keep the repository tidy would be
        the same bug this module was written to fix.
        """
        failed = [c for c in checks if not c.passed and not c.skipped]
        if not result.success or failed:
            reason = "agent failed" if not result.success else "validation failed"
            files = await worktrees.changed_files(wt)
            log(logger, logging.INFO, "discarding attempt", task_id=task.id,
                agent_id=agent_id, reason=reason, files=len(files))
            await emit("discarded", {"task": task.id, "reason": reason,
                                     "files": len(files)})
            return IntegrationResult(merged=False, detail=reason,
                                     had_changes=bool(files), branch=wt.branch,
                                     files=files)

        message = f"coderouter({agent_id}): {task.prompt.splitlines()[0][:60]}"
        integration = await worktrees.integrate(wt, message)
        if integration.conflicted:
            worktrees.keep_branch(wt)
        if integration.files:
            result.files_changed = list(
                dict.fromkeys([*result.files_changed, *integration.files])
            )
        log(logger, logging.INFO, "integration", task_id=task.id, agent_id=agent_id,
            merged=integration.merged, conflicted=integration.conflicted,
            files=len(integration.files), detail=integration.detail)
        await emit("integrated", {"task": task.id, "merged": integration.merged,
                                  "conflicted": integration.conflicted,
                                  "branch": integration.branch,
                                  "files": integration.files,
                                  "detail": integration.detail})
        self.db.audit("integrate", task_id=task.id, agent_id=agent_id,
                      decision=integration.branch or "",
                      detail=f"merged={integration.merged} {integration.detail}"[:300])
        return integration

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
            ready = self.scheduling_policy.decide_batch(graph)
            if not ready:
                break
            isolate = len(ready) > 1
            for task in ready:
                if previous_handoff and not task.handoff:
                    task.handoff = previous_handoff
            results = await asyncio.gather(*[
                self._dispatch_task(t, mode_override=mode_override,
                                    isolate=isolate, on_event=on_event)
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

    async def _dispatch_task(
        self,
        task: Task,
        *,
        mode_override: RoutingMode | None,
        isolate: bool,
        on_event: Event | None,
    ) -> TaskOutcome:
        """Run one task, optionally via the speculative race.

        Speculative races fire only when the task is eligible and
        `routing.speculative: true`. The decision is computed once;
        the top two candidates run in parallel and the first success
        wins. Speculative is **never** for HIGH/CRITICAL complexity.
        """
        if not is_speculative_eligible(task, self.speculative_config):
            return await self.run_task(
                task, mode_override=mode_override, isolate=isolate, on_event=on_event,
            )
        decision = await self.router.decide(task, mode_override=mode_override)
        alternatives = decision.alternatives[: self.speculative_config.max_attempts - 1]
        if not alternatives:
            return await self.run_task(
                task, mode_override=mode_override, isolate=isolate, on_event=on_event,
            )
        self.db.save_decision(decision)
        primary = self.registry.get(decision.selected_agent)
        if primary is None:
            return await self.run_task(
                task, mode_override=mode_override, isolate=isolate, on_event=on_event,
            )
        candidates: list[tuple] = [(primary, decision.selected_model)]
        for alt in alternatives:
            adapter = self.registry.get(alt.agent_id)
            if adapter is not None:
                candidates.append((adapter, alt.model))
        # Save the task once before the race so the audit log has a
        # consistent record even if both attempts crash.
        self.db.save_task(task)
        log(logger, logging.INFO, "speculative dispatch",
            task_id=task.id, agents=",".join(a.id for a, _ in candidates))

        # Each attempt goes through `run_task`, which already persists its own
        # run row, validation and task state. Keep the full outcome of every
        # attempt so the winner can be returned intact — re-persisting the
        # winning AgentResult here would double-count its tokens against the
        # quota, because usage is SUM()ed over the runs table.
        attempt_outcomes: dict[int, TaskOutcome] = {}

        async def _runner(t: Task, adapter, model) -> AgentResult:
            assert adapter is not None
            # Task is a slots dataclass, so `replace` — not `__dict__` — is
            # the way to copy it. The id is carried over on purpose: every
            # attempt is the same task, so they share one row.
            t_copy = replace(t, forced_agent=adapter.id)
            try:
                outcome = await self.run_task(
                    t_copy, mode_override=mode_override, isolate=True, on_event=on_event,
                )
                if outcome.result is None:
                    return AgentResult(
                        task_id=t.id, agent_id=adapter.id, model=model,
                        success=False, output="", error="no result",
                    )
                attempt_outcomes[id(outcome.result)] = outcome
                return outcome.result
            except Exception as exc:  # noqa: BLE001 - never crash the race
                return AgentResult(
                    task_id=t.id, agent_id=adapter.id, model=model,
                    success=False, output="", error=str(exc),
                )

        winning = await race_attempts(task, candidates, _runner)
        if winning is None:
            winning = AgentResult(
                task_id=task.id, agent_id=primary.id, model=decision.selected_model,
                success=False, output="", error="speculative produced no result",
            )
        won = attempt_outcomes.get(id(winning))
        if won is not None:
            # Mirror the winning attempt's state onto the caller's task object;
            # the DB row is already correct, the attempt shares this task id.
            task.state = won.task.state
            return TaskOutcome(task, decision, winning, won.checks, won.handoff)
        # No attempt produced an outcome (all raised, or the race was empty):
        # persist the synthesised failure so the audit log is not silent.
        winning.output = redact_secrets(winning.output)
        self.db.save_run(new_id("run"), task, winning)
        task.state = TaskState.FAILED
        self.db.save_task(task)
        return TaskOutcome(task, decision, winning, [], None)
