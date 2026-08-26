"""Git worktree isolation and integration.

Two agents must never share a working tree (ADR-004), so every concurrent run
gets its own worktree under `.worktrees/`.

Isolation on its own is only half the contract. A worktree that is created,
written into, and then removed has *destroyed* the work — which is what this
module used to do, because `worktree remove --force` discards uncommitted
changes and nothing ever called `merge`. The lifecycle is therefore explicit:

    isolated() -> agent writes -> integrate() or discard() -> cleanup

`integrate` commits inside the worktree and merges the branch back, reporting a
conflict rather than leaving the repository half-merged. Cleanup removes both
the worktree *and* its branch; a branch per attempt, kept forever, is a leak.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..logging import get_logger

logger = get_logger("git.worktree")


@dataclass(slots=True)
class Worktree:
    path: Path
    branch: str


@dataclass(slots=True)
class IntegrationResult:
    """What happened when a worktree's branch was merged back.

    `conflicted` is distinct from a plain failure: the merge was attempted,
    git refused, and the root tree has been restored with `merge --abort`.
    The branch still holds the work, so the caller can retry or hand it to a
    human — `branch` names it.
    """

    merged: bool
    detail: str
    conflicted: bool = False
    had_changes: bool = True
    branch: str | None = None
    files: list[str] = field(default_factory=list)


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


async def has_commits(root: Path) -> bool:
    """True when HEAD resolves. `git worktree add` needs a commit to branch from."""
    code, _, _ = await _git(root, "rev-parse", "--verify", "HEAD")
    return code == 0


class WorktreeManager:
    def __init__(self, root: Path):
        self.root = root
        self.base = root / ".worktrees"
        # Branches whose work must outlive cleanup — a conflicted merge is
        # still the only copy of that attempt's output.
        self._keep: set[str] = set()

    def keep_branch(self, worktree: Worktree) -> None:
        """Preserve this attempt's branch through `cleanup`."""
        self._keep.add(worktree.branch)

    # --- lifecycle ------------------------------------------------------

    @asynccontextmanager
    async def isolated(self, task_id: str, agent_id: str) -> AsyncIterator[Worktree | None]:
        """Yield a private worktree, or None when isolation is not possible.

        None means "work in place": the caller is responsible for knowing that
        parallel siblings must not be given None at the same time. Isolation is
        skipped when git is missing, the directory is not a repository, or the
        repository has no commit to branch from — never silently when a
        worktree *could* have been made.

        The worktree and its branch are both removed on exit. Call
        `integrate` before leaving the block if the work should survive.
        """
        if not shutil.which("git") or not await is_git_repo(self.root):
            yield None
            return
        if not await has_commits(self.root):
            logger.warning(
                "worktree isolation skipped for %s: %s has no commits to branch from",
                task_id, self.root,
            )
            yield None
            return

        branch = f"coderouter/{task_id}-{agent_id}"
        path = self.base / f"{task_id}-{agent_id}"
        self.base.mkdir(parents=True, exist_ok=True)
        code, _, err = await _git(self.root, "worktree", "add", "-b", branch, str(path))
        if code != 0:
            logger.warning("could not create worktree for %s: %s", task_id, err.strip())
            yield None
            return
        wt = Worktree(path, branch)
        try:
            yield wt
        finally:
            await self.cleanup(wt)

    async def cleanup(self, worktree: Worktree) -> None:
        """Remove the worktree and delete its branch.

        Both are best-effort: cleanup runs in a `finally`, and failing to tidy
        up must never mask the error that got us here.
        """
        code, _, err = await _git(self.root, "worktree", "remove", "--force",
                                  str(worktree.path))
        if code != 0:
            logger.warning("worktree remove failed for %s: %s",
                           worktree.path, err.strip())
        if worktree.branch in self._keep:
            logger.info("keeping branch %s: it holds unmerged work", worktree.branch)
            return
        # -D, not -d: an un-merged attempt branch is exactly what we discard.
        code, _, err = await _git(self.root, "branch", "-D", worktree.branch)
        if code != 0:
            logger.debug("branch delete failed for %s: %s", worktree.branch, err.strip())

    # --- inspection -----------------------------------------------------

    async def _status(self, where: Path) -> list[str]:
        """Porcelain status paths, minus our own scratch directory.

        `.worktrees/` lives inside the repository, so a project that does not
        gitignore it would otherwise read as permanently dirty and every merge
        would be refused.
        """
        code, out, _ = await _git(where, "status", "--porcelain")
        if code != 0:
            return []
        files = []
        for line in out.splitlines():
            if len(line) <= 3:
                continue
            path = line[3:].strip().strip('"')
            # Rename entries are "old -> new"; the new path is what changed.
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path == ".worktrees" or path.startswith(".worktrees/"):
                continue
            files.append(path)
        return files

    async def changed_files(self, worktree: Worktree) -> list[str]:
        """Paths the agent touched, staged or not, including untracked."""
        return await self._status(worktree.path)

    async def has_changes(self, worktree: Worktree) -> bool:
        return bool(await self.changed_files(worktree))

    async def root_is_dirty(self) -> bool:
        """True when the project tree has changes a merge would land on top of."""
        return bool(await self._status(self.root))

    async def diff_against_head(self, worktree: Worktree) -> str:
        _, out, _ = await _git(worktree.path, "diff", "HEAD")
        return out

    # --- integration ----------------------------------------------------

    async def commit_all(self, worktree: Worktree, message: str) -> bool:
        """Stage everything in the worktree and commit it.

        Returns False when there was nothing to commit — not an error, just an
        agent that changed no files.
        """
        code, _, err = await _git(worktree.path, "add", "-A")
        if code != 0:
            logger.warning("git add failed in %s: %s", worktree.path, err.strip())
            return False
        code, _, err = await _git(
            worktree.path, "-c", "user.name=coderouter",
            "-c", "user.email=coderouter@localhost",
            "commit", "--no-verify", "-m", message,
        )
        if code != 0:
            # "nothing to commit" is the expected no-op path.
            logger.debug("no commit made in %s: %s", worktree.path, err.strip())
            return False
        return True

    async def integrate(self, worktree: Worktree, message: str) -> IntegrationResult:
        """Commit the worktree's changes and merge them into the root branch.

        On conflict the merge is aborted so the root tree is left exactly as it
        was, and `conflicted` is set. The attempt branch is named in the result
        so the work can still be recovered — the caller must skip `cleanup`
        for a conflicted result if it wants to keep it.
        """
        files = await self.changed_files(worktree)
        if not files:
            return IntegrationResult(merged=False, detail="agent changed no files",
                                     had_changes=False, branch=worktree.branch)

        if not await self.commit_all(worktree, message):
            return IntegrationResult(merged=False, detail="nothing could be committed",
                                     had_changes=False, branch=worktree.branch,
                                     files=files)

        # A dirty root cannot take a merge cleanly; say so instead of
        # half-merging on top of the user's in-progress edits.
        if await self.root_is_dirty():
            return IntegrationResult(
                merged=False, conflicted=True, branch=worktree.branch, files=files,
                detail=("the project tree has uncommitted changes; "
                        f"merge {worktree.branch} by hand"),
            )

        code, out, err = await _git(self.root, "merge", "--no-ff", "-m", message,
                                    worktree.branch)
        if code == 0:
            return IntegrationResult(merged=True, detail=(out or "merged").strip(),
                                     branch=worktree.branch, files=files)

        await _git(self.root, "merge", "--abort")
        return IntegrationResult(
            merged=False, conflicted=True, branch=worktree.branch, files=files,
            detail=(err or out).strip() or "merge conflict",
        )

    # Kept for callers that only want the raw merge.
    async def merge(self, worktree: Worktree) -> tuple[bool, str]:
        code, out, err = await _git(self.root, "merge", "--no-ff", worktree.branch)
        return code == 0, (err or out).strip()
