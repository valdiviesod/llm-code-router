# ADR-004 — Git worktrees for parallel agents

**Status** Accepted — 2026-08-22

## Context

When two agents work on one task graph at the same time, they will edit files.
Sharing a working tree means interleaved writes, corrupted diffs, and results that
cannot be attributed to either agent. Copying the whole project per agent is slow
and loses git history. Container isolation is heavy and adds a runtime dependency.

## Decision

`git worktree add -b coderouter/<task>-<agent> .worktrees/<task>-<agent>` per
concurrent run, removed on exit. A single ready task runs in place, because there
is nobody to collide with. Non-git projects yield `None` and run in place with
isolation disabled.

## Consequences

- Each agent's work is a real branch: diffable, reviewable, mergeable, discardable.
- Cheap — worktrees share the object database.
- Only works in a git repository. `router doctor` says so plainly rather than
  pretending isolation is active.
- Orphaned worktrees are possible after a hard kill; `git worktree prune` cleans
  up, and troubleshooting documents it.
- Merging is deliberate and sequential, never automatic on failure.
