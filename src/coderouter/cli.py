"""coderouter command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, Config, load_config, write_default_config
from .core.doctor import run_doctor
from .core.models import RoutingMode
from .core.orchestrator import Orchestrator
from .errors import CodeRouterError
from .logging import setup_logging
from .storage.db import Database

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def _bootstrap(args: argparse.Namespace) -> tuple[Config, Database]:
    config = load_config(Path(args.config) if args.config else None)
    setup_logging(config.log_dir, config.log_level)
    return config, Database(config.db_path)


async def cmd_doctor(args: argparse.Namespace) -> int:
    config, db = _bootstrap(args)
    db.close()
    findings = await run_doctor(config, Path.cwd())
    failed = 0
    for f in findings:
        mark = f"{GREEN}✓{RESET}" if f.ok else f"{RED}✗{RESET}"
        print(f" {mark} {f.name:<28} {DIM}{f.detail}{RESET}")
        if not f.ok and f.remediation:
            print(f"   {DIM}→ {f.remediation}{RESET}")
            failed += 1
    return 1 if failed else 0


async def cmd_agents(args: argparse.Namespace) -> int:
    config, db = _bootstrap(args)
    orch = Orchestrator(config, db)
    for adapter in orch.registry:
        health = await adapter.health_check()
        mark = f"{GREEN}●{RESET}" if health.healthy else f"{RED}○{RESET}"
        caps = ", ".join(sorted(c.value for c in adapter.capabilities.capabilities))
        print(f" {mark} {BOLD}{adapter.display_name}{RESET} ({adapter.id})")
        print(f"   command: {adapter.command}  status: {health.detail}")
        print(f"   {DIM}{caps}{RESET}")
    db.close()
    return 0


async def cmd_usage(args: argparse.Namespace) -> int:
    config, db = _bootstrap(args)
    orch = Orchestrator(config, db)
    for adapter in orch.registry:
        info = await orch.usage.usage_for(adapter)
        print(f"{BOLD}{adapter.display_name}{RESET}  [{info.status.value.upper()}]")
        for window in info.windows:
            if window.fraction is None:
                print(f"  {window.label:<8} {DIM}no limit configured — "
                      f"{window.used_tokens:,} tokens spent via coderouter{RESET}")
            else:
                filled = int(window.fraction * 20)
                bar = "█" * filled + "░" * (20 - filled)
                print(f"  {window.label:<8} {bar} {window.fraction:.0%}  "
                      f"{window.used_tokens:,}/{window.limit_tokens:,}")
    db.close()
    return 0


async def cmd_status(args: argparse.Namespace) -> int:
    config, db = _bootstrap(args)
    metrics = db.dashboard_metrics()
    runs, ok = metrics["runs"], metrics["successes"]
    print(f"Runs:            {runs}")
    print(f"Successful:      {ok}")
    print(f"Success rate:    {ok / runs:.0%}" if runs else "Success rate:    n/a")
    print(f"Avg duration:    {metrics['avg_duration_s']:.1f}s")
    print(f"Tokens spent:    {metrics['total_tokens']:,}")
    for state, count in sorted(metrics["tasks_by_state"].items()):
        print(f"  {state:<12} {count}")
    db.close()
    return 0


async def cmd_config(args: argparse.Namespace) -> int:
    if args.init:
        path = write_default_config(Path(args.config) if args.config else None)
        print(f"config at {path}")
        return 0
    path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    if not path.exists():
        print(f"no config at {path} (run `router config --init`)")
        return 1
    print(path.read_text())
    return 0


async def cmd_run(args: argparse.Namespace) -> int:
    config, db = _bootstrap(args)
    orch = Orchestrator(config, db)
    mode = RoutingMode(args.mode) if args.mode else None
    task = await orch.analyze(args.prompt, Path.cwd())
    task.forced_agent = getattr(args, "agent", None)
    print(f"{DIM}complexity={task.complexity.value} risk={task.risk.value} "
          f"type={task.task_type} context_files={len(task.context_files)} "
          f"source={task.classification_source} "
          f"confidence={task.classification_confidence:.0%}{RESET}")
    graph = orch.plan(task)
    if len(graph.tasks) > 1:
        print(f"{DIM}decomposed into {len(graph.tasks)} subtasks{RESET}")

    async def on_event(kind: str, payload: dict) -> None:
        print(f"{DIM}[{kind}] {payload}{RESET}")

    try:
        outcomes = await orch.run_graph(graph, mode_override=mode, on_event=on_event)
    except CodeRouterError as exc:
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return 1
    finally:
        db.close()

    for outcome in outcomes:
        if outcome.result:
            print(outcome.result.output)
    return 0 if all(o.result and o.result.success for o in outcomes) else 1


async def cmd_tui(args: argparse.Namespace) -> int:
    config, db = _bootstrap(args)
    from .tui.app import CodeRouterApp

    await CodeRouterApp(config, db).run_async()
    db.close()
    return 0


async def cmd_memory(args: argparse.Namespace) -> int:
    """Project-scoped memory: list / add / forget."""
    config, db = _bootstrap(args)
    from .memory import MemoryKind, MemoryStore
    store = MemoryStore(db)
    project_id = str(Path.cwd().resolve())
    if args.memory_cmd == "list":
        notes = store.list(project_id, limit=50)
        if not notes:
            print(f"{DIM}no memory notes for {project_id}{RESET}")
            return 0
        for n in notes:
            print(f" {BOLD}[{n.kind.value}]{RESET} {n.key} {DIM}{n.value}{RESET}")
    elif args.memory_cmd == "add":
        kind = MemoryKind(args.kind)
        store.upsert(project_id, kind, args.key, args.value)
        print(f"{GREEN}noted:{RESET} [{kind.value}] {args.key}")
    elif args.memory_cmd == "forget":
        kind = MemoryKind(args.kind)
        ok = store.delete(project_id, kind, args.key)
        if ok:
            print(f"{GREEN}forgot:{RESET} [{kind.value}] {args.key}")
        else:
            print(f"{RED}no such note:{RESET} [{kind.value}] {args.key}", file=sys.stderr)
            return 1
    db.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Subcommand parser. A free-text prompt is handled by run_parser instead,
    because argparse cannot disambiguate a positional from a subcommand."""
    parser = argparse.ArgumentParser("router", description="AI coding orchestrator")
    parser.add_argument("--config", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="check the installation")
    sub.add_parser("agents", help="list agents and their health")
    sub.add_parser("usage", help="show usage windows")
    sub.add_parser("status", help="show orchestrator metrics")
    cfg = sub.add_parser("config", help="show or create the config file")
    cfg.add_argument("--init", action="store_true", help="write a default config")
    mem = sub.add_parser("memory", help="project-scoped notes (list|add|forget)")
    mem_sub = mem.add_subparsers(dest="memory_cmd", required=True)
    mem_sub.add_parser("list", help="list notes for the current project")
    add = mem_sub.add_parser("add", help="add or update a note")
    add.add_argument(
        "kind",
        help="one of convention|decision|gotcha|note|success_pattern|failure_pattern",
    )
    add.add_argument("key", help="short identifier (e.g. python-style)")
    add.add_argument("value", help="the note text")
    forget = mem_sub.add_parser("forget", help="delete a note")
    forget.add_argument("kind")
    forget.add_argument("key")
    return parser


def run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser("router", description="run a task")
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("--mode", choices=[m.value for m in RoutingMode],
                        help="override the routing mode for this run")
    parser.add_argument("--agent", help="force a specific agent id")
    parser.add_argument("prompt", nargs="?", help="task to run; omit to open the TUI")
    parser.set_defaults(command=None)
    return parser


# Options that consume the following token, so it is never a subcommand.
_VALUE_OPTIONS = {"--config", "--mode", "--agent"}


def _first_positional(argv: list[str]) -> str | None:
    skip = False
    for token in argv:
        if skip:
            skip = False
            continue
        if token in _VALUE_OPTIONS:
            skip = True
            continue
        if not token.startswith("-"):
            return token
    return None


COMMANDS = {
    "doctor": cmd_doctor, "agents": cmd_agents, "usage": cmd_usage,
    "status": cmd_status, "config": cmd_config, "memory": cmd_memory,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "-h" in argv or "--help" in argv:
        build_parser().parse_args(argv)
    subcommand = _first_positional(argv)
    if subcommand in COMMANDS:
        args = build_parser().parse_args(argv)
        handler = COMMANDS[args.command]
    else:
        args = run_parser().parse_args(argv)
        handler = cmd_run if args.prompt else cmd_tui
    try:
        return asyncio.run(handler(args))
    except KeyboardInterrupt:
        return 130
    except CodeRouterError as exc:
        print(f"{RED}error:{RESET} {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
