"""Model cascade (debt plan P1).

`routing.cascade: true` makes a failed attempt retry on the same agent with
the next more expensive configured tier before giving up. Cheap → medium →
premium, up to `routing.max_attempts` steps.
"""

from __future__ import annotations

from coderouter.agents.base.adapter import AgentAdapter
from coderouter.agents.base.registry import AgentRegistry
from coderouter.config import AgentConfig
from coderouter.core.models import (
    AgentCapabilities,
    AgentResult,
    Capability,
    Complexity,
    HealthStatus,
    ModelInfo,
    Risk,
    Task,
    UsageStatus,
)
from coderouter.core.orchestrator import Orchestrator
from coderouter.routing.engine import RoutingEngine
from coderouter.usage.manager import UsageManager


class _CascadeAdapter(AgentAdapter):
    def __init__(self, config, *, agent_id="cascade", pass_on_attempt=99):
        super().__init__(config)
        self._id = agent_id
        self.pass_on_attempt = pass_on_attempt
        self.attempts: list[str] = []
        self.caps = AgentCapabilities(frozenset({
            Capability.CODE_EDIT, Capability.MODEL_SELECTION,
        }))

    @property
    def id(self): return self._id
    @property
    def display_name(self): return "Cascade"
    @property
    def default_command(self): return "true"
    @property
    def capabilities(self): return self.caps
    async def health_check(self): return HealthStatus(self._id, True, "ok")
    async def get_models(self): return [ModelInfo("m1", "M1", self._id)]
    async def execute(self, task, *, model=None, on_event=None):
        import sys
        print(f"DEBUG adapter.execute received model={model!r}", file=sys.stderr)
        self.attempts.append(model or "default")
        return AgentResult(
            task_id=task.id, agent_id=self._id, model=model,
            success=len(self.attempts) >= self.pass_on_attempt,
            output="done" if len(self.attempts) >= self.pass_on_attempt else "",
            error="first attempt fails" if len(self.attempts) < self.pass_on_attempt else None,
            usage_status=UsageStatus.CONFIRMED,
        )

    async def estimate(self, task):  # type: ignore[override]
        from coderouter.core.models import UsageEstimate
        return UsageEstimate(input_tokens=100, output_tokens=100, confidence=0.4)


def _cascade_config(config, *, max_attempts: int) -> None:
    """All five canonical tiers mapped to haiku/sonnet/opus, except MEDIUM
    and HIGH collapse to sonnet so the cascade must skip a duplicate."""
    config.routing.cascade = True
    config.routing.max_attempts = max_attempts
    config.agents = {"cascade": AgentConfig(
        command="true",
        model_tiers={
            "trivial": "haiku", "low": "haiku", "medium": "sonnet",
            "high": "sonnet", "critical": "opus",
        },
    )}


def _registry_with(adapter: AgentAdapter) -> AgentRegistry:
    class _Reg(AgentRegistry):
        def __init__(self):
            self._adapters = {adapter.id: adapter}
    return _Reg()


async def test_cascade_advances_to_next_distinct_model_on_failure(config, db, tmp_path):
    adapter = _CascadeAdapter(config.agent("cascade"), pass_on_attempt=2)
    _cascade_config(config, max_attempts=3)

    orch = Orchestrator(config, db, registry=_registry_with(adapter))
    task = Task(prompt="p", complexity=Complexity.MEDIUM, project_root=tmp_path, risk=Risk.LOW)
    out = await orch.run_task(task)

    import sys
    print(f"DEBUG final attempts: {adapter.attempts}", file=sys.stderr)
    assert out.result is not None and out.result.success is True
    # MEDIUM is sonnet; HIGH is sonnet again so the cascade must skip it; CRITICAL is opus.
    assert adapter.attempts == ["sonnet", "opus"], adapter.attempts
    cascades = list(db.conn.execute("SELECT * FROM audit_log WHERE action='cascade'"))
    assert len(cascades) == 1, "exactly one cascade step was needed"


async def test_cascade_gives_up_when_max_attempts_reached(config, db, tmp_path):
    adapter = _CascadeAdapter(config.agent("cascade"), pass_on_attempt=99)
    _cascade_config(config, max_attempts=2)

    orch = Orchestrator(config, db, registry=_registry_with(adapter))
    task = Task(prompt="p", complexity=Complexity.MEDIUM, project_root=tmp_path, risk=Risk.LOW)
    out = await orch.run_task(task)

    assert out.result is not None
    assert out.result.success is False
    # Distinct models in the cascade are sonnet, opus. max_attempts=2 means
    # both are tried; the adapter never succeeds so the result is failure.
    assert adapter.attempts == ["sonnet", "opus"]


async def test_cascade_for_starts_at_the_task_complexity(config, db, tmp_path):
    config.agents = {"cascade": AgentConfig(command="true", model_tiers={
        "low": "haiku", "medium": "sonnet", "high": "opus",
    })}
    config.routing.max_attempts = 3
    adapter = _CascadeAdapter(config.agent("cascade"))
    engine = RoutingEngine(
        config, _registry_with(adapter), UsageManager(config, db), db
    )
    assert engine._cascade_for(adapter, Task(prompt="p", complexity=Complexity.MEDIUM,
                                              project_root=tmp_path)) == ["sonnet", "opus"]
    assert engine._cascade_for(adapter, Task(prompt="p", complexity=Complexity.HIGH,
                                              project_root=tmp_path)) == ["opus"]


async def test_cascade_off_by_default_does_not_retry_with_a_different_model(
    config, db, tmp_path
):
    adapter = _CascadeAdapter(config.agent("cascade"), pass_on_attempt=99)
    _cascade_config(config, max_attempts=3)
    config.routing.cascade = False  # the v0.1.0 default

    orch = Orchestrator(config, db, registry=_registry_with(adapter))
    task = Task(prompt="p", complexity=Complexity.MEDIUM, project_root=tmp_path, risk=Risk.LOW)
    out = await orch.run_task(task)

    assert out.result is not None
    # One attempt and done; the cascade did not fire. An agent swap may
    # follow, but the adapter itself only saw one model.
    cascades = list(db.conn.execute("SELECT * FROM audit_log WHERE action='cascade'"))
    assert cascades == []
    assert len(adapter.attempts) == 1
