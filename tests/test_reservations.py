"""Quota reservation semantics.

The scenario these exist for: a 100k pool, agent A reserves 70k, agent B asks
for 50k. B must be refused — 30k is what is left, not 50k. Before reservations
existed both were told yes, because committed usage only lands in SQL after a
run finishes.
"""

from __future__ import annotations

import asyncio

import pytest

from coderouter.core.models import Complexity, Task
from coderouter.usage.quota_pool import QuotaBook, QuotaPool


def _book(db, *, limit=100_000, reserve_percent=0.0) -> QuotaBook:
    pool = QuotaPool(
        id="shared", kind="subscription", tier="standard",
        agent_ids=("fake", "other"), limit_tokens=limit,
        reserve_percent=reserve_percent,
    )
    return QuotaBook.build([pool], ["fake", "other"], db)


def _reserve(book, agent, tokens):
    return book.reserve(agent, tokens, complexity=Complexity.LOW, user_override=False)


def test_second_reservation_cannot_claim_the_first_ones_headroom(db):
    book = _book(db)
    first = _reserve(book, "fake", 70_000)
    assert first is not None
    assert _reserve(book, "other", 50_000) is None, "30k left, not 50k"
    # What actually fits still gets through. The usable share is capped at
    # 85% of the limit even with reserve_percent=0, so 70k + 10k clears it.
    assert _reserve(book, "other", 10_000) is not None


def test_release_returns_the_headroom(db):
    book = _book(db)
    held = _reserve(book, "fake", 70_000)
    assert held is not None
    assert _reserve(book, "other", 50_000) is None
    book.release(held)
    assert _reserve(book, "other", 50_000) is not None


def test_release_is_idempotent(db):
    book = _book(db)
    held = _reserve(book, "fake", 70_000)
    assert held is not None
    book.release(held)
    book.release(held)  # a double release must not free the tokens twice
    assert book.ledger is not None
    assert book.ledger.outstanding("shared") == 0


def test_commit_settles_the_hold(db):
    book = _book(db)
    held = _reserve(book, "fake", 70_000)
    assert held is not None
    book.commit(held, actual_tokens=42_000)
    assert book.ledger is not None
    assert book.ledger.outstanding("shared") == 0


def test_commit_over_the_estimate_is_logged_not_swallowed(db, caplog):
    book = _book(db)
    held = _reserve(book, "fake", 10_000)
    assert held is not None
    with caplog.at_level("WARNING"):
        book.commit(held, actual_tokens=25_000)
    assert "under-estimated" in caplog.text


def test_reservations_count_towards_pressure(db):
    book = _book(db)
    assert book.pressure("fake") == 0.0
    _reserve(book, "fake", 50_000)
    assert book.pressure("fake") == pytest.approx(0.5)


def test_unlimited_pool_never_blocks(db):
    pool = QuotaPool(id="unknown", kind="subscription", tier="standard",
                     agent_ids=("fake",), limit_tokens=None)
    book = QuotaBook.build([pool], ["fake"], db)
    for _ in range(5):
        assert _reserve(book, "fake", 10_000_000) is not None


def test_concurrent_reservations_never_exceed_the_limit(db):
    """The check and the hold are one atomic step, so racing callers cannot
    both be granted the last slice."""
    book = _book(db)
    granted: list[object] = []
    barrier = __import__("threading").Barrier(8)

    def worker() -> None:
        barrier.wait()
        res = _reserve(book, "fake", 20_000)
        if res is not None:
            granted.append(res)

    threading = __import__("threading")
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert book.ledger is not None
    # 85k usable / 20k each: at most four may win, and the ledger must agree.
    assert len(granted) <= 4
    assert book.ledger.outstanding("shared") == len(granted) * 20_000


async def test_orchestrator_releases_the_hold_when_a_run_is_cancelled(
    orchestrator, adapters, tmp_path
):
    """A cancelled task must not leave its tokens claimed forever."""
    ledger = orchestrator.quota_book.ledger
    assert ledger is not None

    started = asyncio.Event()

    async def _hang(task, *, model=None, on_event=None):
        started.set()
        await asyncio.sleep(3600)

    adapters[0].execute = _hang  # type: ignore[method-assign]
    adapters[1].execute = _hang  # type: ignore[method-assign]

    run = asyncio.create_task(
        orchestrator.run_task(Task(prompt="hangs forever", project_root=tmp_path))
    )
    await started.wait()
    pools = orchestrator.quota_book.pools + list(orchestrator.quota_book.implicit.values())
    assert any(ledger.outstanding(p.id) > 0 for p in pools), "a hold must be held mid-run"
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert all(ledger.outstanding(p.id) == 0 for p in pools)


# --- persistent (cross-process) reservations -------------------------------


def _book_on(db, *, limit=100_000) -> QuotaBook:
    pool = QuotaPool(
        id="shared", kind="subscription", tier="standard",
        agent_ids=("fake", "other"), limit_tokens=limit, reserve_percent=0.0,
    )
    return QuotaBook.build([pool], ["fake", "other"], db)


def test_hold_is_visible_to_a_second_process(config, db):
    """Two Database connections to one file model two router processes.

    The whole point of the reservations table: process B must see process
    A's in-flight hold and be refused the same headroom."""
    from coderouter.storage.db import Database

    db_b = Database(config.db_path)
    try:
        book_a = _book_on(db)
        book_b = _book_on(db_b)
        first = _reserve(book_a, "fake", 70_000)
        assert first is not None
        assert _reserve(book_b, "other", 50_000) is None, "B must see A's hold"
        assert _reserve(book_b, "other", 10_000) is not None
    finally:
        db_b.close()


def test_commit_by_one_process_frees_headroom_for_the_other(config, db):
    from coderouter.storage.db import Database

    db_b = Database(config.db_path)
    try:
        book_a = _book_on(db)
        book_b = _book_on(db_b)
        held = _reserve(book_a, "fake", 70_000)
        assert held is not None
        assert _reserve(book_b, "other", 50_000) is None
        book_a.commit(held, actual_tokens=42_000)
        assert _reserve(book_b, "other", 50_000) is not None
    finally:
        db_b.close()


def test_release_is_idempotent_across_processes(config, db):
    from coderouter.storage.db import Database

    db_b = Database(config.db_path)
    try:
        book_a = _book_on(db)
        book_b = _book_on(db_b)
        held = _reserve(book_a, "fake", 70_000)
        assert held is not None
        book_b.release(held)  # B settles A's hold (the reaper's job, by hand)
        book_a.release(held)  # a double release must not free twice
        assert _reserve(book_b, "other", 50_000) is not None
        assert _reserve(book_b, "other", 40_000) is None
    finally:
        db_b.close()


def test_stale_holds_are_reaped(db):
    """A killed process cannot release its own hold; a hold older than the
    stale window is dead by definition and must free its headroom."""
    from datetime import UTC, datetime, timedelta

    book = _book_on(db)
    ledger = book.ledger
    assert ledger is not None
    # A hold taken far in the past, directly in the table.
    old = datetime.now(UTC) - timedelta(hours=48)
    db.conn.execute(
        "INSERT INTO reservations (id, pool_id, agent_id, tokens, created_at, settled) "
        "VALUES ('resv_old', 'shared', 'fake', 90000, ?, 0)",
        (old.isoformat(),),
    )
    db.conn.commit()
    assert ledger.outstanding("shared") == 0, "the stale hold must be reaped"
    # And the headroom it falsely claimed is available again.
    assert _reserve(book, "other", 50_000) is not None


def test_reaper_leaves_fresh_holds_alone(db):
    from datetime import UTC, datetime, timedelta

    book = _book_on(db)
    held = _reserve(book, "fake", 70_000)
    assert held is not None
    fresh = datetime.now(UTC) - timedelta(minutes=5)
    db.conn.execute(
        "UPDATE reservations SET created_at=? WHERE id=?", (fresh.isoformat(), held.id)
    )
    db.conn.commit()
    ledger = book.ledger
    assert ledger is not None
    assert ledger.outstanding("shared") == 70_000


def test_two_books_on_one_db_grant_at_most_the_usable_share(config, db):
    """Interleaved check-then-hold across two connections cannot overspend,
    because reserve() is one BEGIN IMMEDIATE transaction per grant."""
    from coderouter.storage.db import Database

    db_b = Database(config.db_path)
    try:
        book_a = _book_on(db)
        book_b = _book_on(db)
        granted = 0
        # A and B alternate, each trying to take 30k of the 85k usable share:
        # two grants fit (60k), the third (90k) must be refused by both books.
        for book, agent in [(book_a, "fake"), (book_b, "other")] * 3:
            if _reserve(book, agent, 30_000) is not None:
                granted += 30_000
        assert granted == 60_000, "85k usable: two grants, the third refused"
        ledger = book_a.ledger
        assert ledger is not None
        assert ledger.outstanding("shared") == 60_000
    finally:
        db_b.close()
