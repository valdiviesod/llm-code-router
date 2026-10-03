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

Reservations live in the `reservations` SQLite table, so two `router`
processes sharing one subscription (two terminals, or a TUI plus a CLI run)
see each other's holds. The affordability check and the insert happen inside
one `BEGIN IMMEDIATE` transaction, which SQLite serialises across processes —
the cross-process race is closed the same way the in-process one was. A
killed process cannot release its own holds, so a hold older than the stale
window is reaped: dead by definition.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ..core.models import new_id
from ..logging import get_logger

logger = get_logger("usage.reservation")

#: A hold older than this is dead by definition: no configured run window is
#: longer, so the process that took it either finished (and committed) or was
#: killed. Reaped automatically on every reserve and outstanding read.
STALE_AFTER_S = 6 * 3600


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
    """Outstanding reservations, keyed by pool (in-process).

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

    def reserve_atomic(self, fundable, agent_id: str, tokens: int,
                       task_id: str | None = None) -> Reservation | None:
        """Check affordability and hold under one lock.

        `fundable` is a zero-argument callable returning the pool to charge
        (or None when nothing can fund the request). Keeping it a callback is
        what makes check-and-hold atomic without the ledger knowing anything
        about pools.
        """
        with self._lock:
            pool = fundable()
            if pool is None:
                return None
            return self._hold_locked(pool.id, agent_id, tokens, task_id)

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


class SqliteQuotaLedger(QuotaLedger):
    """Reservations persisted to the `reservations` table.

    Same API as the in-memory ledger; the callers do not change. Two router
    processes sharing one database now see each other's outstanding holds,
    and the affordability check plus the insert run inside one
    `BEGIN IMMEDIATE` transaction, which SQLite serialises across processes.
    A per-process thread lock still guards this connection's use of the
    shared `Database` object.
    """

    def __init__(self, db, stale_after_s: float = STALE_AFTER_S) -> None:
        super().__init__()
        self.db = db
        self.stale_after_s = stale_after_s

    # --- reads ----------------------------------------------------------

    def outstanding(self, pool_id: str) -> int:
        with self._lock:
            return self._outstanding_locked(pool_id)

    def _outstanding_locked(self, pool_id: str) -> int:
        """SUM without taking the lock: the caller owns it (or the open
        `BEGIN IMMEDIATE` transaction already serialises writers)."""
        self._reap_stale_now()
        row = self.db.conn.execute(
            "SELECT COALESCE(SUM(tokens),0) AS t FROM reservations "
            "WHERE pool_id=? AND settled=0", (pool_id,),
        ).fetchone()
        return int(row["t"])

    # --- writes ---------------------------------------------------------

    def hold(
        self,
        pool_id: str,
        agent_id: str,
        tokens: int,
        *,
        task_id: str | None = None,
    ) -> Reservation:
        res = Reservation(id=new_id("resv"), pool_id=pool_id, agent_id=agent_id,
                          tokens=max(0, tokens), task_id=task_id)
        with self._lock:
            self.db.conn.execute(
                "INSERT INTO reservations (id, pool_id, agent_id, tokens, task_id, "
                "created_at, settled) VALUES (?,?,?,?,?,?,0)",
                (res.id, pool_id, agent_id, res.tokens, task_id,
                 res.created_at.isoformat()),
            )
            self.db.conn.commit()
        return res

    def _hold_locked(
        self, pool_id: str, agent_id: str, tokens: int, task_id: str | None
    ) -> Reservation:
        """Insert inside a caller-owned transaction (no commit here)."""
        res = Reservation(id=new_id("resv"), pool_id=pool_id, agent_id=agent_id,
                          tokens=max(0, tokens), task_id=task_id)
        self.db.conn.execute(
            "INSERT INTO reservations (id, pool_id, agent_id, tokens, task_id, "
            "created_at, settled) VALUES (?,?,?,?,?,?,0)",
            (res.id, pool_id, agent_id, res.tokens, task_id,
             res.created_at.isoformat()),
        )
        return res

    def release(self, res: Reservation) -> None:
        with self._lock:
            cur = self.db.conn.execute(
                "UPDATE reservations SET settled=1 WHERE id=? AND settled=0",
                (res.id,),
            )
            self.db.conn.commit()
        if cur.rowcount > 0:
            res.settled = True

    def release_all(self) -> None:
        """Settle every unsettled row. For tests: a live process must not
        call this, it would drop other processes' holds."""
        with self._lock:
            self.db.conn.execute("UPDATE reservations SET settled=1 WHERE settled=0")
            self.db.conn.commit()

    # --- atomicity ------------------------------------------------------

    def reserve_atomic(self, fundable, agent_id: str, tokens: int,
                       task_id: str | None = None) -> Reservation | None:
        """Check affordability and hold in one cross-process transaction.

        `BEGIN IMMEDIATE` takes SQLite's write lock up front, so a second
        router process running the same code blocks until this transaction
        commits — it cannot read a stale outstanding total and grant the
        same headroom twice.
        """
        conn = self.db.conn
        with self._lock:
            in_txn = conn.in_transaction
            if not in_txn:
                conn.execute("BEGIN IMMEDIATE")
            try:
                self._reap_stale_now()
                pool = fundable()
                if pool is None:
                    if not in_txn:
                        conn.execute("ROLLBACK")
                    return None
                res = self._hold_locked(pool.id, agent_id, tokens, task_id)
                if not in_txn:
                    conn.execute("COMMIT")
                return res
            except BaseException:
                if not in_txn and conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    # --- reaping --------------------------------------------------------

    def reap_stale(self) -> int:
        """Settle holds older than the stale window. Returns how many."""
        with self._lock:
            return self._reap_stale_now()

    def _reap_stale_now(self) -> int:
        cutoff = (datetime.now(UTC) - timedelta(seconds=self.stale_after_s)).isoformat()
        cur = self.db.conn.execute(
            "UPDATE reservations SET settled=1 WHERE settled=0 AND created_at < ?",
            (cutoff,),
        )
        if not self.db.conn.in_transaction:
            self.db.conn.commit()
        return cur.rowcount
