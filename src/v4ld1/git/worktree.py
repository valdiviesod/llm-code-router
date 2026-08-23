"""Git worktree isolation.

Two agents must never share a working tree. Each concurrent run gets its own
worktree under .worktrees/, and the caller merges deliberately afterwards.
"""

from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from ..errors import V4ld1Error


@dataclass(slots=True)
class Worktree:
    path: Path
    branch: str


async def _git(root: Path, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(root),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def is_git_repo(root: Path) -> bool:
    code, out, _ = await _git(root, "rev-parse", "--is-inside-work-tree")
    return code == 0 and out.strip() == "true"


class WorktreeManager:
    def __init__(self, root: Path):
        self.root = root
        self.base = root / ".worktrees"

    @asynccontextmanager
    async def isolated(self, task_id: str, agent_id: str):
        """Yield a private worktree, removing it on exit."""
        if not shutil.which("git") or not await is_git_repo(self.root):
            yield None
            return
        branch = f"v4ld1/{task_id}-{agent_id}"
        path = self.base / f"{task_id}-{agent_id}"
        self.base.mkdir(parents=True, exist_ok=True)
        code, _, err = await _git(self.root, "worktree", "add", "-b", branch, str(path))
        if code != 0:
            raise V4ld1Error(f"could not create worktree: {err.strip()}")
        try:
            yield Worktree(path, branch)
        finally:
            await _git(self.root, "worktree", "remove", "--force", str(path))

    async def diff_against_head(self, worktree: Worktree) -> str:
        _, out, _ = await _git(worktree.path, "diff", "HEAD")
        return out

    async def merge(self, worktree: Worktree) -> tuple[bool, str]:
        code, out, err = await _git(self.root, "merge", "--no-ff", worktree.branch)
        return code == 0, (err or out).strip()
