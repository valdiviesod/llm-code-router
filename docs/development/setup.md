# Development setup

```bash
git clone <repo> && cd v4ld1
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/router doctor
```

Requires Python 3.11+ and git. Claude Code and/or the Antigravity CLI are needed
only to run real tasks — the unit suite mocks them.

## Layout

```
src/v4ld1/
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

## Useful environment variables

| Variable | Effect |
|---|---|
| `V4LD1_CONFIG` | Path to config.yaml |
| `V4LD1_DATA_DIR` | Database and logs location |
