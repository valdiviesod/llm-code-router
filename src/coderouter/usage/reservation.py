"""Quota reservations: stop parallel agents from spending the same tokens twice.

Committed usage is derived from SQL (`SUM(tokens)` over `usage_events`), which
is only written *after* a run finishes. That leaves a window: between the moment
the router decides an agent can afford a task and the moment the run is
recorded, the tokens are spoken for but invisible. With one task in flight that
window is harmless. With `concurrency.globally > 1` it is a real overspend —
two tasks each check 30k remaining, each estimates 20k, both proceed, and the
pool ends 10k over.

A reservation closes the window by making the intent visible:

    reserve() -> execute -> commit(actual)   # or release() if it never ran

`available = limit - committed(SQL) - outstanding_reservations`.

ponytail: reservations live in this process only. That is the right ceiling for
CodeRouter today, where one `router` process owns its own scheduler and
concurrency limit. Two `router` processes sharing one subscription would still
race; the upgrade path is a `reservations` table with the same API, and the
callers would not change.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..core.models import new_id
from ..logging import get_logger

logger = get_logger("usage.reservation")


@dataclass(slots=True)
class Reservation:
    """A claim on `tokens` from `pool_id`, held until committed or released."""

    id: str
    pool_id: str
    agent_id: str
    tokens: int
    task_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    settled: bool = False


class QuotaLedger:
    """Outstanding reservations, keyed by pool.

    Every mutation is guarded by one lock. The critical section is a few
    dict operations — no I/O, no awaits — so a global lock is cheap and
    removes any question of interleaving.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, Reservation] = {}
        self._outstanding: dict[str, int] = {}

    def outstanding(self, pool_id: str) -> int:
        """Tokens reserved but not yet settled against `pool_id`."""
        with self._lock:
            return self._outstanding.get(pool_id, 0)

    def hold(
        self,
        pool_id: str,
        agent_id: str,
        tokens: int,
        *,
        task_id: str | None = None,
    ) -> Reservation:
        """Record a reservation. Affordability is the caller's decision.

        `QuotaBook.reserve` is the affordability gate; keeping the check out
        of the ledger is what lets the book do check-and-hold under a single
        lock instead of two.
        """
        res = Reservation(id=new_id("resv"), pool_id=pool_id, agent_id=agent_id,
                          tokens=max(0, tokens), task_id=task_id)
        with self._lock:
            self._by_id[res.id] = res
            self._outstanding[pool_id] = self._outstanding.get(pool_id, 0) + res.tokens
        return res

    def commit(self, res: Reservation, actual_tokens: int) -> None:
        """Settle a reservation against what the run actually spent.

        The actual spend is recorded by the orchestrator as a usage event, so
        committing only has to drop the hold. An actual above the estimate is
        logged: a pattern of under-estimating is how a reserve gets breached
        even with reservations in place.
        """
        if actual_tokens > res.tokens:
            logger.warning(
                "reservation %s under-estimated: reserved %d, spent %d (pool %s)",
                res.id, res.tokens, actual_tokens, res.pool_id,
            )
        self.release(res)

    def release(self, res: Reservation) -> None:
        """Drop a hold. Idempotent — a double release must not free twice."""
        with self._lock:
            if res.settled or res.id not in self._by_id:
                return
            res.settled = True
            self._by_id.pop(res.id, None)
            remaining = self._outstanding.get(res.pool_id, 0) - res.tokens
            if remaining > 0:
                self._outstanding[res.pool_id] = remaining
            else:
                self._outstanding.pop(res.pool_id, None)

    def release_all(self) -> None:
        """Drop every hold. For shutdown and for tests."""
        with self._lock:
            for res in self._by_id.values():
                res.settled = True
            self._by_id.clear()
            self._outstanding.clear()

    @property
    def lock(self) -> threading.Lock:
        """Exposed so `QuotaBook` can make check-and-hold one atomic step."""
        return self._lock

    def _hold_locked(
        self, pool_id: str, agent_id: str, tokens: int, task_id: str | None
    ) -> Reservation:
        """`hold` for a caller that already owns `self.lock`."""
        res = Reservation(id=new_id("resv"), pool_id=pool_id, agent_id=agent_id,
                          tokens=max(0, tokens), task_id=task_id)
        self._by_id[res.id] = res
        self._outstanding[pool_id] = self._outstanding.get(pool_id, 0) + res.tokens
        return res

    def _outstanding_locked(self, pool_id: str) -> int:
        return self._outstanding.get(pool_id, 0)
