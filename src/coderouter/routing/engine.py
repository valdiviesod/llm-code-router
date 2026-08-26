"""Weighted-scoring routing engine.

No ML. Each candidate agent gets a score in 0..1 built from named, inspectable
factors; the weights per routing mode are the only knobs. Because every factor
is a named contribution, the RoutingDecision can explain itself in the TUI.

A future ML ranker plugs in by implementing score_candidates(); everything else
(capability filtering, quota veto, decision shape) stays.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from ..agents.base.adapter import AgentAdapter
from ..agents.base.registry import AgentRegistry
from ..config import Config
from ..core.models import (
    Candidate,
    Capability,
    Complexity,
    RoutingDecision,
    RoutingMode,
    Task,
    UsageEstimate,
)
from ..errors import NoViableAgent
from ..storage.db import Database
from ..usage.manager import UsageManager
from ..usage.quota_pool import QuotaBook

# Per-mode weights. They must sum to 1.0 within each mode.
WEIGHTS: dict[RoutingMode, dict[str, float]] = {
    RoutingMode.ECONOMY:  {"quota": 0.55, "history": 0.20, "fit": 0.15, "cost": 0.10},
    RoutingMode.BALANCED: {"quota": 0.30, "history": 0.30, "fit": 0.30, "cost": 0.10},
    RoutingMode.QUALITY:  {"quota": 0.10, "history": 0.35, "fit": 0.50, "cost": 0.05},
    RoutingMode.MAXIMUM:  {"quota": 0.05, "history": 0.30, "fit": 0.60, "cost": 0.05},
}

# Capability-based fit: how well an agent's declared capabilities match what the
# complexity level demands. Not agent-specific — the core stays provider-neutral.
_COMPLEXITY_CAPS: dict[Complexity, frozenset[Capability]] = {
    Complexity.TRIVIAL: frozenset(),
    Complexity.LOW: frozenset(),
    Complexity.MEDIUM: frozenset({Capability.CODE_EDIT}),
    Complexity.HIGH: frozenset({Capability.DEEP_REASONING, Capability.PLANNING}),
    Complexity.CRITICAL: frozenset({Capability.DEEP_REASONING, Capability.PLANNING,
                                    Capability.REVIEW}),
}

# Minimum runs before historical success rate is trusted over the neutral prior.
MIN_SAMPLES = 5
NEUTRAL_PRIOR = 0.6


#: How long a health probe is trusted. Health is a subprocess or network call,
#: so probing it on every routing decision would cost more than it saves.
HEALTH_TTL_S = 60.0


@dataclass
class RoutingEngine:
    config: Config
    registry: AgentRegistry
    usage: UsageManager
    db: Database
    quota_book: QuotaBook | None = None
    #: agent_id -> (checked_at_monotonic, healthy, detail)
    _health: dict[str, tuple[float, bool, str]] = field(default_factory=dict)

    async def health_of(self, adapter: AgentAdapter) -> tuple[bool, str]:
        """Cached health probe.

        A provider that is down is a hard constraint, not a soft penalty:
        routing to it burns an attempt and a retry for a guaranteed failure.
        A probe that itself raises counts as healthy — refusing to route
        because our own check broke would be worse than trying.
        """
        now = monotonic()
        cached = self._health.get(adapter.id)
        if cached is not None and now - cached[0] < HEALTH_TTL_S:
            return cached[1], cached[2]
        try:
            status = await adapter.health_check()
            healthy, detail = status.healthy, (status.detail or "")
        except Exception as exc:  # noqa: BLE001 - a broken probe must not veto
            healthy, detail = True, f"health probe failed: {exc}"
        self._health[adapter.id] = (now, healthy, detail)
        return healthy, detail

    @staticmethod
    def _context_fits(adapter: AgentAdapter, estimate: UsageEstimate) -> tuple[bool, str]:
        """Reject an agent whose context window cannot hold the input.

        An unknown window is not a rejection: `None` means the adapter did not
        declare one, and inventing a limit would veto agents for no reason.
        """
        limit = adapter.capabilities.max_context_tokens
        if limit is None:
            return True, "context window not declared"
        if estimate.input_tokens > limit:
            return False, (f"needs ~{estimate.input_tokens} input tokens, "
                           f"context window is {limit}")
        return True, f"input fits in {limit}-token context"

    def mode_for(self, task: Task, override: RoutingMode | None = None) -> RoutingMode:
        mode = override or self.config.mode
        if mode is not RoutingMode.AUTO:
            return mode
        # AUTO resolves to a concrete mode from the task itself.
        if task.complexity in (Complexity.TRIVIAL, Complexity.LOW):
            return RoutingMode.ECONOMY
        if task.complexity is Complexity.CRITICAL:
            return RoutingMode.QUALITY
        return RoutingMode.BALANCED

    def _fit(self, adapter: AgentAdapter, task: Task) -> tuple[float, str]:
        caps = adapter.capabilities
        wanted = _COMPLEXITY_CAPS[task.complexity] | task.required_capabilities
        if not wanted:
            return 1.0, "no special capabilities required"
        have = len(wanted & caps.capabilities)
        score = have / len(wanted)
        missing = sorted(c.value for c in wanted - caps.capabilities)
        detail = ("covers all required capabilities" if not missing
                  else f"missing {', '.join(missing)}")
        return score, detail

    def _history(self, adapter: AgentAdapter, task: Task) -> tuple[float, str]:
        rate, samples = self.db.success_rate(adapter.id, task.task_type)
        if samples < MIN_SAMPLES:
            return NEUTRAL_PRIOR, f"only {samples} prior runs, using neutral prior"
        return rate, f"historical success {rate:.0%} over {samples} runs"

    def _cost(self, adapter: AgentAdapter, task: Task) -> tuple[float, str]:
        avg = self.db.avg_tokens(adapter.id, task.task_type)
        if avg is None:
            return 0.5, "no cost history"
        peers = [self.db.avg_tokens(a.id, task.task_type) for a in self.registry]
        known = [p for p in peers if p]
        if not known or max(known) == min(known):
            return 0.5, "no cost differentiation"
        # Cheaper is better, normalised against observed peers.
        score = 1 - (avg - min(known)) / (max(known) - min(known))
        return score, f"avg {avg} tokens for {task.task_type}"

    async def decide(
        self, task: Task, *, mode_override: RoutingMode | None = None
    ) -> RoutingDecision:
        adapters = self.registry.available()
        if not adapters:
            raise NoViableAgent("no agent binary found on PATH; run `router doctor`")

        mode = self.mode_for(task, mode_override)
        weights = WEIGHTS[mode]
        conservation = await self.usage.conservation_mode(adapters)
        candidates: list[Candidate] = []

        # Hard constraints run before any scoring: a candidate that cannot do
        # the job at all must be rejected, not merely ranked low. Every
        # rejection is recorded so the decision can explain itself.
        rejected: list[tuple[str, str]] = []
        for adapter in adapters:
            if not adapter.capabilities.has(*task.required_capabilities):
                missing = sorted(c.value for c in
                                 task.required_capabilities - adapter.capabilities.capabilities)
                rejected.append((adapter.id, f"lacks required capability: {', '.join(missing)}"))
                continue
            estimate = await adapter.estimate(task)
            fits, fits_why = self._context_fits(adapter, estimate)
            if not fits:
                rejected.append((adapter.id, fits_why))
                continue
            healthy, health_why = await self.health_of(adapter)
            if not healthy:
                rejected.append((adapter.id, f"unhealthy: {health_why}"))
                continue
            # Pool-aware quota when a QuotaBook is configured; legacy
            # per-agent accounting otherwise. The two paths share the
            # same 0..1 semantics so the score formula is identical.
            if self.quota_book is not None:
                pressure = self.quota_book.pressure(adapter.id)
                pool_id = self._select_pool_id(adapter.id, task)
            else:
                pressure = await self.usage.pressure(adapter)
                pool_id = None
            fit, fit_why = self._fit(adapter, task)
            history, hist_why = self._history(adapter, task)
            cost, cost_why = self._cost(adapter, task)
            quota = 1 - pressure
            score = (weights["quota"] * quota + weights["history"] * history
                     + weights["fit"] * fit + weights["cost"] * cost)

            reasons = [
                f"fit {fit:.2f}: {fit_why}",
                f"history {history:.2f}: {hist_why}",
                f"quota {quota:.2f}: {pressure:.0%} of window consumed",
                f"cost {cost:.2f}: {cost_why}",
            ]
            if pool_id is not None:
                reasons.append(f"pool {pool_id}")

            forecast = await self.usage.forecast(adapter, estimate)
            if not forecast.safe:
                if task.complexity is not Complexity.CRITICAL:
                    reasons.append(f"vetoed: {forecast.reason}")
                    score = 0.0
                else:
                    reasons.append(f"reserve tapped for critical task: {forecast.reason}")

            candidates.append(Candidate(adapter.id, round(score, 4),
                                        self._model_for(adapter, task, mode), reasons, estimate))

        if not candidates:
            why = "; ".join(f"{aid}: {reason}" for aid, reason in rejected)
            raise NoViableAgent(
                f"every agent was rejected by a hard constraint ({why})" if why
                else "no agent provides the required capabilities"
            )

        if task.forced_agent:
            forced = next((c for c in candidates if c.agent_id == task.forced_agent), None)
            if forced is None:
                raise NoViableAgent(f"agent {task.forced_agent!r} is not available")
            forced.reasons.insert(0, "user override")
            best, rest = forced, [c for c in candidates if c is not forced]
        else:
            ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
            best, rest = ranked[0], ranked[1:]
            if best.score == 0.0:
                raise NoViableAgent("every agent is over its configured quota threshold")

        spread = best.score - (rest[0].score if rest else 0.0)
        confidence = min(0.5 + spread * 2, 0.99)
        reason = f"mode={mode.value}; " + "; ".join(best.reasons[:3])
        assert best.estimate is not None
        return RoutingDecision(
            task_id=task.id,
            selected_agent=best.agent_id,
            selected_model=best.model,
            reason=reason,
            alternatives=rest,
            confidence=round(confidence, 2),
            estimated_usage=best.estimate,
            risk=task.risk,
            mode=mode,
            conservation=conservation,
            quota_pool=self._select_pool_id(best.agent_id, task),
            rejected=rejected,
        )

    def _model_for(self, adapter: AgentAdapter, task: Task, mode: RoutingMode) -> str | None:
        """Pick the cheapest model that still matches the task's complexity.

        This is the main lever for making a premium quota last: without it every
        run costs top-tier rates, including the trivial ones. QUALITY and MAXIMUM
        skip the downgrade and always take the highest configured tier.
        """
        cfg = self.config.agent(adapter.id)
        tiers = cfg.model_tiers
        if not tiers:
            # No tiers configured: defer to the CLI's own default rather than
            # guessing a model id that may not exist for this account.
            return cfg.default_model
        if not adapter.capabilities.has(Capability.MODEL_SELECTION):
            return cfg.default_model
        if mode in (RoutingMode.QUALITY, RoutingMode.MAXIMUM):
            ordered = [c for c in Complexity if c.value in tiers]
            if ordered:
                return tiers[ordered[-1].value]
        return tiers.get(task.complexity.value, cfg.default_model)

    def _select_pool_id(self, agent_id: str, task: Task) -> str | None:
        """Pick the pool id that funded this candidate.

        Returns `None` when no pool book is attached, when the agent
        has only an implicit pool, or when no configured pool can
        fund the task (the score-zero veto still applies in the
        `decide` loop; this just reports the would-be funder).
        """
        if self.quota_book is None:
            return None
        for pool in self.quota_book.pools:
            if not pool.enabled or agent_id not in pool.agent_ids:
                continue
            if pool.tier == "premium" and task.complexity is not Complexity.CRITICAL:
                continue
            return pool.id
        impl = self.quota_book.implicit.get(agent_id)
        return impl.id if impl else None
