"""Environment diagnostics. Reports only what it actually checked."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from ..agents.base.registry import AgentRegistry
from ..config import DEFAULT_CONFIG_PATH, Config
from ..storage.db import Database


@dataclass(slots=True)
class Finding:
    name: str
    ok: bool
    detail: str = ""
    remediation: str = ""


async def run_doctor(config: Config, project_root: Path) -> list[Finding]:
    findings: list[Finding] = [
        Finding("Python", sys.version_info >= (3, 11),
                f"{sys.version.split()[0]}", "v4ld1 needs Python 3.11 or newer"),
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
                            "Run `v4ld1 config --init` to create it"))
    try:
        Database(config.db_path).close()
        findings.append(Finding("Database", True, str(config.db_path)))
    except Exception as exc:  # noqa: BLE001
        findings.append(Finding("Database", False, str(exc),
                                f"Check permissions on {config.data_dir}"))

    findings.append(Finding("Project", (project_root / ".git").exists(),
                            str(project_root),
                            "Not a git repository: worktree isolation is disabled"))

    unknown = [aid for aid in registry.ids
               if config.agent(aid).window_limit_tokens is None]
    findings.append(Finding(
        "Usage limits configured", not unknown,
        f"unknown for: {', '.join(unknown)}" if unknown else "all agents configured",
        "Set agents.<id>.window_limit_tokens so usage can be estimated "
        "instead of reported as UNKNOWN",
    ))
    return findings
