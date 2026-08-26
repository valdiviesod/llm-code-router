"""Memory kinds and storage helpers.

The router already has a `memory` SQLite table (project-scoped key-value
notes). This module gives the table a typed shape: every note is a
`MemoryKind` plus a key plus a value, with a stable enum so the
storage layer and the CLI/TUI speak the same vocabulary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from ..security.policy import redact_secrets
from ..storage.db import Database


class MemoryKind(str, Enum):
    CONVENTION = "convention"
    DECISION = "decision"
    GOTCHA = "gotcha"
    SUCCESS_PATTERN = "success_pattern"
    FAILURE_PATTERN = "failure_pattern"
    # A free-form note that does not fit the above kinds. The CLI uses
    # this when the user types `router memory add foo bar`.
    NOTE = "note"


@dataclass(slots=True)
class MemoryNote:
    kind: MemoryKind
    key: str
    value: str
    project_id: str
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> MemoryNote:
        return cls(
            kind=MemoryKind(row["kind"]),
            key=str(row["key"]),
            value=str(row["value"]),
            project_id=str(row["project_id"]),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )


class MemoryStore:
    """Thin wrapper over the existing `memory` table.

    A project is identified by the absolute path of its root; the
    table enforces uniqueness on `(project_id, kind, key)`. The
    `kind` filter scopes the queries; the injector and the CLI both
    use the same store, so the two views of the data stay aligned.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(
        self, project_id: str, kind: MemoryKind, key: str, value: str
    ) -> None:
        self._db.remember(project_id, kind.value, key, redact_secrets(value))

    def delete(self, project_id: str, kind: MemoryKind, key: str) -> bool:
        cur = self._db.conn.execute(
            "DELETE FROM memory WHERE project_id=? AND kind=? AND key=?",
            (project_id, kind.value, key),
        )
        self._db.conn.commit()
        return cur.rowcount > 0

    def list(
        self,
        project_id: str,
        *,
        kind: MemoryKind | None = None,
        limit: int = 100,
    ) -> list[MemoryNote]:
        if kind is None:
            rows = self._db.conn.execute(
                "SELECT * FROM memory WHERE project_id=? "
                "ORDER BY updated_at DESC LIMIT ?",
                (project_id, limit + 1),
            ).fetchall()
        else:
            rows = self._db.conn.execute(
                "SELECT * FROM memory WHERE project_id=? AND kind=? "
                "ORDER BY updated_at DESC LIMIT ?",
                (project_id, kind.value, limit + 1),
            ).fetchall()
        return [MemoryNote.from_row(r) for r in rows[:limit]]

    def get(
        self, project_id: str, kind: MemoryKind, key: str
    ) -> MemoryNote | None:
        row = self._db.conn.execute(
            "SELECT * FROM memory WHERE project_id=? AND kind=? AND key=?",
            (project_id, kind.value, key),
        ).fetchone()
        return MemoryNote.from_row(row) if row else None

    def as_dict(
        self, project_id: str, *, kind: MemoryKind | None = None
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for note in self.list(project_id, kind=kind):
            out[note.key] = note.value
        return out

    def to_jsonl(self, project_id: str) -> str:
        notes = self.list(project_id, limit=10_000)
        return "\n".join(
            json.dumps(
                {
                    "kind": n.kind.value,
                    "key": n.key,
                    "value": n.value,
                    "updated_at": n.updated_at.isoformat(),
                }
            )
            for n in notes
        )

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)
