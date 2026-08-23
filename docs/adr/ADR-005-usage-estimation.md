# ADR-005 — Usage estimation strategy

**Status** Accepted — 2026-08-22

## Context

Routing depends on knowing how much quota is left. We verified what the CLIs
actually expose:

- **Claude Code 2.1.239** — `-p --output-format json` returns per-run
  `usage` (input, output, cache read, cache creation) and `total_cost_usd`.
  There is no command that reports subscription quota or window state.
- **Antigravity 1.1.17** — `--output-format json --print=` returns per-run
  `usage` (input, output, thinking, cache read, total). No quota command either.

Plan limits also differ per user and change over time, so hardcoding
"Claude = N tokens" would be wrong the day it was written.

## Decision

1. Limits are **configuration**, never constants in the code. Absent config means
   absent knowledge.
2. v4ld1 accounts for the tokens *it* spent, per agent, in a rolling window, by
   `SUM` over the `usage_events` table.
3. Every usage figure carries provenance: `CONFIRMED` (provider said so),
   `ESTIMATED` (our accounting against a configured limit), or `UNKNOWN` (no
   limit configured). The CLI and the TUI always display the tag.
4. `get_usage()` on the adapter takes precedence. If a provider ever ships a real
   quota API, that adapter returns `CONFIRMED` and estimation steps aside.

## Consequences

- The user is never shown a fabricated percentage.
- Estimates only count usage that went *through v4ld1*; work done directly in
  Claude Code or agy is invisible. This is documented, and it is why the default
  is UNKNOWN rather than a confident-looking zero.
- Forecasting, reserves and conservation mode all require the user to set limits.
  `v4ld1 doctor` flags unset limits as an actionable finding rather than a silent
  degradation.
