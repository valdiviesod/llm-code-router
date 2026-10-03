"""Sandbox boundaries for processes the router spawns.

Worktrees isolate *files between agents*; they do not stop an agent leaving
the workspace, filling the disk, forking a fork bomb, or reaching the
network. This module draws three best-effort boundaries around every process
the router itself executes (the builtin `run_command` tool today):

- **Filesystem**: the project root is the boundary. The builtin host resolves
  every path against it and refuses escapes; the sandbox additionally caps
  the size of any file a process may create (`RLIMIT_FSIZE`).
- **Resources**: CPU seconds, address space and process count become
  `RLIMIT_*` rlimits applied in the child before `exec`.
- **Network**: when denied, the child is moved into an empty network
  namespace (`unshare(CLONE_NEWNET)`) when the kernel allows it. Without
  `CAP_SYS_ADMIN` or unprivileged user namespaces this is not enforceable,
  so the attempt is honest about failure: it logs and lets the command run
  rather than pretending a boundary exists that does not.

The agent CLIs themselves are *not* sandboxed here — they need network access
to reach their providers, and they run under their own permission systems.
What this guards is the code agents convince the router to run directly.
"""

from __future__ import annotations

import ctypes
import os
import resource
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import SandboxConfig
from ..logging import get_logger

logger = get_logger("security.sandbox")

_CLONE_NEWNET = 0x40000000

#: Environment variable names that look like credentials. A child process
#: spawned to run an agent's command has no business inheriting them.
_SECRET_ENV_RE_ENDINGS = (
    "_TOKEN", "_KEY", "_SECRET", "_PASSWORD", "_PASSWD",
)

__all__ = ["Sandbox"]


@dataclass(slots=True)
class Sandbox:
    """Applies resource and network limits to child processes."""

    config: SandboxConfig

    def check_path(self, path: Path, root: Path) -> bool:
        """Filesystem boundary: `path` must stay inside `root`."""
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            return False
        return True

    def scrub_env(self, env: dict[str, str] | None = None) -> dict[str, str]:
        """Drop credential-shaped variables before spawning a child."""
        source = dict(env) if env is not None else dict(os.environ)
        return {
            k: v for k, v in source.items()
            if not (
                k.upper().endswith(_SECRET_ENV_RE_ENDINGS)
                or k.upper() == "AWS_SECRET_ACCESS_KEY"
            )
        }

    def _preexec(self) -> None:  # runs in the forked child, before exec
        cfg = self.config
        if not cfg.enabled:
            return
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cfg.cpu_seconds, cfg.cpu_seconds))
            resource.setrlimit(
                resource.RLIMIT_AS, (cfg.memory_mb << 20, cfg.memory_mb << 20))
            resource.setrlimit(
                resource.RLIMIT_NPROC, (cfg.max_processes, cfg.max_processes))
            resource.setrlimit(
                resource.RLIMIT_FSIZE, (cfg.max_file_mb << 20, cfg.max_file_mb << 20))
        except (ValueError, OSError) as exc:  # rlimits can be lower already
            logger.warning("sandbox rlimits not fully applied: %s", exc)
        if not cfg.network:
            self._unshare_net()

    @staticmethod
    def _unshare_net() -> None:
        """Move the child into an empty network namespace, best effort.

        Runs inside the forked child (from `_preexec`), so failure can only
        be logged — the exec still happens. A denied network namespace is a
        missing boundary, and the log says so rather than staying silent.
        """
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.unshare(_CLONE_NEWNET) != 0:
                err = ctypes.get_errno()
                logger.warning("network namespace unavailable (errno %d); "
                               "network boundary NOT enforced", err)
        except Exception as exc:  # noqa: BLE001 - never break the exec
            logger.warning("network namespace unavailable (%s); "
                           "network boundary NOT enforced", exc)

    def run(self, argv: list[str], *, cwd: Path, timeout_s: int,
            env: dict[str, str] | None = None) -> tuple[int, str, str]:
        """Run `argv` inside the sandbox. Returns (exit_code, stdout, stderr).

        Timeouts kill the process and report a non-zero exit with the reason
        in stderr — a hung command must not hang the agent that asked for it.
        """
        try:
            proc = subprocess.run(  # noqa: S603 - argv is a list, shell is off
                argv, cwd=str(cwd), timeout=timeout_s,
                env=self.scrub_env(env),
                capture_output=True,
                preexec_fn=self._preexec if self.config.enabled else None,
            )
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout_s}s"
        except FileNotFoundError:
            return 127, "", f"command not found: {argv[0]}"
        return (proc.returncode,
                proc.stdout.decode(errors="replace"),
                proc.stderr.decode(errors="replace"))
