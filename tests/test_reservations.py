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
