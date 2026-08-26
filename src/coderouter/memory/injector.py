"""Memory injector.

Builds the "Project context" block that goes onto the task prompt
alongside the skills block. The injector pulls the most-recent
project notes up to a token budget, ordered by `updated_at` so the
freshest learning is most likely to be useful.

The injector is best-effort: a malformed note is skipped, a note
that would not fit the budget is dropped, and a missing project
id is fine (the block is just empty).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.models import Task
from .store import MemoryNote, MemoryStore


@dataclass(slots=True)
class MemoryBlock:
    block: str
    note_count: int


def _project_id_for(root: Path) -> str:
    return str(root.resolve())


def _approx_tokens(text: str) -> int:
    return max(len(text) // 4, 1)


class MemoryInjector:
    def __init__(self, store: MemoryStore, budget_tokens: int = 600) -> None:
        self._store = store
        self._budget = budget_tokens

    def build(self, task: Task, *, kinds: tuple | None = None) -> MemoryBlock:
        """Return a memory block under the configured token budget.

        `kinds` is an optional filter; passing `()` is equivalent to
        all kinds. The block is the most recent notes first, with a
        `[truncated]` marker when the budget cut some off.
        """
        project_id = _project_id_for(task.project_root)
        notes = self._store.list(project_id, limit=200)
        if kinds is not None:
            notes = [n for n in notes if n.kind in kinds]
        used = 0
        picked: list[MemoryNote] = []
        truncated = False
        for note in notes:
            cost = _approx_tokens(f"{note.kind.value}: {note.key}: {note.value}")
            if used + cost > self._budget:
                truncated = True
                break
            picked.append(note)
            used += cost
        if not picked:
            return MemoryBlock("", 0)
        sections = [
            f"- [{n.kind.value}] {n.key}: {n.value}" for n in picked
        ]
        body = "\n".join(sections)
        if truncated:
            body += "\n- …(more notes, omitted to fit the budget)"
        block = (
            "Project context (from coderouter memory):\n"
            f"{body}"
        )
        return MemoryBlock(block, len(picked))

    def render_into_prompt(self, prompt: str, block: MemoryBlock) -> str:
        if not block.block:
            return prompt
        return f"{prompt}\n\n{block.block}"

    @staticmethod
    def render_into_prompt_static(prompt: str, block: MemoryBlock) -> str:
        """The same join as `render_into_prompt`, exposed as a static for
        callers that already have a block in hand and no injector.
        """
        if not block.block:
            return prompt
        return f"{prompt}\n\n{block.block}"
