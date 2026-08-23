# ADR-001 — Use Textual for the TUI

**Status** Accepted — 2026-08-22

## Context

The TUI is a core part of the product, not a wrapper. It needs live panels,
tabs, async updates while agents run, and it must look professional in a
terminal. The orchestrator itself is Python and asyncio-based.

Alternatives: raw `curses` (no layout, no async story, enormous effort);
Rich alone (renders well, no interaction model); a Go or Rust TUI in a separate
process (a second language and an IPC boundary for no gain).

## Decision

Python + Textual, with Rich renderables inside custom widgets.

## Consequences

- The TUI shares the orchestrator's event loop; no IPC, no serialisation layer.
- Widgets are declarative and CSS-styled (`tui/styles.tcss`), so visual changes
  do not touch logic.
- `App.run_test()` makes the TUI genuinely testable in the normal suite.
- Textual is a real dependency with its own release cadence.
- Widgets must stay presentation-only. Any widget that computes business logic is
  a bug, because the CLI would then behave differently from the TUI.
