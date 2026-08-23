## What and why

<!-- What changed, and what problem it solves. Link the issue or ADR. -->

## Invariants

Confirm none of these were broken (see [CLAUDE.md](../CLAUDE.md)):

- [ ] No provider branching outside `agents/<provider>/`
- [ ] No invented usage numbers; provenance is CONFIRMED / ESTIMATED / UNKNOWN
- [ ] Aggregation stays in SQL
- [ ] Adapters return `AgentResult(success=False)` instead of raising
- [ ] Parallel agents still get isolated worktrees
- [ ] No secret can reach a log, the database, or another agent
- [ ] Tests pass without a real provider account

## Definition of done

- [ ] Implemented
- [ ] Tested (contract tests if an adapter changed)
- [ ] `ruff check .` clean
- [ ] `mypy src/v4ld1` clean
- [ ] `pytest` green
- [ ] Docs updated
- [ ] `CHANGELOG.md` updated
- [ ] ADR added if this was an architectural decision

## Verified against a real CLI?

<!-- If this touches an adapter: which version, which invocation, and is the
     captured payload in NATIVE_PAYLOADS? Never a flag you have not executed. -->
