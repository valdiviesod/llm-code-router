# v4ld1 Code Router

An AI coding orchestrator for Linux. It sits between you and your coding agents,
decides which one should do the work, splits the work when that helps, validates
the result, and keeps track of what every run cost.

Today it drives **Claude Code** and the **Antigravity CLI**. Adding a third agent
means writing one adapter — no changes to the router, scheduler, TUI or database.

```
$ router "implement JWT authentication"

complexity=high risk=medium type=security context_files=7
decomposed into 4 subtasks
[routed] architecture → claude (deep reasoning required, quota pressure low)
[routed] implementation → antigravity
[validated] ruff: pass  pytest: pass
```

## Install

```bash
git clone <repo> && cd v4ld1
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/router config --init
.venv/bin/router doctor
```

`doctor` tells you exactly what is missing and how to fix it. It never guesses.

## Use

```bash
router                        # open the TUI
router "fix the login bug"    # run one task
router --mode economy "..."   # force a routing mode
router --agent claude "..."   # force an agent
router status                 # metrics
router agents                 # health and capabilities
router usage                  # usage windows
router doctor                 # diagnostics
router config --init          # write a default config
```

## The point

The goal is not "always use the best agent". It is **the most quality per unit of
quota**. v4ld1 picks the cheapest agent that is actually capable of the task, and
escalates only when the task earns it.

Because neither CLI exposes subscription quota, v4ld1 accounts for the tokens it
spends itself and compares them against limits *you* configure. Any number
derived that way is labelled `ESTIMATED`; nothing is ever presented as a
confirmed provider figure unless the provider confirmed it. See
[ADR-005](docs/adr/ADR-005-usage-estimation.md).

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — the whole system in one page
- [docs/architecture/](docs/architecture/) — per-component design and boundaries
- [docs/extending/adding-agent.md](docs/extending/adding-agent.md) — add an agent
- [docs/operations/](docs/operations/) — install, configure, troubleshoot
- [docs/adr/](docs/adr/) — why things are the way they are
- [CLAUDE.md](CLAUDE.md) — project rules and the invariants that must not break

## Status

Supported agents: Claude Code, Antigravity.
Planned via the same adapter interface: Codex, Gemini, Qwen, DeepSeek, OpenAI API,
Anthropic API, Ollama, OpenRouter, local models.
