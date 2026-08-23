from datetime import UTC, datetime, timedelta

from coderouter.core.models import UsageEstimate, UsageStatus
from coderouter.usage.manager import UsageManager


def _spend(db, agent_id, tokens, hours_ago=0.0):
    when = datetime.now(UTC) - timedelta(hours=hours_ago)
    db.conn.execute(
        "INSERT INTO usage_events (agent_id, run_id, tokens, status, occurred_at) "
        "VALUES (?,?,?,?,?)", (agent_id, "r", tokens, "confirmed", when.isoformat()))
    db.conn.commit()


async def test_unknown_limit_reports_unknown(config, db, adapters):
    manager = UsageManager(config, db)
    info = await manager.usage_for(adapters[0])
    assert info.status is UsageStatus.UNKNOWN
    assert all(w.fraction is None for w in info.windows)


async def test_configured_limit_gives_estimated_fraction(config, db, adapters):
    config.agents["fake"].window_limit_tokens = 1000
    _spend(db, "fake", 400)
    manager = UsageManager(config, db)
    info = await manager.usage_for(adapters[0])
    assert info.status is UsageStatus.ESTIMATED
    assert info.window("5h").fraction == 0.4


async def test_events_outside_the_window_are_excluded(config, db, adapters):
    config.agents["fake"].window_limit_tokens = 1000
    _spend(db, "fake", 900, hours_ago=6)
    manager = UsageManager(config, db)
    assert await manager.pressure(adapters[0]) == 0.0


async def test_forecast_blocks_when_projection_breaches_reserve(config, db, adapters):
    config.agents["fake"].window_limit_tokens = 1000
    config.agents["fake"].reserve_percent = 20  # usable ceiling is 80%
    _spend(db, "fake", 700)
    manager = UsageManager(config, db)
    forecast = await manager.forecast(adapters[0], UsageEstimate(150, 100))
    assert not forecast.safe
    assert "reserve" in forecast.reason


async def test_forecast_safe_without_limits(config, db, adapters):
    manager = UsageManager(config, db)
    forecast = await manager.forecast(adapters[0], UsageEstimate(10_000, 10_000))
    assert forecast.safe and forecast.projected_fraction is None


async def test_conservation_mode_needs_all_known_agents_under_pressure(config, db, adapters):
    config.agents["fake"].window_limit_tokens = 1000
    config.agents["other"].window_limit_tokens = 1000
    _spend(db, "fake", 800)
    manager = UsageManager(config, db)
    assert not await manager.conservation_mode(adapters)
    _spend(db, "other", 800)
    assert await manager.conservation_mode(adapters)
