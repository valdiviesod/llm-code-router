"""Tests for the plugin manifest subsystem."""

from __future__ import annotations

from coderouter.plugins import PluginBundle, discover_plugins
from coderouter.plugins.manifest import _load_one


class _FakeEntryPoint:
    """Stand-in for importlib.metadata.EntryPoint."""

    def __init__(self, name: str, obj: object):
        self.name = name
        self._obj = obj
        self.group = "coderouter.plugins"

    def load(self):
        return self._obj


def test_load_one_from_bundle():
    bundle = PluginBundle(name="test", adapters=["a"], tool_factories=["t"])
    ep = _FakeEntryPoint("test", bundle)
    result = _load_one(ep)
    assert result is not None
    assert result.name == "test"
    assert result.adapters == ["a"]
    assert result.tool_factories == ["t"]


def test_load_one_from_dict():
    ep = _FakeEntryPoint("test", {
        "adapters": ["a"],
        "skills": ["s"],
        "tools": ["t"],
        "mcp_servers": [{"name": "fs"}],
    })
    result = _load_one(ep)
    assert result is not None
    assert result.name == "test"
    assert result.adapters == ["a"]
    assert result.mcp_servers == [{"name": "fs"}]


def test_load_one_from_callable():
    def factory():
        return PluginBundle(name="from-factory", adapters=["a"])
    ep = _FakeEntryPoint("test", factory)
    result = _load_one(ep)
    assert result is not None
    assert result.name == "from-factory"


def test_load_one_from_callable_returning_dict():
    def factory():
        return {"adapters": ["a"], "tools": ["t"]}
    ep = _FakeEntryPoint("test", factory)
    result = _load_one(ep)
    assert result is not None
    assert result.adapters == ["a"]


def test_load_one_returns_none_on_bad_callable():
    def factory():
        raise RuntimeError("boom")
    ep = _FakeEntryPoint("test", factory)
    assert _load_one(ep) is None


def test_load_one_returns_none_on_bad_type():
    ep = _FakeEntryPoint("test", 42)
    assert _load_one(ep) is None


def test_discover_plugins_returns_empty_when_no_plugins():
    bundles = discover_plugins(groups=("coderouter.nonexistent",))
    assert bundles == []


def test_plugin_bundle_defaults():
    bundle = PluginBundle(name="x")
    assert bundle.adapters == []
    assert bundle.skill_factories == []
    assert bundle.tool_factories == []
    assert bundle.mcp_servers == []
