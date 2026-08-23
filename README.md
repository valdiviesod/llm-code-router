# v4ld1 Code TUI

An AI coding orchestrator for Linux. It sits between you and your coding agents,
decides which one should do the work, splits the work when that helps, validates
the result, and keeps track of what every run cost.

Today it drives **Claude Code** and the **Antigravity CLI**. Adding a third agent
means writing one adapter — no changes to the router, scheduler, TUI or database.

```
$ v4ld1 "implement JWT authentication"

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
.venv/bin/v4ld1 config --init
.venv/bin/v4ld1 doctor
```

`doctor` tells you exactly what is missing and how to fix it. It never guesses.

## Use

```bash
v4ld1                       # open the TUI
v4ld1 "fix the login bug"   # run one task
v4ld1 --mode economy "..."  # force a routing mode
v4ld1 --agent claude "..."  # force an agent
v4ld1 status                # metrics
v4ld1 agents                # health and capabilities
v4ld1 usage                 # usage windows
v4ld1 doctor                # diagnostics
v4ld1 config --init         # write a default config
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

## Status

Supported agents: Claude Code, Antigravity.
Planned via the same adapter interface: Codex, Gemini, Qwen, DeepSeek, OpenAI API,
Anthropic API, Ollama, OpenRouter, local models.
