# Agent layer (`coderouter/agents/`)

**Why** The whole product rests on being able to add an agent without touching
anything else.

**Responsibility** Translate a provider-neutral `Task` into a CLI invocation, and
the CLI's output back into an `AgentResult`.

**Structure**

```
agents/
  base/adapter.py    the AgentAdapter interface + subprocess helpers
  base/registry.py   discovery: built-in packages + "coderouter.agents" entry points
  claude/adapter.py
  antigravity/adapter.py
```

**Interface** `id`, `display_name`, `default_command`, `capabilities`,
`health_check()`, `get_models()`, `get_usage()`, `estimate()`, `execute()`,
`cancel()`.

**Must not**
- Import `routing/`, `core/orchestrator.py` or `tui/`.
- Raise on provider failure. Return `AgentResult(success=False, error=...)`.
- Invent usage numbers. Absent provider data means `UsageStatus.UNKNOWN`.

**Capabilities, not names** Differences between agents are expressed as
`Capability` values. The router asks "who has DEEP_REASONING?", never "is this
Claude?".

**Parsing is a pure function** Each adapter exposes `parse_result(...)`, so the
contract tests exercise real captured payloads without spawning a process.

**Verified CLI behaviour**

| Agent | Non-interactive invocation | Usage reported |
|---|---|---|
| Claude Code 2.1.239 | `claude -p --output-format json <prompt>` | per-run tokens + `total_cost_usd` |
| Antigravity 1.1.17 | `agy --output-format json --print=<prompt>` | per-run tokens |

Antigravity uses Go flag parsing: `--print` takes its value attached, and other
flags must precede it or they are swallowed as the prompt.

**Extending** See [../extending/adding-agent.md](../extending/adding-agent.md).
