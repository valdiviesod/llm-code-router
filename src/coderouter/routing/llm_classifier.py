"""Model-backed task classification, paid for out of the same subscriptions.

The heuristic classifier in `classifier.py` is English regex. It cannot read a
Spanish prompt, it cannot read intent, and its failure mode is silent: a prompt
about production that never says "production" comes back as low risk, and risk
is what drives conservation mode and model tier. That is the gap this closes.

Three things constrain the design, and all three are measured, not assumed:

1. **A classification call is not cheap.** Verified against Claude Code 2.1.239
   (~16k billed input tokens for a minimal call, ~31k without the lean flags)
   and agy 1.1.17 (~23k). Both bill their whole tool preamble before reading the
   prompt. So calling a model on every prompt would cost more than most of the
   tasks being classified. Hence `auto`: ask a model only when the heuristic
   admits it is unsure, and cache the answer.
2. **It must never invent.** Every field is validated against the domain enums.
   Anything unparseable, unknown or out of range falls back to the heuristic
   result rather than being coerced into something plausible.
3. **It must pay for itself honestly.** The tokens the classifier spends are
   recorded as usage events for the agent that spent them, so the quota figures
   the router reads already include what the router itself burned.

Which agent answers is decided by quota pressure, never by agent id: whoever has
the most headroom, using whatever model that agent's config maps to its cheapest
tier. Adding a third agent changes nothing here.
"""

from __future__ import annotations

import hashlib
import json
import logging

from ..agents.base.adapter import AgentAdapter
from ..agents.base.registry import AgentRegistry
from ..config import Config
from ..core.models import TASK_TYPES, Capability, Complexity, Risk, UsageStatus
from ..logging import get_logger, log
from ..storage.db import Database
from ..usage.manager import UsageManager
from .classifier import Classification, capabilities_for, classify

logger = get_logger("classifier")

SYSTEM_PROMPT = (
    "You classify software engineering requests for a task router. "
    "The request may be in any language. Answer with a single JSON object and "
    "nothing else: no prose, no markdown fence, no explanation."
)

# Ordered highest-signal-first so the instruction reads the way the enum ranks.
_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "task_type": {"type": "string", "enum": list(TASK_TYPES)},
        "complexity": {"type": "string", "enum": [c.value for c in Complexity]},
        "risk": {"type": "string", "enum": [r.value for r in Risk]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["task_type", "complexity", "risk", "confidence", "reason"],
    "additionalProperties": False,
}

_INSTRUCTIONS = f"""\
Classify the request below.

task_type: one of {", ".join(TASK_TYPES)}
complexity: trivial (a typo or one-liner), low (one small change), medium (one
  feature in a few files), high (cross-cutting, architectural, or a migration),
  critical (high complexity AND irreversible or production-facing)
risk: high if it touches production, payments, authentication, secrets, data
  deletion or schema migration; medium if it touches user-facing behaviour or
  security-adjacent code; otherwise low
confidence: 0..1, how sure you are
reason: one short sentence, in English

Judge intent, not vocabulary: the request may be in any language and may
describe a risky change without naming it.

Return only the JSON object.

REQUEST:
{{prompt}}
"""


def _fingerprint(prompt: str) -> str:
    return hashlib.sha256(prompt.strip().lower().encode()).hexdigest()[:32]


def _strip_fence(text: str) -> str:
    """Models fence JSON even when told not to; this is cheaper than a retry."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1] if "\n" in body else body
        body = body.rsplit("```", 1)[0]
    start, end = body.find("{"), body.rfind("}")
    return body[start : end + 1] if start != -1 and end > start else body


def _parse(payload: dict, prompt: str) -> Classification | None:
    """Validate against the domain enums. Unknown values are rejected, not guessed."""
    try:
        task_type = str(payload["task_type"]).strip().lower()
        complexity = Complexity(str(payload["complexity"]).strip().lower())
        risk = Risk(str(payload["risk"]).strip().lower())
        confidence = float(payload.get("confidence", 0.7))
    except (KeyError, TypeError, ValueError):
        return None
    if task_type not in TASK_TYPES:
        return None
    reason = str(payload.get("reason", ""))[:200]
    return Classification(
        task_type=task_type,
        complexity=complexity,
        risk=risk,
        required_capabilities=capabilities_for(task_type),
        reasons=[f"model: {reason}" if reason else "model classification"],
        confidence=round(min(1.0, max(0.0, confidence)), 2),
        source="llm",
    )


class LLMClassifier:
    """Wraps the heuristic classifier; escalates to a model when it pays off."""

    def __init__(
        self, config: Config, registry: AgentRegistry, usage: UsageManager, db: Database
    ):
        self.config = config
        self.registry = registry
        self.usage = usage
        self.db = db

    @property
    def _routing(self):
        return self.config.routing

    async def _pick_agent(self) -> AgentAdapter | None:
        """The agent with the most quota headroom that can answer at all.

        Selection is by declared capability and measured pressure. There is no
        agent id in this method on purpose: that is invariant #1.
        """
        candidates = [
            a for a in self.registry.available()
            if a.capabilities.has(Capability.STRUCTURED_COMPLETION)
        ]
        if not candidates:
            return None
        pressures = [(await self.usage.pressure(a), i, a) for i, a in enumerate(candidates)]
        return min(pressures)[2]

    def _model_for(self, agent_id: str) -> str | None:
        """The cheapest tier this agent's config declares, if it declares one.

        Model names are provider-specific, so they live in configuration. A
        missing tier means "let the CLI pick", never a hardcoded name.
        """
        cfg = self.config.agent(agent_id)
        tier = self._routing.classifier_tier
        return cfg.model_tiers.get(tier) or cfg.default_model

    async def classify(self, prompt: str) -> Classification:
        heuristic = classify(prompt)
        mode = self._routing.classifier
        if mode == "heuristic":
            return heuristic
        if mode == "auto" and heuristic.confidence >= self._routing.classifier_threshold:
            heuristic.reasons.append(
                f"heuristic confident ({heuristic.confidence:.0%}); no model call"
            )
            return heuristic

        fingerprint = _fingerprint(prompt)
        cached = self.db.cached_classification(
            fingerprint, max_age_hours=self._routing.classifier_cache_hours
        )
        if cached:
            parsed = _parse(cached, prompt)
            if parsed:
                parsed.reasons.append("cached model classification")
                return parsed

        result = await self._ask(prompt)
        if result is None:
            heuristic.reasons.append("model classification unavailable; kept heuristic")
            return heuristic
        payload, parsed = result
        self.db.cache_classification(fingerprint, payload)
        log(logger, logging.INFO, "model classification",
            task_type=parsed.task_type, complexity=parsed.complexity.value,
            risk=parsed.risk.value, confidence=parsed.confidence)
        return parsed

    async def _ask(self, prompt: str) -> tuple[dict, Classification] | None:
        adapter = await self._pick_agent()
        if adapter is None:
            return None
        # The prompt is truncated, not summarised: the tail of a long prompt is
        # rarely what sets its risk, and every character here is billed.
        excerpt = prompt.strip()[: self._routing.classifier_max_chars]
        completion = await adapter.complete(
            _INSTRUCTIONS.format(prompt=excerpt),
            system=SYSTEM_PROMPT,
            schema=_SCHEMA,
            model=self._model_for(adapter.id),
            timeout=self._routing.classifier_timeout_s,
        )
        if completion is None:
            log(logger, logging.WARNING, "classifier call failed", agent_id=adapter.id)
            return None

        # Charged before parsing: the tokens were spent whether or not the
        # answer turns out to be usable.
        if completion.total_tokens:
            self.db.record_usage(
                completion.agent_id, completion.total_tokens, UsageStatus.CONFIRMED.value,
                kind="classification",
            )

        payload = completion.structured
        if payload is None:
            try:
                candidate = json.loads(_strip_fence(completion.text))
            except json.JSONDecodeError:
                log(logger, logging.WARNING, "classifier returned non-JSON",
                    agent_id=adapter.id)
                return None
            payload = candidate if isinstance(candidate, dict) else None
        if payload is None:
            return None
        parsed = _parse(payload, prompt)
        if parsed is None:
            log(logger, logging.WARNING, "classifier returned invalid fields",
                agent_id=adapter.id)
            return None
        return payload, parsed
