# Git worktrees

**Why** Two agents must never share a working tree. See
[ADR-004](../adr/ADR-004-git-worktree-isolation.md) for the isolation decision and
[ADR-007](../adr/ADR-007-worktree-integration.md) for how the work gets back.

## Lifecycle

```
isolated() -> agent writes -> validate -> integrate | discard -> cleanup
```

| Step | What happens |
|---|---|
| `isolated()` | `git worktree add -b coderouter/<task>-<agent> .worktrees/<task>-<agent>` |
| validate | Checks run **inside the worktree**, before any merge |
| `integrate()` | Commit in the worktree, then `merge --no-ff` into the project |
| discard | A failed attempt is never merged |
| `cleanup()` | Remove the worktree and delete its branch |

Isolation is skipped — `isolated()` yields `None` and the run happens in place —
when git is missing, the directory is not a repository, or the repository has no
commit to branch from. It is never skipped silently when a worktree *could* have
been created.

## Integration outcomes

`IntegrationResult` distinguishes four states, because "it did not merge" is not
one thing:

| State | Meaning |
|---|---|
| `merged=True` | The work is in the project tree |
| `had_changes=False` | The agent changed no files. Not an error |
| `conflicted=True` | The merge was attempted, git refused, `merge --abort` ran, the project tree is untouched, and the branch is **kept** |
| `merged=False` | Everything else, with `detail` explaining it |

A conflicted branch is the only copy of that attempt's work. It is kept
deliberately; `router doctor` lists leftover `coderouter/*` branches so they do
not accumulate unnoticed.

A dirty project tree refuses the merge rather than landing on top of a human's
in-progress edits. `.worktrees/` is excluded from that dirty check, since it
lives inside the repository.

## Consequences for the rest of the system

- Work that could not be merged marks the task **failed**, however well the
  agent reported it going.
- The `runs` table records `integrated` and `integration_detail`. `router
  history` shows `landed` or `stranded`; `router explain <task-id>` shows why.
- Escalation carries `isolate` through to the retry — otherwise the retry would
  write into the shared tree and put two agents in one working tree.

**Must not** Remove a worktree that holds unmerged work without keeping its
branch. Auto-resolve a conflict. Merge onto a dirty project tree.
