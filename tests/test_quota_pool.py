"""Tests for the quota pool subsystem."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from coderouter.core.models import Complexity
from coderouter.usage.quota_pool import QuotaBook, QuotaPool


def _spend(db, agent_id, tokens, hours_ago=0.0):
    when = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    db.conn.execute(
        "INSERT INTO usage_events (agent_id, run_id, tokens, status, occurred_at, kind) "
        "VALUES (?,?,?,?,?, 'run')",
        (agent_id, "r", tokens, "confirmed", when),
    )
    db.commit() if hasattr(db, "commit") else db.conn.commit()


def test_implicit_pool_per_agent_when_unconfigured(config, db):
    book = QuotaBook.build([], ["claude", "antigravity"], db)
    assert "claude" in book.implicit
    assert "antigravity" in book.implicit
    pools = book.pools_for("claude")
    # Exactly one implicit pool per agent when nothing is configured.
    assert len(pools) == 1
    assert pools[0].id == "_implicit:claude"


def test_configured_pool_aggregates_across_agents(config, db, adapters):
    pool = QuotaPool(
        id="team-claude", kind="subscription", tier="standard",
        agent_ids=("fake", "other"),
        limit_tokens=1000, reserve_percent=10.0,
    )
    book = QuotaBook.build([pool], ["fake", "other"], db)
    _spend(db, "fake", 400)
    _spend(db, "other", 100)
    # SQL aggregation: the book sees 500/1000 = 0.5 across the team.
    info = book.usage_info(pool)
    assert info.status.value == "estimated"
    window = info.windows[0]
    assert window.used_tokens == 500
    assert window.fraction == 0.5


def test_pressure_maxes_across_pools(config, db, adapters):
    """A more-pressured pool dominates; routing should not prefer a
    quieter pool that the agent isn't in.
    """
    pool = QuotaPool(
        id="narrow", kind="subscription", tier="standard",
        agent_ids=("fake",), limit_tokens=100, reserve_percent=0.0,
    )
    book = QuotaBook.build([pool], ["fake", "other"], db)
    _spend(db, "fake", 90)  # 90% of the narrow pool
    assert book.pressure("fake") == 0.9


def test_premium_pool_refuses_non_critical(config, db, adapters):
    pool = QuotaPool(
        id="premium", kind="subscription", tier="premium",
        agent_ids=("fake",), limit_tokens=1_000_000, reserve_percent=0.0,
    )
    book = QuotaBook.build([pool], ["fake"], db)
    ok, reason = book.can_fund("fake", 100, complexity=Complexity.LOW,
                                user_override=False)
    assert not ok
    ok2, _ = book.can_fund("fake", 100, complexity=Complexity.CRITICAL,
                            user_override=False)
    assert ok2
    ok3, _ = book.can_fund("fake", 100, complexity=Complexity.LOW,
                            user_override=True)
    assert ok3


def test_can_fund_uses_reserve(config, db, adapters):
    pool = QuotaPool(
        id="tight", kind="subscription", tier="standard",
        agent_ids=("fake",), limit_tokens=100, reserve_percent=20.0,
    )
    book = QuotaBook.build([pool], ["fake"], db)
    _spend(db, "fake", 75)
    # 75% used, reserve 20% -> usable ceiling 80%. A 4-token task would
    # still fit (79/100 < 80%); a 10-token task would not.
    ok, _ = book.can_fund("fake", 4, complexity=Complexity.MEDIUM, user_override=False)
    assert ok
    ok2, reason = book.can_fund("fake", 10, complexity=Complexity.MEDIUM, user_override=False)
    assert not ok2
    assert "reserve" in reason


def test_pools_for_returns_configured_then_implicit(config, db, adapters):
    configured = QuotaPool(
        id="team", kind="subscription", tier="standard",
        agent_ids=("fake", "other"), limit_tokens=1000,
    )
    book = QuotaBook.build([configured], ["fake", "other"], db)
    pools = book.pools_for("fake")
    assert pools[0].id == "team"
    # No implicit pool when the agent is covered by a configured one.
    assert all(p.id != "_implicit:fake" for p in pools)


def test_disabled_pool_excludes_agent(config, db, adapters):
    pool = QuotaPool(
        id="off", kind="subscription", tier="standard",
        agent_ids=("fake",), limit_tokens=100, enabled=False,
    )
    book = QuotaBook.build([pool], ["fake", "other"], db)
    pools = book.pools_for("fake")
    # Disabled pool is filtered; agent falls through to implicit.
    assert all(p.id != "off" for p in pools)
    assert any(p.id == "_implicit:fake" for p in pools)
