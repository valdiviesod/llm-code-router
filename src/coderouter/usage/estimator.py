"""Calibrated token estimation.

The pre-flight heuristic in `AgentAdapter.estimate` models a single-shot
completion: prompt bytes plus selected context plus a flat 2k, times a
complexity factor. But the adapters drive agentic CLIs that read files we
never selected, run tools, and iterate over many turns. On the run that
motivated this module the heuristic said 2,626 tokens and the run spent
115,924 — a 44x gap. No value of BYTES_PER_TOKEN closes that; the heuristic
models the wrong thing.

This estimator puts observed history first. `agent_stats` has been
accumulating real spend per agent x task type x complexity since v0.1.0, and
that is a far better predictor than any bytes-per-token constant. The
heuristic survives only as the cold-start prior, scaled by the observed
actual/estimated correction factor rather than trusted raw.

Provenance is reported honestly (ADR-005): an estimate built from enough
observed samples is `observed`, a corrected heuristic is `calibrated`, and
the raw heuristic is `heuristic`. `router plan` prints it.
"""

from __future__ import annotations

from ..core.models import Task, UsageEstimate
from ..storage.db import Database

#: Runs required before observed history outranks the corrected heuristic.
#: Same bar as the routing engine's success-rate factor (MIN_SAMPLES), so
#: "enough history" means the same thing everywhere in the router.
MIN_SAMPLES = 5


class TokenEstimator:
    """Estimates task cost from recorded runs, falling back to a corrected
    heuristic. One instance per Database; stateless otherwise."""

    def __init__(self, db: Database, min_samples: int = MIN_SAMPLES):
        self.db = db
        self.min_samples = min_samples

    async def estimate(self, adapter, task: Task) -> UsageEstimate:
        """The estimate every quota decision should rest on.

        Order of preference:
        1. Observed history for this agent x task type (complexity bucket
           first, then the task type as a whole when the bucket is thin).
        2. The adapter heuristic multiplied by the observed actual/estimated
           correction factor for this agent.
        3. The raw heuristic (a brand-new agent with no history at all).
        """
        heuristic = await adapter.estimate(task)

        avg, n = self.db.avg_tokens_detail(
            adapter.id, task.task_type, task.complexity.value
        )
        if avg is None or n < self.min_samples:
            # The complexity bucket is thin; the whole task type may still
            # have enough runs to be a better predictor than the heuristic.
            avg, n = self.db.avg_tokens_detail(adapter.id, task.task_type)
        if avg is not None and n >= self.min_samples:
            return self._from_observed(adapter.id, task, avg)

        correction = self.db.estimate_correction(adapter.id)
        if correction is not None and correction > 1.0:
            return UsageEstimate(
                input_tokens=int(heuristic.input_tokens * correction),
                output_tokens=int(heuristic.output_tokens * correction),
                confidence=0.6,
                source="calibrated",
            )
        return heuristic

    def _from_observed(self, agent_id: str, task: Task, avg_total: int) -> UsageEstimate:
        """Split an observed average into input/output halves.

        The split uses the agent's own recorded input:output ratio so the
        context-window fit check in the routing engine sees a realistic
        input figure, not an arbitrary 50/50.
        """
        avg_in, avg_out = self.db.avg_token_split(agent_id, task.task_type)
        if avg_in + avg_out <= 0:
            avg_in, avg_out = avg_total / 2, avg_total / 2
        share = avg_in / (avg_in + avg_out)
        input_tokens = int(avg_total * share)
        return UsageEstimate(
            input_tokens=input_tokens,
            output_tokens=avg_total - input_tokens,
            confidence=0.9,
            source="observed",
        )
