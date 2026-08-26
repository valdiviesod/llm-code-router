# ADR-007 — Isolated work is integrated, not discarded

**Status** Accepted — 2026-08-26

**Amends** [ADR-004](ADR-004-git-worktree-isolation.md)

## Context

ADR-004 established one git worktree per concurrent run. It specified creation
and removal, and said nothing about how the work gets back.

The implementation took that literally. `isolated()` created the worktree, the
agent wrote into it, and the context manager's `finally` ran
`git worktree remove --force` — which discards uncommitted changes. `merge()`
existed but had no callers anywhere in the codebase.

So isolation worked perfectly and every isolated run produced nothing. In
practice that meant:

- `run_graph` isolates whenever more than one task is ready, so parallel
  execution never changed a file.
- Speculative dispatch always isolates, so the winner of the race won nothing.
- The per-attempt branch `coderouter/<task>-<agent>` was never deleted, leaking
  one branch per run forever.
- Validation ran `git diff` against the *project root* rather than the worktree,
  so it always saw an unchanged tree and silently skipped every check.

The tests passed throughout, because they asserted that a worktree was created
and then removed — which is exactly what the bug did.

## Decision

Isolation is half a contract. The full lifecycle is explicit:

```
isolated() -> agent writes -> validate -> integrate or discard -> cleanup
```

1. **Validate inside the worktree, before merging.** The attempt is what is
   being judged. Only work that passes its own checks is integrated.
2. **`integrate()` commits inside the worktree and merges the branch back.**
   A conflict aborts the merge, leaving the project tree exactly as it was, and
   is reported as `conflicted` rather than as a generic failure.
3. **A conflicted branch is kept.** That branch is the only copy of the work.
   Deleting it to keep the repository tidy would be the same class of bug.
4. **A dirty project tree refuses the merge.** Landing a merge on top of a
   human's in-progress edits is not ours to do.
5. **`cleanup()` removes the worktree and deletes its branch**, unless the
   branch was explicitly kept.
6. **Work that could not be merged marks the task failed**, however well the
   agent reported it going. A run that succeeded in a worktree nobody merged
   did not happen.

`.worktrees/` is excluded from the dirty check, because it lives inside the
repository and a project that does not gitignore it would otherwise read as
permanently dirty and never accept a merge.

Escalation carries `isolate` through to the retry. It previously did not, so a
retry after a failed isolated run wrote straight into the shared project tree —
putting two agents in one working tree, which is the thing ADR-004 forbids.

## Consequences

- The `runs` table records `integrated` and `integration_detail`, so history can
  distinguish work that landed from work that merely succeeded somewhere that
  was then thrown away.
- `router doctor` probes worktree creation for real and reports leftover
  worktrees and unmerged `coderouter/*` branches.
- A repository with no commits cannot be branched from, so isolation degrades to
  running in place with a warning rather than raising.
- Conflicts are now a visible, recoverable state that a human resolves. That is
  a deliberate trade: silent auto-resolution would be worse than a branch
  someone has to look at.

## Alternatives rejected

**Rebase instead of merge.** Rewrites the attempt's history and makes a conflict
harder to reason about after the fact. `--no-ff` keeps each attempt as an
identifiable unit in the history.

**Auto-resolve conflicts by preferring the agent's side.** Silently overwrites
human work. The whole point of detecting the conflict is to not do that.

**Copy files out of the worktree instead of merging.** Loses the three-way
merge, so a file the human also changed would be clobbered without anyone
noticing.
