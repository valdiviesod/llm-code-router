"""Weighted-scoring routing engine.

No ML. Each candidate agent gets a score in 0..1 built from named, inspectable
factors; the weights per routing mode are the only knobs. Because every factor
is a named contribution, the RoutingDecision can explain itself in the TUI.

A future ML ranker plugs in by implementing score_candidates(); everything else
(capability filtering, quota veto, decision shape) stays.
"""

from __future__ import annotations

from dataclasses import dataclass

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
)
from ..errors import NoViableAgent
from ..storage.db import Database
from ..usage.manager import UsageManager

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


@dataclass(slots=True)
class RoutingEngine:
    config: Config
    registry: AgentRegistry
    usage: UsageManager
    db: Database

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
            raise NoViableAgent("no agent binary found on PATH; run `v4ld1 doctor`")

        mode = self.mode_for(task, mode_override)
        weights = WEIGHTS[mode]
        conservation = await self.usage.conservation_mode(adapters)
        candidates: list[Candidate] = []

        for adapter in adapters:
            if not adapter.capabilities.has(*task.required_capabilities):
                continue
            estimate = await adapter.estimate(task)
            pressure = await self.usage.pressure(adapter)
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
            raise NoViableAgent("no agent provides the required capabilities")

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
        )

    def _model_for(self, adapter: AgentAdapter, task: Task, mode: RoutingMode) -> str | None:
        configured = self.config.agent(adapter.id).default_model
        if configured:
            return configured
        # Without a configured model, defer to the CLI's own default rather than
        # guessing a model id that may not exist for this account.
        return None
