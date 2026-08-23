"""Textual TUI. Thin: it renders orchestrator state and forwards user intent."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static, TabbedContent, TabPane
from textual.worker import Worker

from ..config import Config
from ..core.models import RoutingMode
from ..core.orchestrator import Orchestrator
from ..security.policy import redact_secrets
from ..storage.db import Database
from .theme import DEFAULT_THEME, THEMES, palette, use_palette
from .widgets import AgentRow, AgentsPanel, MetricsPanel, RoutingPanel, UsagePanel

# Health checks shell out to the provider CLIs, so this is deliberately slow.
REFRESH_SECONDS = 30.0
# A run can legitimately take many minutes (agents.timeout_s defaults to 1800),
# so the status line has to tick or the app is indistinguishable from hung.
TICK_SECONDS = 1.0


def _detach_console_logging() -> list[logging.Handler]:
    """Stop log records from being written over the TUI, and return what we removed.

    Textual owns the terminal. Any handler that writes to stdout/stderr paints
    on top of it, which looks exactly like a crash. `setup_logging` only rebinds
    the `v4ld1` logger, so records from asyncio, warnings or a third-party
    library still reach `logging.lastResort`, which is a stderr handler that
    activates precisely when the root logger has none of its own.
    """
    root = logging.getLogger()
    removed: list[logging.Handler] = [
        h for h in root.handlers if isinstance(h, logging.StreamHandler)
    ]
    for handler in removed:
        root.removeHandler(handler)
    # A NullHandler keeps `lastResort` from stepping in now that root is empty.
    root.addHandler(logging.NullHandler())
    return removed


class V4ld1App(App):
    CSS_PATH = "styles.tcss"
    TITLE = "v4ld1"
    SUB_TITLE = "AI software orchestrator"
    # The prompt input keeps focus, so bindings use keys an Input ignores.
    BINDINGS = [
        # Textual unbinds ctrl+c on purpose (it is copy in an Input), leaving a
        # running app with no reflexive way out. Priority so it fires even while
        # a worker holds the app busy. `interrupt` still yields to a live text
        # selection, so copy keeps working the way every terminal user expects.
        Binding("ctrl+c", "interrupt", "Copy/Cancel/Quit", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("escape", "cancel_run", "Cancel run", show=False),
        ("f5", "refresh", "Refresh"),
        ("f1", "mode('auto')", "Auto"),
        ("f2", "mode('economy')", "Economy"),
        ("f3", "mode('balanced')", "Balanced"),
        ("f4", "mode('quality')", "Quality"),
        ("ctrl+l", "clear", "Clear"),
        ("ctrl+t", "cycle_theme", "Theme"),
        ("ctrl+y", "copy_last", "Copy reply"),
        ("ctrl+s", "copy_all", "Copy session"),
    ]

    def __init__(self, config: Config, db: Database, project_root: Path | None = None):
        super().__init__()
        self.config = config
        self.db = db
        self.project_root = project_root or Path.cwd()
        self.orchestrator = Orchestrator(config, db)
        self.mode_override: RoutingMode | None = None
        self.busy = False
        self._worker: Worker | None = None
        self._started_at = 0.0
        self._detached_handlers: list[logging.Handler] = []
        # Agent output kept verbatim: the log renders it styled and wrapped, so
        # it is not a source you can copy an exact diff or command back out of.
        self._replies: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="status")
        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard", id="dashboard"):
                with Horizontal(id="dashboard-top"):
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
                yield AgentsPanel(id="agents-panel", classes="panel")
            with TabPane("Logs", id="logs"):
                yield RichLog(id="logview", markup=False, wrap=True)
        yield Input(placeholder="What should I build?", id="prompt")
        yield Footer()

    async def on_mount(self) -> None:
        self._detached_handlers = _detach_console_logging()
        for theme, _ in THEMES.values():
            self.register_theme(theme)
        self.theme = DEFAULT_THEME
        use_palette(DEFAULT_THEME)
        self._render_status()
        self.query_one("#prompt", Input).focus()
        self.set_interval(TICK_SECONDS, self._tick)
        self.set_interval(REFRESH_SECONDS, self.action_refresh)
        await self.action_refresh()

    def on_unmount(self) -> None:
        root = logging.getLogger()
        for handler in self._detached_handlers:
            root.addHandler(handler)

    # ------------------------------------------------------------------ state

    def _tick(self) -> None:
        if self.busy:
            self._render_status()

    def _render_status(self) -> None:
        p = palette()
        mode = self.mode_override.value if self.mode_override else "auto"
        if self.busy:
            elapsed = int(time.monotonic() - self._started_at)
            state = f"[{p.warn}]running {elapsed // 60}m{elapsed % 60:02d}s[/]"
            hint = f"   [{p.muted}]esc/ctrl+c to cancel[/]"
        else:
            state = f"[{p.info}]idle[/]"
            hint = ""
        self.query_one("#status", Static).update(
            f"[{p.accent}]▍[/] [b]v4ld1[/]  [{p.muted}]mode[/] [{p.alt}]{mode}[/]"
            f"   [{p.muted}]state[/] {state}"
            f"   [{p.muted}]project[/] [{p.muted}]{self.project_root.name}[/]{hint}"
        )

    def _set_busy(self, busy: bool) -> None:
        """One run at a time. Overlapping graphs would interleave in the log and
        double-spend quota the router already budgeted for a single task."""
        self.busy = busy
        if busy:
            self._started_at = time.monotonic()
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = busy
        prompt.placeholder = "working… (esc to cancel)" if busy else "What should I build?"
        self._render_status()
        if not busy:
            prompt.focus()

    async def action_refresh(self) -> None:
        infos = [await self.orchestrator.usage.usage_for(a) for a in self.orchestrator.registry]
        self.query_one("#usage", UsagePanel).infos = infos
        self.query_one("#metrics", MetricsPanel).metrics = self.db.dashboard_metrics()
        rows = []
        for adapter in self.orchestrator.registry:
            # A broken adapter must show as unhealthy, not blank the whole panel.
            try:
                health = await adapter.health_check()
                healthy, detail = health.healthy, health.detail
            except Exception as exc:  # noqa: BLE001 - reported in the panel
                healthy, detail = False, f"health check failed: {exc}"
            rows.append(AgentRow(adapter.id, adapter.display_name, healthy, detail))
        self.query_one("#agents-panel", AgentsPanel).agents = rows

    def action_mode(self, mode: str) -> None:
        self.mode_override = None if mode == "auto" else RoutingMode(mode)
        self._render_status()
        self._note(f"routing mode: {mode}")

    def action_clear(self) -> None:
        self.query_one("#stream", RichLog).clear()

    def action_cycle_theme(self) -> None:
        names = list(THEMES)
        index = names.index(self.theme) + 1 if self.theme in names else 0
        name = names[index % len(names)]
        self.theme = name
        use_palette(name)
        # Rich renderables cache nothing, but the reactive panels must redraw to
        # pick up the new hex values.
        for widget in self.query(Static):
            widget.refresh()
        self._render_status()
        self._note(f"theme: {name}")

    def action_interrupt(self) -> None:
        """Ctrl+C: cancel the run if one is in flight, otherwise quit.

        A reflexive ctrl+c during a long agent call should stop that call, not
        throw away the session; pressing it again with nothing running quits.
        """
        if self.screen.get_selected_text():
            self.action_copy_text()
            return
        if self.busy:
            self.action_cancel_run()
        else:
            self.exit()

    def action_cancel_run(self) -> None:
        if self._worker is not None and self.busy:
            self._worker.cancel()
            self._note("cancelling…")

    # ------------------------------------------------------------------ copy

    def _copy(self, text: str, label: str) -> None:
        """Copy through the terminal (OSC 52), because the TUI has no X display.

        Nothing here reaches the log or the database, so the text is handed over
        as typed. Anything already redacted stays redacted.
        """
        if not text:
            self._note(f"nothing to copy ({label})")
            return
        self.copy_to_clipboard(text)
        self._note(f"copied {label} — {len(text)} chars")

    def action_copy_text(self) -> None:
        """Copy the mouse selection, then drop it so the highlight does not linger."""
        self._copy(self.screen.get_selected_text() or "", "selection")
        self.screen.clear_selection()

    def action_copy_last(self) -> None:
        self._copy(self._replies[-1] if self._replies else "", "last reply")

    def action_copy_all(self) -> None:
        self._copy("\n\n".join(self._replies), "session")

    def _note(self, message: str) -> None:
        self.query_one("#stream", RichLog).write(f"[{palette().muted}]{message}[/]")

    # -------------------------------------------------------------------- run

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt or self.busy:
            return
        event.input.value = ""
        self.query_one("#stream", RichLog).write(f"[b {palette().accent}]❯ {prompt}[/]")
        # Busy is set here, not inside the worker: the worker may not be
        # scheduled before the user hits enter again.
        self._set_busy(True)
        self._worker = self.run_worker(
            self._run(prompt), exclusive=True, exit_on_error=False
        )

    async def _run(self, prompt: str) -> None:
        p = palette()
        stream = self.query_one("#stream", RichLog)
        logview = self.query_one("#logview", RichLog)
        try:
            task = await self.orchestrator.analyze(prompt, self.project_root)
            src_style = p.accent if task.classification_source == "llm" else p.muted
            stream.write(
                f"[{p.muted}]complexity={task.complexity.value} risk={task.risk.value} "
                f"type={task.task_type} context={len(task.context_files)} files "
                f"source=[/][{src_style}]{task.classification_source}[/]"
                f"[{p.muted}] confidence={task.classification_confidence:.0%}[/]"
            )
            graph = self.orchestrator.plan(task)
            if len(graph.tasks) > 1:
                stream.write(f"[{p.muted}]decomposed into {len(graph.tasks)} subtasks[/]")
            stream.write(f"[{p.muted}]dispatching… this can take minutes[/]")

            async def on_event(kind: str, payload: dict) -> None:
                logview.write(redact_secrets(f"{kind}: {payload}"))
                if kind == "routed":
                    stream.write(
                        f"[{p.ok}]→ {payload['agent']}[/] [{p.muted}]{payload['reason']}[/]"
                    )

            try:
                outcomes = await self.orchestrator.run_graph(
                    graph, mode_override=self.mode_override, on_event=on_event
                )
            except asyncio.CancelledError:
                stream.write(f"[{p.warn}]cancelled[/]")
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
                stream.write(f"[{p.danger}]{exc}[/]")
                return
            for outcome in outcomes:
                if outcome.decision:
                    self.query_one("#routing-panel", RoutingPanel).decision = outcome.decision
                if outcome.result:
                    self._replies.append(outcome.result.output)
                    # Agent output is data, not markup: a stray "[" in a diff
                    # must not be parsed as a style tag.
                    stream.write(Text(outcome.result.output))
            await self.action_refresh()
        finally:
            self._set_busy(False)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """A cancelled or crashed worker must still release the prompt."""
        if event.worker is not self._worker:
            return
        if event.state.name in ("CANCELLED", "ERROR", "SUCCESS") and self.busy:
            self._set_busy(False)
