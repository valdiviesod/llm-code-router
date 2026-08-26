"""Plugin discovery.

`discover_plugins()` returns the bundles registered under
`coderouter.plugins` (or any extra groups the caller passes). The
manifest itself is opt-in: a built-in install with no `plugins:`
config and no plugins installed discovers zero bundles and
behaves identically to v0.1.0.
"""

from .manifest import PluginBundle, discover_plugins

__all__ = ["PluginBundle", "discover_plugins"]
