# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/1.1.0/).

## [Unreleased]

### Added
- TUI text copying. `Ctrl+C` copies the mouse selection when there is one
  (previously the priority cancel/quit binding shadowed Textual's copy action,
  so nothing in the app could be copied), and still cancels or quits otherwise.
  `Ctrl+Y` copies the last agent reply and `Ctrl+S` the whole session, both from
  the raw agent output rather than the wrapped, styled render.

### Changed
- TUI reworked. Two registered Textual themes, `gruvbox-dark` (default) and
  `v4ld1-pastel`, cycled with `Ctrl+T`; each ships a matching `Palette` of hex
  values because Rich renderables cannot read Textual theme variables.
  `styles.tcss` now carries layout only and takes every colour from theme
  variables. The dashboard gained a status line (mode, run state,
  project), an agent-health table replacing the hand-joined string, per-metric
  emphasis, and a `Ctrl+L` clear binding. Usage, metrics and health refresh on
  an interval instead of only on `F5`. Documented in
  `docs/architecture/tui.md`.
- The console command is now `router` (was `v4ld1`). The Python package, config
  path and data directory keep the `v4ld1` name; only the entry point changed.

### Added
- Project harness: `CLAUDE.md` (rules and invariants), `.claude/settings.json`
  (permission allowlist), GitHub Actions CI on Python 3.11 and 3.12, a pull
  request template carrying the invariant checklist, `.editorconfig`, and
  `.engram/config.json` pinning the persistent-memory project name.
- Complexity-based model tiering (`agents.<id>.model_tiers`): the router now
  picks the cheapest model that still matches the task, instead of one fixed
  model per agent. QUALITY and MAXIMUM always take the top tier. Measured: the
  same trivial task cost $0.48 on opus and $0.045 on haiku.

### Fixed
- The TUI no longer appears to hang or crash on submit. Three separate causes:
  log records reaching stderr and painting over the app (root-logger stream
  handlers are now detached while the TUI runs, and a `NullHandler` keeps
  `logging.lastResort` from stepping in); no progress feedback during a dispatch
  that `agents.timeout_s` allows to run for 30 minutes (the status line now
  ticks with elapsed time); and blocking classification/context selection
  running on the event loop, which froze even the key bindings (`analyze` now
  runs via `asyncio.to_thread`).
- `Ctrl+C` works again. Textual unbinds it by default, leaving only a
  "press ctrl+q" notification; it is now a priority binding that cancels the
  running task graph, or quits when nothing is running. `Esc` also cancels.
- A cancelled or failed worker releases the prompt instead of leaving the app
  permanently busy.
- Agent output is written to the stream pane as `rich.text.Text` instead of
  markup, so a `[` in a diff or log line can no longer break rendering or inject
  styling.
- Orchestrator event payloads pass through `redact_secrets` before reaching the
  log pane.
- The prompt is disabled and the run worker is exclusive while a task graph is
  running; submitting again mid-run used to start an overlapping graph that
  interleaved output and spent quota the router had budgeted once.
- A failing `health_check` no longer blanks the Agents panel — the adapter is
  listed as unhealthy with the error.
- `agy models` is a network call that can return empty transiently; the adapter
  now retries once, and `router doctor` no longer reports a network blip as an
  authentication failure.

## [0.1.0] — 2026-08-22

### Added
- Orchestrator core: classify → plan → route → execute → validate → learn.
- `AgentAdapter` interface with registry-based discovery and entry-point plugins.
- `ClaudeCodeAdapter`, verified against Claude Code 2.1.239
  (`claude -p --output-format json`).
- `AntigravityAdapter`, verified against agy 1.1.17
  (`agy --output-format json --print=<prompt>`).
- `RoutingEngine` with weighted scoring, five routing modes, quota veto, safe
  reserves and user override.
- `UsageManager`: rolling-window token accounting with explicit
  CONFIRMED/ESTIMATED/UNKNOWN provenance and pre-flight forecasting.
- `ContextManager`: relevance-ranked file selection, fingerprinting, output
  summarisation.
- `TaskGraph`: dependency-ordered decomposition with cycle detection.
- `ValidationEngine`: stack detection for Python, Node, Go and Rust.
- `WorktreeManager`: git worktree isolation for parallel agents.
- Security: command policy (SAFE/ASK/BLOCK), secret redaction, audit log.
- SQLite persistence with all aggregation done in SQL.
- Textual TUI: dashboard, routing view, agents, logs.
- CLI: run, `doctor`, `agents`, `usage`, `status`, `config`.
- 109 unit tests plus provider contract tests; live-CLI tests behind `-m provider`.
