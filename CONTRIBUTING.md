# Contributing

## Definition of done

A change is not finished until all of these hold:

- [ ] Implemented
- [ ] Unit tested (and contract-tested if it touches an adapter)
- [ ] `ruff check .` clean
- [ ] `mypy src/v4ld1` clean
- [ ] `pytest` green
- [ ] Documented in `docs/`
- [ ] `CHANGELOG.md` updated
- [ ] `ARCHITECTURE.md` updated if a boundary moved
- [ ] An ADR added if an architectural decision was made
- [ ] No other adapter broken

## Workflow

```bash
.venv/bin/pytest              # unit tests, no network, no provider CLIs
.venv/bin/pytest -m provider  # real CLIs; costs real quota
.venv/bin/ruff check .
.venv/bin/mypy src/v4ld1
```

## Rules that are not negotiable

1. **No provider branching in the core.** If you find yourself writing
   `if agent_id == ...` outside `agents/<that agent>/`, add a `Capability` instead.
2. **Never invent usage numbers.** If a provider does not report it, it is
   `ESTIMATED` or `UNKNOWN`, and the UI says so.
3. **Aggregation happens in SQL.** No summing rows in Python.
4. **Tests never require a real account.** Mock the CLI; mark live tests
   `@pytest.mark.provider`.
5. **Secrets never reach a log, the database, or another agent.**
