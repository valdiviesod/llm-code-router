# Conventions

The authoritative list lives in [CLAUDE.md](../../CLAUDE.md) at the repository
root, which Claude Code loads automatically. This page is the human-facing copy.

## Hard rules

1. **No provider branching outside `agents/<provider>/`.** Express differences as
   `Capability` values.
2. **No invented numbers.** Usage is CONFIRMED, ESTIMATED or UNKNOWN, and the UI
   always says which.
3. **Aggregation in SQL.** `COUNT`/`SUM`/`AVG`, never a Python loop over rows.
   Capped fetches use `limit + 1` so truncation is detectable.
4. **Adapters never raise on provider failure.** Return `AgentResult(success=False)`.
5. **Typed errors.** Everything derives from `V4ld1Error`.

## Style

- Type hints everywhere; `from __future__ import annotations` at the top.
- `@dataclass(slots=True)` for domain types.
- Line length 100, enforced by ruff (`E,F,I,B,UP,SIM`).
- Module docstrings answer *why the module exists*, not what the language does.
- Comments explain intent and constraints, never restate the code.
- No god modules. If a file passes ~300 lines, it is probably two responsibilities.

## Naming

- Adapter ids are lowercase, stable, and double as config keys and DB keys.
- Enum values are the strings that get persisted — changing one is a migration.
