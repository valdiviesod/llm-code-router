"""Stack-aware testing engine.

Runs only the checks the detected project stack actually supports. A missing
tool is skipped, not failed: reporting a fake lint pass would be worse than
reporting nothing. This module owns the *what to run*; `validation.engine` is
the public surface that orchestrates testing + (optional) review.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..tools.output_bounds import bound_output


@dataclass(slots=True)
class CheckResult:
    name: str
    passed: bool
    skipped: bool = False
    detail: str = ""


@dataclass(slots=True)
class Stack:
    name: str
    markers: tuple[str, ...]
    checks: tuple[tuple[str, tuple[str, ...]], ...]


STACKS: tuple[Stack, ...] = (
    Stack("python", ("pyproject.toml", "setup.py", "requirements.txt"), (
        ("ruff", ("ruff", "check", ".")),
        ("mypy", ("mypy", ".")),
        ("pytest", ("pytest", "-q")),
    )),
    Stack("node", ("package.json",), (
        ("eslint", ("npx", "--no-install", "eslint", ".")),
        ("tsc", ("npx", "--no-install", "tsc", "--noEmit")),
        ("npm test", ("npm", "test", "--silent")),
    )),
    Stack("go", ("go.mod",), (
        ("go vet", ("go", "vet", "./...")),
        ("go test", ("go", "test", "./...")),
    )),
    Stack("rust", ("Cargo.toml",), (
        ("cargo check", ("cargo", "check")),
        ("cargo test", ("cargo", "test")),
    )),
)


def detect_stacks(root: Path) -> list[Stack]:
    return [s for s in STACKS if any((root / m).exists() for m in s.markers)]


class TestingEngine:
    def __init__(self, timeout_s: int = 600):
        self.timeout_s = timeout_s

    async def git_diff(self, root: Path) -> tuple[str, list[str]]:
        """Authoritative list of changed files, straight from git."""
        code, out, _ = await self._exec(("git", "diff", "--name-only", "HEAD"), root)
        if code != 0:
            return "", []
        files = [line.strip() for line in out.splitlines() if line.strip()]
        _, diff, _ = await self._exec(("git", "diff", "HEAD"), root)
        return diff, files

    async def run(self, root: Path) -> list[CheckResult]:
        results: list[CheckResult] = []
        for stack in detect_stacks(root):
            for name, argv in stack.checks:
                if not shutil.which(argv[0]):
                    results.append(CheckResult(name, True, skipped=True,
                                               detail=f"{argv[0]} not installed"))
                    continue
                code, out, err = await self._exec(argv, root)
                results.append(CheckResult(
                    name, code == 0,
                    detail=bound_output(err or out, "tests", 2_000)
                ))
        return results

    async def _exec(self, argv: tuple[str, ...], cwd: Path) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
        except (TimeoutError, FileNotFoundError, OSError) as exc:
            return 1, "", str(exc)
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
