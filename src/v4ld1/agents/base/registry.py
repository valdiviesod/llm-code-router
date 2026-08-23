"""Adapter discovery. The core asks the registry, never a concrete class."""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator

from ...config import Config
from .adapter import AgentAdapter

_ADAPTER_CLASSES: dict[str, type[AgentAdapter]] = {}


def register(cls: type[AgentAdapter]) -> type[AgentAdapter]:
    """Decorator used by each adapter module to make itself discoverable."""
    instance_id = cls.id.fget(cls) if isinstance(cls.id, property) else cls.id  # type: ignore[union-attr]
    _ADAPTER_CLASSES[str(instance_id)] = cls
    return cls


def _discover() -> None:
    import v4ld1.agents as pkg

    for mod in pkgutil.iter_modules(pkg.__path__):
        if mod.name in ("base",) or not mod.ispkg:
            continue
        try:
            importlib.import_module(f"v4ld1.agents.{mod.name}.adapter")
        except ModuleNotFoundError:
            continue
    # External plugins declare entry points in the "v4ld1.agents" group.
    try:
        from importlib.metadata import entry_points

        for ep in entry_points(group="v4ld1.agents"):
            ep.load()
    except Exception:  # pragma: no cover - plugin loading is best-effort
        pass


class AgentRegistry:
    def __init__(self, config: Config):
        self._config = config
        _discover()
        self._adapters: dict[str, AgentAdapter] = {}
        for agent_id, cls in _ADAPTER_CLASSES.items():
            agent_cfg = config.agent(agent_id)
            if not agent_cfg.enabled:
                continue
            self._adapters[agent_id] = cls(agent_cfg)

    def __iter__(self) -> Iterator[AgentAdapter]:
        return iter(self._adapters.values())

    def __len__(self) -> int:
        return len(self._adapters)

    @property
    def ids(self) -> list[str]:
        return list(self._adapters)

    def get(self, agent_id: str) -> AgentAdapter | None:
        return self._adapters.get(agent_id)

    def available(self) -> list[AgentAdapter]:
        return [a for a in self._adapters.values() if a.binary_available()]
