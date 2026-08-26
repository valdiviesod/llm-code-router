"""Environment diagnostics. Reports only what it actually checked."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from ..agents.base.registry import AgentRegistry
from ..config import DEFAULT_CONFIG_PATH, Config
from ..git.worktree import WorktreeManager, has_commits
from ..skills import SkillRegistry
from ..storage.db import SCHEMA_VERSION, Database
from ..tools import MCPServer, ToolRegistry
from ..tools.mcp_client import MCPClient
from .models import Risk


@dataclass(slots=True)
class Finding:
    name: str
    ok: bool
    detail: str = ""
    remediation: str = ""


async def run_doctor(config: Config, project_root: Path) -> list[Finding]:
    findings: list[Finding] = [
        Finding("Python", sys.version_info >= (3, 11),
                f"{sys.version.split()[0]}", "coderouter needs Python 3.11 or newer"),
        Finding("Git", bool(shutil.which("git")),
                shutil.which("git") or "not found", "Install git"),
    ]

    registry = AgentRegistry(config)
    findings.append(Finding("Agents registered", len(registry) > 0,
                            ", ".join(registry.ids) or "none",
                            "Enable at least one agent in the config"))
    for adapter in registry:
        health = await adapter.health_check()
        findings.append(Finding(
            f"{adapter.display_name}", health.healthy,
            f"{health.detail}{' v' + health.version if health.version else ''}",
            health.remediation,
        ))
        models = await adapter.get_models()
        findings.append(Finding(
            f"{adapter.display_name} models", bool(models),
            f"{len(models)} available",
            f"`{adapter.command}` returned no models. Check authentication, or "
            "retry — this call hits the network and can fail transiently.",
        ))

    findings.append(Finding("Configuration", DEFAULT_CONFIG_PATH.exists(),
                            str(DEFAULT_CONFIG_PATH),
                            "Run `router config --init` to create it"))
    try:
        db = Database(config.db_path)
        version = db.schema_version
        db.close()
        findings.append(Finding("Database", True, str(config.db_path)))
        findings.append(Finding(
            "Schema version", version == SCHEMA_VERSION,
            f"{version} (expected {SCHEMA_VERSION})",
            "The database is newer than this install; upgrade coderouter",
        ))
    except Exception as exc:  # noqa: BLE001
        findings.append(Finding("Database", False, str(exc),
                                f"Check permissions on {config.data_dir}"))

    findings.append(Finding("Project", (project_root / ".git").exists(),
                            str(project_root),
                            "Not a git repository: worktree isolation is disabled"))

    findings.extend(await _worktree_findings(project_root))
    findings.extend(_skill_findings(config, project_root))
    findings.extend(_tool_findings(config))
    findings.extend(await _mcp_findings(config))

    unknown = [aid for aid in registry.ids
               if config.agent(aid).window_limit_tokens is None]
    findings.append(Finding(
        "Usage limits configured", not unknown,
        f"unknown for: {', '.join(unknown)}" if unknown else "all agents configured",
        "Set agents.<id>.window_limit_tokens so usage can be estimated "
        "instead of reported as UNKNOWN",
    ))
    return findings


async def _worktree_findings(project_root: Path) -> list[Finding]:
    """Worktree isolation is only real if a worktree can actually be created.

    Reporting "git present" was never enough: a repository with no commits
    cannot be branched from, and a stale `.worktrees/` from a killed run makes
    every later `worktree add` fail.
    """
    if not (project_root / ".git").exists():
        return []
    findings: list[Finding] = []
    if not await has_commits(project_root):
        findings.append(Finding(
            "Worktree isolation", False, "repository has no commits",
            "Make an initial commit; `git worktree add` needs one to branch from",
        ))
        return findings

    manager = WorktreeManager(project_root)
    probe = f"doctor-{os.getpid()}"
    created = False
    try:
        async with manager.isolated(probe, "probe") as wt:
            created = wt is not None
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never crash
        findings.append(Finding("Worktree isolation", False, str(exc),
                                "Run `git worktree prune` and retry"))
        return findings
    findings.append(Finding(
        "Worktree isolation", created,
        "can create and remove a worktree" if created else "could not create a worktree",
        "Run `git worktree prune`; a stale entry blocks new worktrees",
    ))

    stale = [p.name for p in (project_root / ".worktrees").glob("*")] \
        if (project_root / ".worktrees").exists() else []
    findings.append(Finding(
        "Worktree leftovers", not stale,
        f"{len(stale)} left behind" if stale else "none",
        "Run `git worktree prune` and delete .worktrees/ entries from killed runs",
    ))

    code, out, _ = await _git_out(project_root, "branch", "--list", "coderouter/*")
    leftover = [b.strip("* ").strip() for b in out.splitlines() if b.strip()]
    findings.append(Finding(
        "Unmerged attempt branches", not leftover,
        f"{len(leftover)}: {', '.join(leftover[:3])}" if leftover else "none",
        "These hold work from conflicted merges. Review and merge or delete them.",
    ))
    return findings


async def _git_out(root: Path, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(root),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def _skill_findings(config: Config, project_root: Path) -> list[Finding]:
    if not config.skills.enabled:
        return [Finding("Skills", True, "disabled in config")]
    project = project_root / config.skills.project_search_path
    registry = SkillRegistry.from_paths([project, config.skills.user_search_path])
    return [Finding(
        "Skills", True,
        f"{len(registry)} loaded from {project} and {config.skills.user_search_path}",
    )]


def _tool_findings(config: Config) -> list[Finding]:
    registry = ToolRegistry()
    risky = [t.name for t in registry.builtin if t.risk is Risk.HIGH]
    return [
        Finding("Tools", True, f"{len(registry.builtin)} builtin"),
        Finding(
            "Command policy", bool(config.security.block_patterns),
            f"{len(config.security.block_patterns)} block / "
            f"{len(config.security.ask_patterns)} ask rules"
            + (f"; high-risk tools: {', '.join(risky)}" if risky else ""),
            "No block patterns configured: `run_command` would classify every "
            "command as SAFE. Set security.block_patterns.",
        ),
    ]


async def _mcp_findings(config: Config) -> list[Finding]:
    """Probe every configured MCP server, because a configured server that
    never completes the handshake is indistinguishable from none at all."""
    servers = list(getattr(config, "mcp_servers", []) or [])
    if not servers:
        return [Finding("MCP servers", True, "none configured")]
    findings: list[Finding] = []
    for raw in servers:
        server = MCPServer(name=raw.name, command=raw.command,
                           args=tuple(raw.args), env=dict(raw.env))
        client = MCPClient(server, timeout_s=10.0)
        try:
            tools = await client.list_tools()
        except Exception as exc:  # noqa: BLE001
            findings.append(Finding(f"MCP {raw.name}", False, str(exc),
                                    f"Check that `{raw.command}` runs"))
            continue
        finally:
            await client.close()
        findings.append(Finding(
            f"MCP {raw.name}", bool(tools),
            f"{len(tools)} tools" if tools else "no tools",
            f"`{raw.command}` did not start or failed the initialize handshake",
        ))
    return findings
