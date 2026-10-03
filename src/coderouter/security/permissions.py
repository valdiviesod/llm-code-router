"""Capability-based permissions.

`CommandPolicy` classifies *shell strings* — a string matcher for the builtin
`run_command` tool. It says nothing about *capabilities*: whether an agent may
write to the filesystem at all, whether it may reach the network, whether a
`git push` is ever acceptable. The agent CLIs run under their own permission
systems, which coderouter neither configures nor intercepts; this engine gates
what passes *through* the router — the builtin tools and the MCP bridge.

A rule maps a capability name to a decision:

    permissions:
      rules:
        - capability: git.push
          decision: deny
        - capability: shell.execute
          decision: ask
          pattern: "docker .*"

`pattern` is optional; when present the rule matches only details (command
strings, paths) that match the regex. The most specific matching rule wins:
capability+pattern beats capability-only; among equals, the first configured
rule wins. No rule matching means `allow` — the path boundary, the command
policy and the sandbox still apply on top.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import PermissionsConfig
from ..logging import get_logger
from .policy import Decision

logger = get_logger("security.permissions")

#: Well-known capability names. The set is open: a rule may name any string,
#: so plugins and future tools do not need this tuple edited to be governed.
CAPABILITIES = (
    "filesystem.read",
    "filesystem.write",
    "filesystem.delete",
    "shell.execute",
    "git.push",
    "git.commit",
    "network.access",
    "mcp.call",
)

_DECISIONS = {"allow": Decision.SAFE, "ask": Decision.ASK, "deny": Decision.BLOCK}


@dataclass(slots=True)
class PermissionRule:
    capability: str
    decision: Decision
    pattern: re.Pattern[str] | None = None

    def matches(self, capability: str, detail: str) -> bool:
        if self.capability != capability:
            return False
        return self.pattern is None or bool(self.pattern.search(detail))


class PermissionsEngine:
    """allow / deny / ask, per capability, on top of the other gates."""

    def __init__(self, config: PermissionsConfig | None = None):
        self._rules: list[PermissionRule] = []
        for raw in (config.rules if config else []):
            decision = _DECISIONS.get(str(raw.get("decision", "")).lower())
            if decision is None:
                logger.warning("permissions rule for %r has unknown decision %r; ignored",
                               raw.get("capability"), raw.get("decision"))
                continue
            pattern = None
            if raw.get("pattern"):
                try:
                    pattern = re.compile(str(raw["pattern"]))
                except re.error as exc:
                    logger.warning("permissions rule for %r has bad pattern: %s; ignored",
                                   raw.get("capability"), exc)
                    continue
            self._rules.append(PermissionRule(
                capability=str(raw.get("capability", "")), decision=decision,
                pattern=pattern,
            ))

    def check(self, capability: str, detail: str = "") -> tuple[Decision, str]:
        """The decision for `capability` given `detail` (a command, a path).

        Most specific match wins: a rule with a pattern that matches the
        detail outranks a patternless rule for the same capability.
        """
        best: PermissionRule | None = None
        for rule in self._rules:
            if not rule.matches(capability, detail):
                continue
            if best is None or (rule.pattern is not None and best.pattern is None):
                best = rule
                if rule.pattern is not None:
                    break  # most specific possible match
        if best is None:
            return Decision.SAFE, f"no permissions rule for {capability}"
        why = (f"permissions rule for {best.capability}"
               + (f" (pattern {best.pattern.pattern!r})" if best.pattern else ""))
        return best.decision, why
