"""Textual TUI. Thin: it renders orchestrator state and forwards user intent."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static, TabbedContent, TabPane

from ..config import Config
from ..core.models import RoutingMode
from ..core.orchestrator import Orchestrator
from ..storage.db import Database
from .widgets import MetricsPanel, RoutingPanel, UsagePanel

BANNER = """\
╭──────────────────────────────────────────────╮
│              v4ld1 CODE TUI                  │
│           AI SOFTWARE ORCHESTRATOR           │
╰──────────────────────────────────────────────╯"""


class V4ld1App(App):
    CSS_PATH = "styles.tcss"
    TITLE = "v4ld1"
    # The prompt input keeps focus, so bindings use keys an Input ignores.
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("f5", "refresh", "Refresh"),
        ("f1", "mode('auto')", "Auto"),
        ("f2", "mode('economy')", "Economy"),
        ("f3", "mode('balanced')", "Balanced"),
        ("f4", "mode('quality')", "Quality"),
    ]

    def __init__(self, config: Config, db: Database, project_root: Path | None = None):
        super().__init__()
        self.config = config
        self.db = db
        self.project_root = project_root or Path.cwd()
        self.orchestrator = Orchestrator(config, db)
        self.mode_override: RoutingMode | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(BANNER, id="banner")
        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard", id="dashboard"):
                with Horizontal():
                    with Vertical(classes="panel"):
                        yield Static("Usage", classes="panel-title")
                        yield UsagePanel(id="usage")
                    with Vertical(classes="panel"):
                        yield Static("Metrics", classes="panel-title")
                        yield MetricsPanel(id="metrics")
                yield RichLog(id="stream", highlight=True, markup=True, wrap=True)
            with TabPane("Routing", id="routing"):
                yield RoutingPanel(id="routing-panel", classes="panel")
            with TabPane("Agents", id="agents"):
                yield Static(id="agents-panel", classes="panel")
            with TabPane("Logs", id="logs"):
                yield RichLog(id="logview", markup=False, wrap=True)
        yield Input(placeholder="What should I build?", id="prompt")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        await self.action_refresh()

    async def action_refresh(self) -> None:
        infos = [await self.orchestrator.usage.usage_for(a)
                 for a in self.orchestrator.registry]
        self.query_one("#usage", UsagePanel).infos = infos
        self.query_one("#metrics", MetricsPanel).metrics = self.db.dashboard_metrics()
        lines = []
        for adapter in self.orchestrator.registry:
            health = await adapter.health_check()
            dot = "[green]●[/]" if health.healthy else "[red]○[/]"
            lines.append(f"{dot} [b]{adapter.display_name}[/] ({adapter.id}) — "
                         f"{health.detail}")
        self.query_one("#agents-panel", Static).update("\n".join(lines) or "no agents")

    def action_mode(self, mode: str) -> None:
        self.mode_override = None if mode == "auto" else RoutingMode(mode)
        self.query_one("#stream", RichLog).write(f"[dim]routing mode: {mode}[/]")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        event.input.value = ""
        stream = self.query_one("#stream", RichLog)
        stream.write(f"[b cyan]> {prompt}[/]")
        self.run_worker(self._run(prompt), exclusive=False)

    async def _run(self, prompt: str) -> None:
        stream = self.query_one("#stream", RichLog)
        logview = self.query_one("#logview", RichLog)
        task = self.orchestrator.analyze(prompt, self.project_root)
        stream.write(f"[dim]complexity={task.complexity.value} risk={task.risk.value} "
                     f"type={task.task_type} context={len(task.context_files)} files[/]")
        graph = self.orchestrator.plan(task)
        if len(graph.tasks) > 1:
            stream.write(f"[dim]decomposed into {len(graph.tasks)} subtasks[/]")

        async def on_event(kind: str, payload: dict) -> None:
            logview.write(f"{kind}: {payload}")
            if kind == "routed":
                stream.write(f"[green]→ {payload['agent']}[/] [dim]{payload['reason']}[/]")

        try:
            outcomes = await self.orchestrator.run_graph(
                graph, mode_override=self.mode_override, on_event=on_event
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            stream.write(f"[red]{exc}[/]")
            return
        for outcome in outcomes:
            if outcome.decision:
                self.query_one("#routing-panel", RoutingPanel).decision = outcome.decision
            if outcome.result:
                stream.write(outcome.result.output)
        await self.action_refresh()
