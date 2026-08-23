# Troubleshooting

Start with `router doctor`. It checks Python, git, adapter registration, each
CLI's health and model list, the config file, the database, the project, and
whether usage limits are configured — and prints a fix for anything failing.

## "no agent binary found on PATH"

The configured `command` is not installed or not on PATH. Check
`agents.<id>.command` against `which claude` / `which agy`.

## "every agent is over its configured quota threshold"

Working as designed: the forecast said the run would breach your threshold or
reserve. Options: wait for the window to roll over, raise
`routing.safe_threshold_percent`, lower `reserve_percent`, or force the run with
`--agent`.

## Usage always shows UNKNOWN

No limits configured. Set `agents.<id>.window_limit_tokens`. Neither CLI reports
subscription quota, so coderouter cannot discover it for you —
see [ADR-005](../adr/ADR-005-usage-estimation.md).

## An agent fails, then a different one succeeds

That is escalation. The second attempt receives a compact `HandoffPackage`
describing the first failure. `routing.max_attempts` caps it.

## Validation is not running

It only runs when the tree actually changed *and* the stack is detected. A
missing tool is reported as skipped, not as a pass.

## Worktrees left behind

Isolation cleans up on exit, but a hard kill can orphan one:

```bash
git worktree list
git worktree remove --force .worktrees/<name>
git worktree prune
```

## The TUI looks wrong

Textual needs a truecolor-capable terminal. Try `TERM=xterm-256color`. Check
your terminal is at least 80×24.

## Where is the state?

Database and logs in `~/.local/share/coderouter/` (or `$CODEROUTER_DATA_DIR`). Deleting the
database loses history and routing statistics; it does not break anything else.
