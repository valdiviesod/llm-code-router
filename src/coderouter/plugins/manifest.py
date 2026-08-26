"""Plugin manifest.

A plugin is anything that opts into the router by exposing a Python
entry point under the `coderouter.plugins` group. A plugin may
contribute:

- An `AgentAdapter` subclass (the `coderouter.agents` entry-point
  group already does this; the manifest is the superset).
- A `Skill` factory.
- A `Tool` factory.
- An MCP server config (just a dict the orchestrator can register).

The manifest is opt-in. `plugins.scan: true` enables it; default
off so the unconfigured case is unchanged.

Plugins are best-effort: a broken entry point is logged and
skipped, never raised. The discovery function returns a typed
`PluginBundle` the orchestrator can iterate over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from ..logging import get_logger

logger = get_logger("plugins.manifest")


@dataclass(slots=True)
class PluginBundle:
    name: str
    adapters: list[type] = field(default_factory=list)
    skill_factories: list = field(default_factory=list)
    tool_factories: list = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)


def _ep_group(ep: EntryPoint) -> str:
    return ep.group


def _load_one(ep: EntryPoint) -> PluginBundle | None:
    """Load a single entry point and translate it to a bundle.

    A plugin entry point may be:
    - a `PluginBundle` instance already (rare, useful for tests);
    - a callable returning a `PluginBundle` or a dict;
    - a dict with the same shape as `PluginBundle`.

    Anything else is logged and skipped.
    """
    try:
        obj = ep.load()
    except Exception as exc:  # noqa: BLE001 - one bad plugin must not kill discovery
        logger.warning("plugin %r failed to load: %s", ep.name, exc)
        return None
    if isinstance(obj, PluginBundle):
        return obj
    if isinstance(obj, dict):
        return PluginBundle(
            name=ep.name,
            adapters=list(obj.get("adapters", [])),
            skill_factories=list(obj.get("skills", [])),
            tool_factories=list(obj.get("tools", [])),
            mcp_servers=list(obj.get("mcp_servers", [])),
        )
    if callable(obj):
        try:
            produced = obj()
        except Exception as exc:  # noqa: BLE001
            logger.warning("plugin %r callable raised: %s", ep.name, exc)
            return None
        if isinstance(produced, PluginBundle):
            return produced
        if isinstance(produced, dict):
            return PluginBundle(
                name=ep.name,
                adapters=list(produced.get("adapters", [])),
                skill_factories=list(produced.get("skills", [])),
                tool_factories=list(produced.get("tools", [])),
                mcp_servers=list(produced.get("mcp_servers", [])),
            )
        logger.warning("plugin %r callable returned %s, expected bundle or dict",
                       ep.name, type(produced).__name__)
        return None
    logger.warning("plugin %r exported %s, expected a bundle/dict/callable",
                   ep.name, type(obj).__name__)
    return None


def discover_plugins(groups: tuple[str, ...] = ("coderouter.plugins",)) -> list[PluginBundle]:
    """Find every entry point in any of the given groups and return bundles.

    Discovery is best-effort: a plugin that fails to import is
    logged and skipped, never raised. The result preserves the
    entry-point order.
    """
    bundles: list[PluginBundle] = []
    try:
        eps = entry_points()
    except Exception as exc:  # noqa: BLE001 - importlib.metadata can fail
        logger.warning("entry point discovery failed: %s", exc)
        return bundles
    selected = []
    for group in groups:
        for ep in eps.select(group=group):
            selected.append(ep)
    for ep in selected:
        bundle = _load_one(ep)
        if bundle is not None:
            bundles.append(bundle)
    return bundles
