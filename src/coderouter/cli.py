"""coderouter command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
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


async def cmd_plan(args: argparse.Namespace) -> int:
    """Dry run: everything `run` would decide, without spending a token.

    This is the debugging surface for the router. It classifies, decomposes,
    and asks the routing engine for a real decision per task — then stops.
    No task is executed and no quota is reserved.

    One honest caveat: with `classification.mode: llm` or `auto`, classifying
    the prompt is itself a model call and does spend quota. That spend is
    recorded like any other, so the figures stay true.
    """
    config, db = _bootstrap(args)
    orch = Orchestrator(config, db)
    mode = RoutingMode(args.mode) if args.mode else None
    try:
        task = await orch.analyze(args.prompt, Path.cwd())
        task.forced_agent = getattr(args, "agent", None)
        print(f"{BOLD}classification{RESET}")
        print(f"  complexity={task.complexity.value} risk={task.risk.value} "
              f"type={task.task_type}")
        print(f"  source={task.classification_source} "
              f"confidence={task.classification_confidence:.0%}")
        print(f"  context files: {len(task.context_files)}")

        graph = orch.plan(task)
        print(f"\n{BOLD}task graph{RESET}  ({len(graph.tasks)} task(s))")
        for t in graph.tasks.values():
            deps = ", ".join(t.depends_on) if getattr(t, "depends_on", None) else "-"
            print(f"  {t.id}  {t.prompt.splitlines()[0][:60]}")
            print(f"    {DIM}complexity={t.complexity.value} depends_on={deps}{RESET}")

        total = 0
        print(f"\n{BOLD}routing{RESET}")
        for t in graph.tasks.values():
            try:
                decision = await orch.router.decide(t, mode_override=mode)
            except CodeRouterError as exc:
                print(f"  {RED}{t.id}: {exc}{RESET}")
                continue
            total += decision.estimated_usage.total_tokens
            print(f"  {t.id} → {BOLD}{decision.selected_agent}{RESET}"
                  f" [{decision.selected_model or 'cli default'}]"
                  f" mode={decision.mode.value} confidence={decision.confidence:.0%}")
            for line in decision.reason.split("; "):
                print(f"    {DIM}+ {line}{RESET}")
            for alt in decision.alternatives:
                print(f"    {DIM}- {alt.agent_id} scored {alt.score:.2f}{RESET}")
            for agent_id, why in decision.rejected:
                print(f"    {RED}x {agent_id}: {why}{RESET}")
            print(f"    {DIM}estimate: {decision.estimated_usage.total_tokens} tokens"
                  f" [{decision.estimated_usage.source}, "
                  f"confidence {decision.estimated_usage.confidence:.0%}]"
                  f" (pool {decision.quota_pool or 'none'}){RESET}")

        ready = orch.scheduling_policy.decide_batch(graph)
        print(f"\n{BOLD}execution{RESET}")
        print(f"  first parallel batch: {len(ready)} task(s)")
        print(f"  worktree isolation: {'yes' if len(ready) > 1 else 'no (single task)'}")
        print(f"  estimated total: {total} tokens")
        print(f"\n{DIM}dry run — nothing was executed and no quota was reserved{RESET}")
    finally:
        db.close()
    return 0


async def cmd_explain(args: argparse.Namespace) -> int:
    """Why a past task was routed the way it was."""
    config, db = _bootstrap(args)
    try:
        task_row = db.task_row(args.task_id)
        if task_row is None:
            print(f"{RED}no such task:{RESET} {args.task_id}", file=sys.stderr)
            return 1
        print(f"{BOLD}{task_row['id']}{RESET}  {task_row['prompt'][:120]}")
        print(f"  {DIM}type={task_row['task_type']} complexity={task_row['complexity']} "
              f"risk={task_row['risk']} state={task_row['state']}{RESET}")

        decision = db.decision_for(args.task_id)
        if decision is None:
            print(f"\n{DIM}no routing decision recorded{RESET}")
        else:
            print(f"\n{BOLD}selected{RESET} {decision['selected_agent']}"
                  f" [{decision['selected_model'] or 'cli default'}]"
                  f" mode={decision['mode']} confidence={decision['confidence']:.0%}")
            for line in (decision["reason"] or "").split("; "):
                print(f"  {DIM}+ {line}{RESET}")
            for alt in json.loads(decision["alternatives"] or "[]"):
                print(f"  {DIM}- {alt['agent']} scored {alt['score']:.2f}{RESET}")
                for reason in alt.get("reasons", [])[:3]:
                    print(f"      {DIM}{reason}{RESET}")
            for rej in json.loads(decision["rejected"] or "[]"):
                print(f"  {RED}x {rej['agent']}: {rej['reason']}{RESET}")
            print(f"  {DIM}estimated {decision['estimated_tokens']} tokens{RESET}")

        runs = db.runs_for(args.task_id)
        if runs:
            print(f"\n{BOLD}runs{RESET}")
        for run in runs:
            mark = f"{GREEN}✓{RESET}" if run["success"] else f"{RED}✗{RESET}"
            integrated = run["integrated"]
            landed = ("" if integrated is None
                      else f" integrated={'yes' if integrated else 'NO'}")
            print(f"  {mark} {run['agent_id']} attempt={run['attempt']}"
                  f" tokens={run['input_tokens'] + run['output_tokens']}{landed}")
            if run["error"]:
                print(f"    {RED}{run['error'][:200]}{RESET}")
            if integrated == 0 and run["integration_detail"]:
                print(f"    {DIM}{run['integration_detail'][:200]}{RESET}")
    finally:
        db.close()
    return 0


async def cmd_quota(args: argparse.Namespace) -> int:
    """Quota pools: what is spent, what is reserved, what is left."""
    config, db = _bootstrap(args)
    orch = Orchestrator(config, db)
    book = orch.quota_book
    pools = list(book.pools) + list(book.implicit.values())
    if not pools:
        print("no quota pools configured")
        db.close()
        return 0
    for pool in pools:
        info = book.usage_info(pool)
        reserved = book.ledger.outstanding(pool.id) if book.ledger else 0
        agents = ", ".join(pool.agent_ids)
        print(f"{BOLD}{pool.id}{RESET}  [{info.status.value.upper()}]"
              f"  tier={pool.tier}  agents: {agents}")
        for window in info.windows:
            if window.limit_tokens:
                used = window.used_tokens + (reserved if window.label != "weekly" else 0)
                pct = used / window.limit_tokens
                bar = "█" * int(pct * 24) + "░" * (24 - int(pct * 24))
                print(f"  {window.label:<8} {bar} {pct:>5.0%}"
                      f"  {used}/{window.limit_tokens}")
            else:
                print(f"  {window.label:<8} {DIM}{window.used_tokens} used, "
                      f"limit unknown — no fraction can be computed{RESET}")
        if reserved:
            print(f"  {DIM}{reserved} tokens reserved by runs in flight{RESET}")
        print(f"  {DIM}reserve_percent={pool.reserve_percent:g}{RESET}")
    db.close()
    return 0


async def cmd_models(args: argparse.Namespace) -> int:
    config, db = _bootstrap(args)
    orch = Orchestrator(config, db)
    for adapter in orch.registry:
        print(f"{BOLD}{adapter.display_name}{RESET} ({adapter.id})")
        try:
            models = await adapter.get_models()
        except Exception as exc:  # noqa: BLE001 - a provider listing is best effort
            print(f"  {RED}could not list models: {exc}{RESET}")
            continue
        if not models:
            print(f"  {DIM}no models reported (this is often a transient network "
                  f"call, not an auth failure){RESET}")
        tiers = config.agent(adapter.id).model_tiers
        by_tier = {model_id: tier for tier, model_id in tiers.items()}
        for model in models:
            tier = by_tier.get(model.id)
            label = f"  {DIM}[{tier}]{RESET}" if tier else ""
            print(f"  {model.id:<40} {DIM}{model.name}{RESET}{label}")
    db.close()
    return 0


async def cmd_history(args: argparse.Namespace) -> int:
    config, db = _bootstrap(args)
    limit = args.limit
    rows = db.recent_runs(limit)
    truncated = len(rows) > limit
    for run in rows[:limit]:
        mark = f"{GREEN}✓{RESET}" if run["success"] else f"{RED}✗{RESET}"
        integrated = run["integrated"]
        landed = ("" if integrated is None
                  else (" landed" if integrated else f" {RED}stranded{RESET}"))
        print(f" {mark} {run['created_at'][:19]}  {run['agent_id']:<12}"
              f" {run['input_tokens'] + run['output_tokens']:>7} tok"
              f"  {run['task_id']}{landed}")
    if truncated:
        print(f"{DIM}... more runs not shown; this page is partial, so it is not "
              f"summed. Use `router status` for totals.{RESET}")
    db.close()
    return 0


async def cmd_skills(args: argparse.Namespace) -> int:
    config, db = _bootstrap(args)
    orch = Orchestrator(config, db)
    skills = list(orch.skill_registry)
    if not skills:
        print(f"{DIM}no skills found in {config.skills.project_search_path} "
              f"or {config.skills.user_search_path}{RESET}")
    for skill in skills:
        print(f"{BOLD}{skill.id}{RESET}  {DIM}{skill.description[:80]}{RESET}")
    db.close()
    return 0


async def cmd_tools(args: argparse.Namespace) -> int:
    config, db = _bootstrap(args)
    orch = Orchestrator(config, db)
    registry = orch.tool_registry
    print(f"{DIM}builtin tools are executable: served to agents over the MCP"
          f" bridge (`router mcp-serve`), gated by permissions + policy +"
          f" sandbox{RESET}")
    groups: list[tuple[str, list]] = [("builtin", registry.builtin)]
    groups += [(f"adapter:{aid}", tools)
               for aid, tools in registry.adapter_tools.items()]
    for label, tools in groups:
        if not tools:
            continue
        print(f"{BOLD}{label}{RESET}")
        for tool in tools:
            print(f"  {tool.name:<24} {DIM}kind={tool.kind} "
                  f"risk={tool.risk.value}{RESET}")
            if tool.description:
                print(f"    {DIM}{tool.description[:90]}{RESET}")
    if registry.mcp_servers:
        print(f"{DIM}plus MCP-discovered tools; run `router mcp --probe`{RESET}")
    db.close()
    return 0


async def cmd_mcp_serve(args: argparse.Namespace) -> int:
    """The MCP bridge process. Spawned by agent CLIs (see `--mcp-config`),
    or by hand for testing; speaks line-delimited JSON-RPC on stdio."""
    from .tools.mcp_server import main as mcp_serve_main

    argv = ["--root", args.root]
    if args.allow:
        argv += ["--allow", args.allow]
    if args.config:
        argv += ["--config", args.config]
    if args.yes:
        argv += ["--yes"]
    return mcp_serve_main(argv)


async def cmd_mcp(args: argparse.Namespace) -> int:
    """List configured MCP servers and probe the tools they expose."""
    config, db = _bootstrap(args)
    orch = Orchestrator(config, db)
    servers = orch.tool_registry.mcp_servers
    if not servers:
        print(f"{DIM}no MCP servers configured (see `mcp:` in config.yaml){RESET}")
    for name, server in servers.items():
        print(f"{BOLD}{name}{RESET}  {DIM}{server.command} "
              f"{' '.join(server.args)}{RESET}")
        if not args.probe:
            continue
        from .tools.mcp_client import MCPClient
        client = MCPClient(server)
        try:
            tools = await client.list_tools()
        finally:
            await client.close()
        if not tools:
            print(f"  {RED}no tools (server did not start, or failed the "
                  f"initialize handshake){RESET}")
        for tool in tools:
            print(f"  {tool.name:<30} {DIM}{tool.description[:60]}{RESET}")
    db.close()
    return 0


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
    sub.add_parser("quota", help="quota pools: spent, reserved, remaining")
    sub.add_parser("models", help="list the models each agent reports")
    sub.add_parser("skills", help="list discovered skills")
    sub.add_parser("tools", help="list registered tools and their permissions")
    mcp = sub.add_parser("mcp", help="list configured MCP servers")
    mcp.add_argument("--probe", action="store_true",
                     help="connect to each server and list its tools")
    serve = sub.add_parser(
        "mcp-serve",
        help="run the router's builtin-tool MCP bridge on stdio "
             "(normally spawned by agent CLIs via --mcp-config)",
    )
    serve.add_argument("--root", default=".", help="project root to serve")
    serve.add_argument("--allow", default="",
                       help="comma-separated tool names to advertise")
    serve.add_argument("--yes", action="store_true",
                       help="auto-approve ASK verdicts (explicit trust)")
    hist = sub.add_parser("history", help="recent runs")
    hist.add_argument("-n", "--limit", type=int, default=20)
    plan = sub.add_parser(
        "plan",
        help="dry run: show what `run` would do, execute nothing "
             "(classification may still spend quota)",
    )
    plan.add_argument("prompt", help="task to plan")
    plan.add_argument("--mode", choices=[m.value for m in RoutingMode])
    plan.add_argument("--agent", help="force a specific agent id")
    explain = sub.add_parser("explain", help="why a past task was routed that way")
    explain.add_argument("task_id")
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
_VALUE_OPTIONS = {"--config", "--mode", "--agent", "-n", "--limit"}


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
    "quota": cmd_quota, "models": cmd_models, "skills": cmd_skills,
    "tools": cmd_tools, "mcp": cmd_mcp, "mcp-serve": cmd_mcp_serve,
    "history": cmd_history, "plan": cmd_plan, "explain": cmd_explain,
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
