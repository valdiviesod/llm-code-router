import pytest

from coderouter.core.models import Capability, Complexity, RoutingMode, Task
from coderouter.errors import NoViableAgent


async def test_capability_requirement_filters_candidates(orchestrator):
    task = Task(prompt="review this",
                required_capabilities=frozenset({Capability.REVIEW}))
    decision = await orchestrator.router.decide(task)
    assert decision.selected_agent == "other"


async def test_no_agent_with_capability_raises(orchestrator):
    task = Task(prompt="x", required_capabilities=frozenset({Capability.USAGE_REPORTING}))
    with pytest.raises(NoViableAgent):
        await orchestrator.router.decide(task)


async def test_user_override_wins(orchestrator):
    task = Task(prompt="anything", forced_agent="fake")
    decision = await orchestrator.router.decide(task)
    assert decision.selected_agent == "fake"
    assert "user override" in decision.reason or any(
        "user override" in r for r in [decision.reason])


async def test_unknown_forced_agent_raises(orchestrator):
    with pytest.raises(NoViableAgent):
        await orchestrator.router.decide(Task(prompt="x", forced_agent="nope"))


@pytest.mark.parametrize("complexity,expected", [
    (Complexity.TRIVIAL, RoutingMode.ECONOMY),
    (Complexity.MEDIUM, RoutingMode.BALANCED),
    (Complexity.CRITICAL, RoutingMode.QUALITY),
])
def test_auto_mode_resolves_by_complexity(orchestrator, complexity, expected):
    task = Task(prompt="x", complexity=complexity)
    assert orchestrator.router.mode_for(task) is expected


async def test_quota_veto_zeroes_score(orchestrator, config, db):
    for agent_id in ("fake", "other"):
        config.agents[agent_id].window_limit_tokens = 100
        db.conn.execute(
            "INSERT INTO usage_events (agent_id, run_id, tokens, status, occurred_at) "
            "VALUES (?,?,?,?,datetime('now'))", (agent_id, "r", 99, "confirmed"))
    db.conn.commit()
    with pytest.raises(NoViableAgent, match="quota"):
        await orchestrator.router.decide(Task(prompt="x", complexity=Complexity.MEDIUM))


async def test_critical_task_may_tap_the_reserve(orchestrator, config, db):
    for agent_id in ("fake", "other"):
        config.agents[agent_id].window_limit_tokens = 100
        db.conn.execute(
            "INSERT INTO usage_events (agent_id, run_id, tokens, status, occurred_at) "
            "VALUES (?,?,?,?,datetime('now'))", (agent_id, "r", 99, "confirmed"))
    db.conn.commit()
    decision = await orchestrator.router.decide(
        Task(prompt="x", complexity=Complexity.CRITICAL))
    assert decision.selected_agent in {"fake", "other"}


async def test_weights_sum_to_one():
    from coderouter.routing.engine import WEIGHTS
    for mode, weights in WEIGHTS.items():
        assert abs(sum(weights.values()) - 1.0) < 1e-9, mode


async def test_decision_is_persisted(orchestrator, db):
    await orchestrator.run_task(Task(prompt="do it"))
    row = db.conn.execute("SELECT COUNT(*) AS n FROM routing_decisions").fetchone()
    assert row["n"] >= 1


def _with_tiers(orchestrator, model_selection=True):
    orchestrator.config.agents["fake"].model_tiers = {
        "trivial": "cheap", "low": "cheap", "medium": "mid",
        "high": "top", "critical": "top",
    }
    adapter = orchestrator.registry.get("fake")
    if model_selection:
        adapter._caps = adapter._caps | {Capability.MODEL_SELECTION}
    return adapter


@pytest.mark.parametrize("complexity,expected", [
    (Complexity.TRIVIAL, "cheap"),
    (Complexity.MEDIUM, "mid"),
    (Complexity.CRITICAL, "top"),
])
def test_model_tier_follows_complexity(orchestrator, complexity, expected):
    adapter = _with_tiers(orchestrator)
    task = Task(prompt="x", complexity=complexity)
    assert orchestrator.router._model_for(adapter, task, RoutingMode.BALANCED) == expected


def test_quality_mode_always_takes_the_top_tier(orchestrator):
    adapter = _with_tiers(orchestrator)
    task = Task(prompt="x", complexity=Complexity.TRIVIAL)
    assert orchestrator.router._model_for(adapter, task, RoutingMode.QUALITY) == "top"


def test_no_tiers_falls_back_to_default_model(orchestrator):
    orchestrator.config.agents["fake"].default_model = "fallback"
    adapter = orchestrator.registry.get("fake")
    assert orchestrator.router._model_for(
        adapter, Task(prompt="x"), RoutingMode.BALANCED) == "fallback"


def test_tiers_ignored_without_model_selection_capability(orchestrator):
    adapter = _with_tiers(orchestrator, model_selection=False)
    orchestrator.config.agents["fake"].default_model = "fallback"
    # FakeAdapter declares only CODE_EDIT and SHELL, so it cannot pick models.
    assert orchestrator.router._model_for(
        adapter, Task(prompt="x"), RoutingMode.BALANCED) == "fallback"


async def test_unhealthy_agent_is_rejected_before_scoring(orchestrator, adapters):
    """A provider that is down burns an attempt and a retry for a guaranteed
    failure. It is a hard constraint, not a low score."""
    from coderouter.core.models import HealthStatus

    async def _down():
        return HealthStatus("fake", healthy=False, detail="not logged in")

    adapters[0].health_check = _down
    decision = await orchestrator.router.decide(Task(prompt="anything"))

    assert decision.selected_agent == "other"
    assert ("fake", "unhealthy: not logged in") in decision.rejected


async def test_agent_whose_context_window_is_too_small_is_rejected(config, db):
    from conftest import FakeAdapter, FakeRegistry

    from coderouter.core.models import AgentCapabilities, UsageEstimate
    from coderouter.core.orchestrator import Orchestrator

    class NarrowAdapter(FakeAdapter):
        @property
        def capabilities(self):
            return AgentCapabilities(self._caps, max_context_tokens=10)

        async def estimate(self, task):
            return UsageEstimate(input_tokens=50_000, output_tokens=1_000)

    adapters = [NarrowAdapter(config, "fake"), FakeAdapter(config, "other")]
    router = Orchestrator(config, db, FakeRegistry(adapters)).router

    decision = await router.decide(Task(prompt="huge repo"))
    assert decision.selected_agent == "other"
    assert any("context window is 10" in why for _aid, why in decision.rejected)


async def test_health_probe_failure_does_not_veto(orchestrator, adapters):
    """If our own check breaks, trying is better than refusing to route."""
    async def _boom():
        raise RuntimeError("probe exploded")

    adapters[0].health_check = _boom
    adapters[1].health_check = _boom
    decision = await orchestrator.router.decide(Task(prompt="anything"))
    assert decision.selected_agent in {"fake", "other"}


async def test_health_is_cached_across_decisions(orchestrator, adapters):
    from coderouter.core.models import HealthStatus

    calls = []

    async def _counted():
        calls.append(1)
        return HealthStatus("fake", healthy=True, detail="ok")

    adapters[0].health_check = _counted
    await orchestrator.router.decide(Task(prompt="one"))
    await orchestrator.router.decide(Task(prompt="two"))
    assert len(calls) == 1, "health is a subprocess call; probe it once per TTL"


async def test_rejections_are_reported_when_nothing_survives(orchestrator, adapters):
    from coderouter.core.models import HealthStatus

    async def _down():
        return HealthStatus("x", healthy=False, detail="offline")

    for adapter in adapters:
        adapter.health_check = _down
    with pytest.raises(NoViableAgent, match="offline"):
        await orchestrator.router.decide(Task(prompt="anything"))
