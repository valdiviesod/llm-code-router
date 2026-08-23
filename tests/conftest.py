from __future__ import annotations

from pathlib import Path

import pytest

from coderouter.agents.base.adapter import AgentAdapter
from coderouter.agents.base.registry import AgentRegistry
from coderouter.config import AgentConfig, Config
from coderouter.core.models import (
    AgentCapabilities,
    AgentResult,
    Capability,
    HealthStatus,
    ModelInfo,
    Task,
    UsageStatus,
)
from coderouter.core.orchestrator import Orchestrator
from coderouter.storage.db import Database


class FakeAdapter(AgentAdapter):
    """In-memory adapter used everywhere a real CLI would be. No subprocesses."""

    def __init__(self, config, agent_id="fake", caps=None, succeed=True, tokens=1000, completion=None):
        super().__init__(config)
        self._id = agent_id
        self._caps = caps or frozenset({Capability.CODE_EDIT, Capability.SHELL})
        self.succeed = succeed
        self.tokens = tokens
        self.completion = completion
        self.calls: list[Task] = []
        self.complete_calls: list[dict] = []

    @property
    def id(self): return self._id

    @property
    def display_name(self): return self._id.title()

    @property
    def default_command(self): return "true"

    @property
    def capabilities(self): return AgentCapabilities(self._caps)

    def binary_available(self): return "/bin/true"

    async def health_check(self): return HealthStatus(self._id, True, "ok", version="1.0")

    async def get_models(self): return [ModelInfo("m1", "Model One", self._id)]

    async def complete(self, prompt, *, system="", schema=None, model=None, timeout=120):
        self.complete_calls.append({
            "prompt": prompt, "system": system, "schema": schema, "model": model, "timeout": timeout
        })
        return self.completion

    async def execute(self, task, *, model=None, on_event=None):
        self.calls.append(task)
        return AgentResult(
            task_id=task.id, agent_id=self._id, model=model, success=self.succeed,
            output="done" if self.succeed else "boom",
            input_tokens=self.tokens // 2, output_tokens=self.tokens // 2,
            usage_status=UsageStatus.CONFIRMED, duration_s=0.1,
            error=None if self.succeed else "failed",
        )


class FakeRegistry(AgentRegistry):
    def __init__(self, adapters: list[AgentAdapter]):
        self._config = None
        self._adapters = {a.id: a for a in adapters}


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config(data_dir=tmp_path)
    cfg.agents = {
        "fake": AgentConfig(command="true"),
        "other": AgentConfig(command="true"),
    }
    return cfg


@pytest.fixture
def db(config: Config) -> Database:
    database = Database(config.db_path)
    yield database
    database.close()


@pytest.fixture
def adapters(config: Config) -> list[FakeAdapter]:
    return [
        FakeAdapter(config.agent("fake"), "fake"),
        FakeAdapter(config.agent("other"), "other",
                    caps=frozenset({Capability.CODE_EDIT, Capability.DEEP_REASONING,
                                    Capability.PLANNING, Capability.REVIEW})),
    ]


@pytest.fixture
def orchestrator(config, db, adapters) -> Orchestrator:
    return Orchestrator(config, db, registry=FakeRegistry(adapters))
