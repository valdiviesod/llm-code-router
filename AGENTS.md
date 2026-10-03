# CodeRouter — project rules

Inherits global rules from `/root/AGENTS.md` (incl. "Token & cache efficiency"). More specific rules here win.

AI coding orchestrator. It routes work between Claude Code and the Antigravity
CLI, tracks what every run costs, and is built so that adding a tenth agent is
one directory.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing anything structural.
Decisions live in [docs/adr/](docs/adr/); if you are about to contradict one,
write a new ADR instead of quietly diverging.

## The command

The console command is `router`. The Python package, the config path
(`~/.config/coderouter/config.yaml`) and the data directory (`~/.local/share/coderouter/`)
are all still named `coderouter`. Do not "fix" that inconsistency: renaming them
breaks every existing install's config and database.

## Non-negotiable invariants

These are the properties the whole design rests on. Breaking one is a bug even
if the tests pass.

### 1. No provider branching outside `agents/<provider>/`

```python
# NEVER, anywhere in core/, routing/, scheduler/, tui/, storage/
if agent_id == "claude":
    ...
```

Differences between agents are expressed as `Capability` values that adapters
declare and the router reads. If you need new behaviour for one agent, add a
capability, do not add a branch. This is the single rule that keeps agent number
ten cheap. See [ADR-002](docs/adr/ADR-002-agent-adapter-architecture.md).

### 2. Never invent a usage number

Neither CLI exposes subscription quota. Every usage figure carries provenance:

| Status | Means |
|---|---|
| `CONFIRMED` | The provider reported it. |
| `ESTIMATED` | We summed our own runs against a user-configured limit. |
| `UNKNOWN` | No limit configured, so no fraction can be computed. |

Limits are configuration, never constants in code — they are plan-specific and
they change. A plausible-looking fabricated percentage is worse than an honest
`UNKNOWN`, because every routing decision depends on it.
See [ADR-005](docs/adr/ADR-005-usage-estimation.md).

### 3. Aggregation happens in SQL

`COUNT` / `SUM` / `AVG` / `GROUP BY` in the database. Never a Python loop that
sums rows, because a fetched page may be truncated and the total would be
silently wrong. Capped fetches select `limit + 1` so truncation is detectable.

### 4. Adapters never raise on provider failure

Return `AgentResult(success=False, error=...)`. An exception escaping an adapter
takes down a task graph that could have escalated to another agent instead.

### 5. Parallel agents never share a working tree

More than one ready task means every one of them gets its own git worktree.
See [ADR-004](docs/adr/ADR-004-git-worktree-isolation.md).

### 6. Secrets never reach a log, the database, or another agent

`redact_secrets()` runs over agent output before it is persisted or handed on,
and the JSON log formatter redacts any field whose name looks like a credential.

### 7. Tests never require a real account

Mock the CLI. Live tests are marked `@pytest.mark.provider` and excluded from the
default run by `addopts`. A test suite that needs someone's quota to pass is not
a test suite.

## Verify before you claim

This project exists because CLI behaviour is not what you assume. Two examples
that cost real debugging time:

- `agy -p --output-format json "x"` **fails** — Go flag parsing takes
  `--output-format` as the prompt. The correct form is
  `agy --output-format json --print=<prompt>`.
- `agy models` is a network call that intermittently returns empty. It is not an
  authentication failure, and reporting it as one sends the user down the wrong
  path.

So: run `--help`, run the real invocation, capture the real JSON, and put that
captured payload in `NATIVE_PAYLOADS` in `tests/test_adapter_contract.py`. Never
write a flag you have not executed.

## Definition of done

A change is not finished until all of these hold:

- [ ] Implemented
- [ ] Unit tested (contract-tested if it touches an adapter)
- [ ] `ruff check .` clean
- [ ] `mypy src/coderouter` clean
- [ ] `pytest` green
- [ ] Documented in `docs/`
- [ ] `CHANGELOG.md` updated
- [ ] `ARCHITECTURE.md` updated if a boundary moved
- [ ] ADR written if an architectural decision was made
- [ ] No other adapter broken

## Persistent memory

This repository carries `.engram/config.json` pinning the engram project to
`coderouter`. It is needed because this machine has many git repositories side by
side, and without it engram cannot tell which project a memory belongs to.

Save decisions, verified CLI behaviour and gotchas to the `coderouter` project — not
to whatever project cwd happens to resolve to.

## Commands

```bash
.venv/bin/pytest              # unit + contract tests, no network, no CLIs
.venv/bin/pytest -m provider  # real CLIs; costs real quota
.venv/bin/ruff check .
.venv/bin/mypy src/coderouter
router doctor                 # diagnose the local install
```

## Style

- Type hints everywhere; `from __future__ import annotations` at the top.
- `@dataclass(slots=True)` for domain types.
- Line length 100, ruff rules `E,F,I,B,UP,SIM`.
- Module docstrings answer *why the module exists*, not what the language does.
- Comments explain intent and constraints, never restate the code.
- No god modules. Past ~300 lines it is probably two responsibilities.
- Conventional Commits. No AI attribution in commit messages.

## Layout

```
src/coderouter/
    core/        orchestrator, domain models, task graph, doctor
    agents/      base interface + registry, one package per agent
    routing/     classifier, weighted scoring engine
    usage/       windows, forecasting, conservation mode
    context/     file selection, fingerprinting, summarisation
    validation/  stack detection and checks
    git/         worktree isolation
    security/    command policy, redaction
    storage/     SQLite
    tui/         Textual app and widgets
```

Adding an agent: [docs/extending/adding-agent.md](docs/extending/adding-agent.md).
It should touch exactly one new directory plus two lines of test parameters.
