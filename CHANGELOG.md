# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/1.1.0/).

## [Unreleased]

### Changed
- The console command is now `router` (was `v4ld1`). The Python package, config
  path and data directory keep the `v4ld1` name; only the entry point changed.

### Added
- Project harness: `CLAUDE.md` (rules and invariants), `.claude/settings.json`
  (permission allowlist), GitHub Actions CI on Python 3.11 and 3.12, a pull
  request template carrying the invariant checklist, and `.editorconfig`.
- Complexity-based model tiering (`agents.<id>.model_tiers`): the router now
  picks the cheapest model that still matches the task, instead of one fixed
  model per agent. QUALITY and MAXIMUM always take the top tier. Measured: the
  same trivial task cost $0.48 on opus and $0.045 on haiku.

### Fixed
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
