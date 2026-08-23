# Development setup

```bash
git clone <repo> && cd coderouter
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/router doctor
```

Requires Python 3.11+ and git. Claude Code and/or the Antigravity CLI are needed
only to run real tasks — the unit suite mocks them.

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

## Project harness

| File | Purpose |
|---|---|
| `CLAUDE.md` | The project's rules and invariants, loaded automatically by Claude Code. |
| `.claude/settings.json` | Permission allowlist: test/lint/typecheck run unprompted, pushes and installs ask, provider tests and `.env` reads are denied. |
| `.github/workflows/ci.yml` | Lint, type check and test on Python 3.11 and 3.12, plus a check that the `router` command installs. |
| `.github/pull_request_template.md` | The invariant checklist and definition of done. |
| `.editorconfig` | Indentation and line endings. |
| `.engram/config.json` | Pins the engram memory project to `coderouter`, so memories are not filed under whichever sibling repository cwd resolves to. |

CI deliberately never runs `-m provider`: it has no agent CLIs, and a pipeline
that depends on someone's quota to go green is not a pipeline.

## Useful environment variables

| Variable | Effect |
|---|---|
| `CODEROUTER_CONFIG` | Path to config.yaml |
| `CODEROUTER_DATA_DIR` | Database and logs location |
