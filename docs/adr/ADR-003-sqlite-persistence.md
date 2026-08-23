# ADR-003 — SQLite for persistence

**Status** Accepted — 2026-08-22

## Context

The orchestrator must persist tasks, runs, routing decisions, usage events,
statistics, validations and an audit trail. It is a single-user, single-machine
Linux application. Postgres, Redis or a message broker would each mean a service
to install and run before the tool works at all.

## Decision

Plain `sqlite3` from the standard library, WAL mode, behind a narrow repository
class (`storage/db.py`). No ORM. All aggregation is expressed in SQL.

## Consequences

- Zero infrastructure: the tool works immediately after install.
- The schema avoids SQLite-only syntax, so a Postgres backend later means
  replacing the connection layer, not the callers.
- No ORM means explicit SQL, which is exactly what we want given aggregation is a
  correctness requirement: `COUNT`/`SUM`/`AVG` in the database, never a Python
  loop over a possibly-truncated page. Capped fetches select `limit + 1` so
  truncation is detectable rather than silent.
- Concurrent writers are limited. Acceptable for a single-user desktop tool;
  it is the constraint that would force the Postgres move.
