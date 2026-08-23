"""Context selection, fingerprinting and token optimisation.

The cheapest token is the one never sent. This module decides which project
files an agent actually needs, truncates them, and fingerprints the selection so
an identical context can be recognised and reused instead of re-analysed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import TokenSavingConfig
from ..core.models import Task

IGNORED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", ".next", ".worktrees",
}
CODE_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php",
    ".c", ".h", ".cpp", ".cs", ".sql", ".sh", ".yaml", ".yml", ".toml", ".md",
}


@dataclass(slots=True)
class ContextBundle:
    files: list[Path] = field(default_factory=list)
    fingerprint: str = ""
    approx_tokens: int = 0
    truncated: bool = False


def _tokenish(text: str) -> int:
    return max(len(text) // 4, 1)


class ContextManager:
    def __init__(self, config: TokenSavingConfig):
        self.config = config

    def keywords(self, prompt: str) -> set[str]:
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", prompt.lower())
        stop = {"the", "and", "for", "with", "this", "that", "add", "fix", "make",
                "implement", "should", "into", "from", "using", "please"}
        return {w for w in words if w not in stop}

    def select(self, task: Task) -> ContextBundle:
        """Rank candidate files by name/content relevance and keep the top N."""
        if not self.config.enabled:
            return ContextBundle()
        root = task.project_root
        keys = self.keywords(task.prompt)
        scored: list[tuple[float, Path]] = []
        for path in self._walk(root):
            score = self._score(path, root, keys)
            if score > 0:
                scored.append((score, path))
        scored.sort(key=lambda item: item[0], reverse=True)
        limit = self.config.max_context_files // (2 if self.config.aggressive else 1)
        chosen = [p for _, p in scored[:limit]]
        approx = 0
        for path in chosen:
            try:
                approx += _tokenish(path.read_text(errors="ignore")[: self.config.max_file_bytes])
            except OSError:
                continue
        return ContextBundle(
            files=chosen,
            fingerprint=self.fingerprint(chosen),
            approx_tokens=approx,
            truncated=len(scored) > len(chosen),
        )

    def _walk(self, root: Path):
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in CODE_SUFFIXES:
                continue
            if any(part in IGNORED_DIRS for part in path.parts):
                continue
            yield path

    def _score(self, path: Path, root: Path, keys: set[str]) -> float:
        name = path.stem.lower()
        score = 0.0
        for key in keys:
            if key in name:
                score += 3.0
            elif key in str(path.relative_to(root)).lower():
                score += 1.0
        if score == 0:
            return 0.0
        try:
            head = path.read_text(errors="ignore")[:4000].lower()
        except OSError:
            return score
        score += sum(0.5 for key in keys if key in head)
        return score

    def fingerprint(self, files: list[Path]) -> str:
        """Stable hash of (relative path, size, mtime) for the selected set."""
        digest = hashlib.sha256()
        for path in sorted(files):
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(f"{path}:{stat.st_size}:{int(stat.st_mtime)}".encode())
        return digest.hexdigest()[:32]

    def summarize_output(self, text: str, max_chars: int = 1500) -> str:
        """Compress an agent's prose for handoff. Keeps head and tail, which is
        where objectives and conclusions live."""
        text = text.strip()
        if len(text) <= max_chars:
            return text
        head = text[: max_chars // 2]
        tail = text[-max_chars // 2:]
        return f"{head}\n...[{len(text) - max_chars} chars elided]...\n{tail}"
