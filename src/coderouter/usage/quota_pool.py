"""Quota pools: multi-subscription aggregation and selection.

A QuotaPool is a named budget that may span one or more agent ids.
The user might have a Claude Pro subscription on the `claude` adapter
*and* a pay-as-you-go Claude API key on a different `claude-api`
adapter; both could feed a single "claude" pool. A separate pool
might cover `antigravity` and `codex` under a different billing
relationship.

The book keeps one explicit `QuotaPool` per configured `quota_pools:`
entry plus one implicit pool per registered agent that has no
configured pool. The implicit pool is single-agent, so the
unconfigured case is byte-for-byte the existing per-agent
accounting: the router asks `book.pressure(agent_id)` and gets
the same number as `UsageManager.pressure(adapter)`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from ..core.models import Complexity, UsageInfo, UsageStatus, UsageWindow
from ..storage.db import Database
from .manager import UsageManager
from .reservation import QuotaLedger, Reservation, SqliteQuotaLedger

PoolKind = Literal["subscription", "api_key"]
PoolTier = Literal["premium", "standard", "economy"]


@dataclass(slots=True)
class QuotaPool:
    """A budget that may span multiple agents.

    `agent_ids` is the list of adapter ids whose usage is summed
    against this pool's limit. `limit_tokens=None` means "unknown",
    matching the per-agent convention.
    """

    id: str
    kind: PoolKind
    tier: PoolTier
    agent_ids: tuple[str, ...]
    window_hours: float = 5.0
    limit_tokens: int | None = None
    weekly_limit_tokens: int | None = None
    reserve_percent: float = 15.0
    enabled: bool = True
    window_used: int = 0  # populated by the book; not user-configured
    weekly_used: int = 0
    status: UsageStatus = UsageStatus.UNKNOWN


@dataclass(slots=True)
class QuotaBook:
    """Registry + query API for QuotaPools.

    Aggregation is done in SQL (Invariant: never sum rows in Python).
    """

    pools: list[QuotaPool] = field(default_factory=list)
    implicit: dict[str, QuotaPool] = field(default_factory=dict)
    db: Database | None = None
    #: Outstanding reservations. Built by `build`; None means reservations
    #: are off and `reserve()` always declines, which is the safe default
    #: for a book assembled by hand in a test.
    ledger: QuotaLedger | None = None

    # --- construction --------------------------------------------------

    @classmethod
    def build(
        cls,
        configured: list[QuotaPool],
        agent_ids: list[str],
        db: Database,
    ) -> QuotaBook:
        """Compose the configured pools with implicit single-agent pools.

        Every agent that does not appear in any configured pool gets
        one implicit pool. Disabling a pool that still appears in the
        list leaves its agent un-pooled, so the agent falls through to
        its own implicit pool.
        """
        covered: set[str] = set()
        for pool in configured:
            if pool.enabled:
                covered.update(pool.agent_ids)
        implicit: dict[str, QuotaPool] = {}
        for aid in agent_ids:
            if aid in covered:
                continue
            implicit[aid] = QuotaPool(
                id=f"_implicit:{aid}",
                kind="subscription",
                tier="standard",
                agent_ids=(aid,),
                window_hours=5.0,
                limit_tokens=None,
                weekly_limit_tokens=None,
                reserve_percent=15.0,
            )
        return cls(pools=list(configured), implicit=implicit, db=db,
                   ledger=SqliteQuotaLedger(db))

    def pools_for(self, agent_id: str) -> list[QuotaPool]:
        """Every pool that covers this agent, configured first, then implicit.

        The implicit pool is always present (one per agent), so the
        caller can rely on the list being non-empty.
        """
        explicit = [p for p in self.pools if p.enabled and agent_id in p.agent_ids]
        impl = self.implicit.get(agent_id)
        return explicit + ([impl] if impl else [])

    # --- SQL aggregation -----------------------------------------------

    def _refresh_window(self, pool: QuotaPool) -> None:
        """Compute window_used / weekly_used for one pool from SQL.

        The result is stored on the pool object so the rest of the
        API can stay synchronous. Aggregations are still SQL: one
        `SUM` per window, never a Python loop over rows.
        """
        if self.db is None:
            return
        since_window = (datetime.now(UTC) - timedelta(hours=pool.window_hours)).isoformat()
        since_week = (datetime.now(UTC) - timedelta(hours=24 * 7)).isoformat()
        placeholders = ",".join("?" for _ in pool.agent_ids)
        params: list = list(pool.agent_ids)
        row = self.db.conn.execute(
            f"SELECT COALESCE(SUM(tokens),0) AS t FROM usage_events "
            f"WHERE agent_id IN ({placeholders}) AND occurred_at >= ?",
            (*params, since_window),
        ).fetchone()
        pool.window_used = int(row["t"])
        row2 = self.db.conn.execute(
            f"SELECT COALESCE(SUM(tokens),0) AS t FROM usage_events "
            f"WHERE agent_id IN ({placeholders}) AND occurred_at >= ?",
            (*params, since_week),
        ).fetchone()
        pool.weekly_used = int(row2["t"])
        pool.status = (
            UsageStatus.ESTIMATED
            if (pool.limit_tokens or pool.weekly_limit_tokens)
            else UsageStatus.UNKNOWN
        )

    # --- read API ------------------------------------------------------

    def pressure(self, agent_id: str) -> float:
        """0.0..1.0 — the most-pressured pool covering this agent.

        The router uses this as the `quota` factor. Pools with no
        configured limit return 0.0 so a missing limit never silently
        penalises an agent.
        """
        pools = self.pools_for(agent_id)
        if not pools:
            return 0.0
        pressures = [self._window_fraction(p) for p in pools]
        return max(pressures) if pressures else 0.0

    def _window_fraction(self, pool: QuotaPool) -> float:
        if pool.limit_tokens is None or pool.limit_tokens == 0:
            return 0.0
        self._refresh_window(pool)
        # In-flight reservations are pressure too: work that is already
        # committed to but not yet on the books still cannot be spent twice.
        reserved = self.ledger._outstanding_locked(pool.id) if self.ledger else 0
        return min((pool.window_used + reserved) / pool.limit_tokens, 1.0)

    def can_fund(
        self,
        agent_id: str,
        estimate_tokens: int,
        *,
        complexity: Complexity,
        user_override: bool,
    ) -> tuple[bool, str]:
        """True if the agent has at least one pool that can fund `estimate_tokens`.

        A premium-tier pool is not consumed by anything but CRITICAL
        tasks or an explicit user override, exactly the same rule the
        per-agent reserve already follows. That symmetry is what
        makes the no-config case a byte-for-byte no-op: a single
        implicit pool is "standard" tier, so it is never protected.

        This is advisory: between the answer and the spend, a parallel task
        can take the same headroom. Use `reserve()` when the answer will be
        acted on.
        """
        pool, reason = self._fundable_pool(
            agent_id, estimate_tokens, complexity=complexity, user_override=user_override,
        )
        return pool is not None, reason

    def _fundable_pool(
        self,
        agent_id: str,
        estimate_tokens: int,
        *,
        complexity: Complexity,
        user_override: bool,
    ) -> tuple[QuotaPool | None, str]:
        """The first pool that can fund the estimate, or None with the reason.

        Outstanding reservations count against the window exactly as spent
        tokens do — that is what stops two parallel tasks from both being
        told the same headroom is theirs.
        """
        for pool in self.pools_for(agent_id):
            if not pool.enabled:
                continue
            is_premium = pool.tier == "premium"
            if is_premium and complexity is not Complexity.CRITICAL and not user_override:
                continue
            if pool.limit_tokens is None:
                return pool, "no configured limit; usage unknown"
            self._refresh_window(pool)
            reserved = self.ledger._outstanding_locked(pool.id) if self.ledger else 0
            threshold = pool.reserve_percent / 100
            usable = min(0.85, 1 - threshold)
            projected = pool.window_used + reserved + estimate_tokens
            if projected / pool.limit_tokens <= usable:
                return pool, "within usable share"
        return None, "every pool above reserve"

    # --- reservations ---------------------------------------------------

    def reserve(
        self,
        agent_id: str,
        estimate_tokens: int,
        *,
        complexity: Complexity,
        user_override: bool,
        task_id: str | None = None,
    ) -> Reservation | None:
        """Atomically check affordability and claim the tokens.

        Returns None when no pool can fund the estimate. The check and the
        hold happen under one lock — one `BEGIN IMMEDIATE` transaction with
        the SQLite ledger — so two concurrent callers, in this process or in
        another one sharing the database, cannot both be granted the last of
        a pool's headroom.

        A pool with no configured limit is still reserved against, so the
        outstanding total is visible; it just never blocks, because an
        unknown limit yields no fraction to compare against (Invariant 2).
        """
        if self.ledger is None:
            return None

        def _fundable_pool_only():
            pool, _reason = self._fundable_pool(
                agent_id, estimate_tokens,
                complexity=complexity, user_override=user_override,
            )
            return pool

        return self.ledger.reserve_atomic(
            _fundable_pool_only, agent_id, estimate_tokens, task_id,
        )

    def commit(self, res: Reservation, actual_tokens: int) -> None:
        """Settle a reservation with what the run actually spent."""
        if self.ledger is not None:
            self.ledger.commit(res, actual_tokens)

    def release(self, res: Reservation) -> None:
        """Drop a reservation whose run never happened."""
        if self.ledger is not None:
            self.ledger.release(res)

    def usage_info(self, pool: QuotaPool) -> UsageInfo:
        self._refresh_window(pool)
        windows = [
            UsageWindow(
                label=f"{pool.window_hours:g}h",
                used_tokens=pool.window_used,
                limit_tokens=pool.limit_tokens,
                window_start=datetime.now(UTC) - timedelta(hours=pool.window_hours),
                window_end=datetime.now(UTC) + timedelta(hours=pool.window_hours),
                status=pool.status,
            ),
            UsageWindow(
                label="weekly",
                used_tokens=pool.weekly_used,
                limit_tokens=pool.weekly_limit_tokens,
                window_start=datetime.now(UTC) - timedelta(hours=24 * 7),
                window_end=datetime.now(UTC) + timedelta(hours=24 * 7),
                status=pool.status,
            ),
        ]
        return UsageInfo(pool.id, windows, pool.status)

    # --- backward compatibility shim -----------------------------------

    def legacy_pressure(self, agent_id: str, usage: UsageManager) -> float:
        """Pressure for one agent, computed exactly as `UsageManager.pressure`.

        Provided so call sites that did not migrate to QuotaBook still
        get the same number. The book is the new source of truth.
        """
        return usage.pressure_for(agent_id)
