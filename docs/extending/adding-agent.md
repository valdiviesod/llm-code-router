# Adding an agent

The goal: a new agent is one new directory. No changes to the router, scheduler,
TUI, database or core. This walkthrough uses a hypothetical Codex CLI.

## 1. Create the adapter

```
src/v4ld1/agents/codex/
    __init__.py
    adapter.py
```

## 2. Implement the interface

```python
from ..base.adapter import AgentAdapter
from ..base.registry import register

@register
class CodexAdapter(AgentAdapter):
    @property
    def id(self) -> str: return "codex"

    @property
    def display_name(self) -> str: return "Codex"

    @property
    def default_command(self) -> str: return "codex"
```

## 3. Declare capabilities

Capabilities are how the router reasons about your agent. Declare only what it
genuinely does — an overclaimed capability sends it work it will fail.

```python
_CAPS = AgentCapabilities(
    capabilities=frozenset({Capability.CODE_EDIT, Capability.SHELL}),
    max_context_tokens=128_000,
)
```

## 4. Declare models

```python
async def get_models(self) -> list[ModelInfo]:
    code, out, _ = await self._run([self.command, "models"])
    ...
```

Prefer asking the CLI over hardcoding a list that will go stale.

## 5. Health check

Must never raise, and must return a `remediation` string when unhealthy —
`router doctor` prints it verbatim to the user.

```python
async def health_check(self) -> HealthStatus:
    if not self.binary_available():
        return HealthStatus(self.id, False, "not on PATH",
                            remediation="Install the Codex CLI")
    ...
```

## 6. Execution

**Inspect the real CLI first.** Run `--help`, run one real non-interactive
invocation, and capture the actual JSON. Do not assume flags.

Keep parsing in a pure `parse_result(task, model, code, out, err, duration)` so
the contract tests can drive it without a subprocess. Return
`AgentResult(success=False, error=...)` on failure — never raise.

## 7. Usage

If the CLI reports per-run tokens, populate `input_tokens`/`output_tokens` and set
`usage_status=CONFIRMED`. If it exposes real quota, override `get_usage()`.
If it exposes neither, do nothing: the base class returns `UNKNOWN` and the
`UsageManager` handles estimation. **Never invent numbers.**

## 8. Register

The `@register` decorator plus the package location is enough for built-in
adapters — `AgentRegistry` imports `v4ld1.agents.*.adapter` automatically.

For an out-of-tree plugin, publish an entry point instead:

```toml
[project.entry-points."v4ld1.agents"]
codex = "my_package.adapter:CodexAdapter"
```

## 9. Tests

Add your class to `ADAPTERS` in `tests/test_adapter_contract.py` and to
`NATIVE_PAYLOADS` with a payload **captured from the real CLI**. The shared
contract then covers identity, capabilities, models, usage defaults, estimation,
health check, parsing (success, native shape, reported failure, non-JSON,
non-zero exit), argv and cancellation.

Add a live test to `tests/test_live_providers.py` behind
`@pytest.mark.provider` so it stays out of the default suite.

## 10. Document limitations

Add a row to the verified-CLI table in
[../architecture/agents.md](../architecture/agents.md) stating the exact version
you tested, the invocation, and what usage data is genuinely available. Update
`CHANGELOG.md`. If a decision was architectural, write an ADR.

## Checklist

- [ ] Adapter implements every abstract member
- [ ] Capabilities are honest
- [ ] `health_check()` returns a remediation when unhealthy
- [ ] `execute()` never raises on provider failure
- [ ] Usage is CONFIRMED only when the provider confirmed it
- [ ] Contract tests pass
- [ ] No core file changed
