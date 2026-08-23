"""Presentation-only widgets. They render state, they never compute it."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ..core.models import RoutingDecision, UsageInfo

BAR_WIDTH = 12


def usage_bar(fraction: float | None, width: int = BAR_WIDTH) -> Text:
    if fraction is None:
        return Text("no limit set", style="dim")
    filled = int(fraction * width)
    style = "green" if fraction < 0.6 else "yellow" if fraction < 0.85 else "red"
    bar = Text("█" * filled, style=style)
    bar.append("░" * (width - filled), style="dim")
    bar.append(f"  {fraction:.0%}")
    return bar


class UsagePanel(Static):
    """One row per agent per window, with an explicit ESTIMATED/CONFIRMED tag."""

    infos: reactive[list[UsageInfo]] = reactive(list, layout=True)

    def render(self) -> Table:
        table = Table.grid(padding=(0, 2))
        table.add_column("agent", style="bold")
        table.add_column("window")
        table.add_column("bar")
        table.add_column("source", style="dim")
        for info in self.infos:
            for window in info.windows:
                table.add_row(info.agent_id, window.label,
                              usage_bar(window.fraction), info.status.value.upper())
        if not self.infos:
            table.add_row("—", "—", Text("no data yet", style="dim"), "")
        return table


class RoutingPanel(Static):
    decision: reactive[RoutingDecision | None] = reactive(None, layout=True)

    def render(self) -> Text:
        d = self.decision
        if d is None:
            return Text("No routing decision yet.", style="dim")
        text = Text()
        text.append(f"Selected: {d.selected_agent}", style="bold green")
        if d.selected_model:
            text.append(f"  ({d.selected_model})", style="dim")
        text.append(f"\nMode: {d.mode.value}   Confidence: {d.confidence:.0%}\n")
        text.append(f"Estimate: ~{d.estimated_usage.total_tokens:,} tokens\n", style="dim")
        text.append(f"Reason: {d.reason}\n")
        if d.conservation:
            text.append("QUOTA CONSERVATION MODE\n", style="bold red")
        for alt in d.alternatives:
            text.append(f"  {alt.agent_id}: {alt.score:.2f}\n", style="dim")
        return text


class MetricsPanel(Static):
    metrics: reactive[dict] = reactive(dict, layout=True)

    def render(self) -> Table:
        m = self.metrics
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim")
        table.add_column(justify="right")
        runs = m.get("runs", 0)
        ok = m.get("successes", 0)
        table.add_row("Runs", str(runs))
        table.add_row("Success rate", f"{ok / runs:.0%}" if runs else "n/a")
        table.add_row("Avg duration", f"{m.get('avg_duration_s', 0):.1f}s")
        table.add_row("Tokens spent", f"{m.get('total_tokens', 0):,}")
        # The headline metric: successful work per million tokens burned.
        tokens = m.get("total_tokens", 0)
        efficiency = f"{ok / (tokens / 1_000_000):.1f}" if tokens else "n/a"
        table.add_row("Successes / Mtok", efficiency)
        for state, count in sorted(m.get("tasks_by_state", {}).items()):
            table.add_row(f"tasks:{state}", str(count))
        return table
