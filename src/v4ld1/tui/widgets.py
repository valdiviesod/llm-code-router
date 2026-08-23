"""Presentation-only widgets. They render state, they never compute it."""

from __future__ import annotations

from dataclasses import dataclass

from rich.table import Table
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ..core.models import RoutingDecision, UsageInfo
from .theme import palette

BAR_WIDTH = 12

# Filled/empty glyphs are half-block pairs so the bar keeps its weight at any
# terminal font size.
FILLED = "█"
EMPTY = "░"


def usage_bar(fraction: float | None, width: int = BAR_WIDTH) -> Text:
    """A quota bar, or an explicit statement that no quota is known.

    An unknown limit is never drawn as an empty bar: an empty bar reads as
    "plenty left", which is a number we do not have.
    """
    p = palette()
    if fraction is None:
        return Text("no limit set", style=f"italic {p.muted}")
    filled = int(fraction * width)
    style = p.ok if fraction < 0.6 else p.warn if fraction < 0.85 else p.danger
    bar = Text(FILLED * filled, style=style)
    bar.append(EMPTY * (width - filled), style=p.muted)
    bar.append(f"  {fraction:>4.0%}", style=f"bold {style}")
    return bar


def _status_style(status: str) -> str:
    p = palette()
    return {"confirmed": p.ok, "estimated": p.warn}.get(status.lower(), p.muted)


class UsagePanel(Static):
    """One row per agent per window, with an explicit ESTIMATED/CONFIRMED tag."""

    infos: reactive[list[UsageInfo]] = reactive(list, layout=True)

    def render(self) -> Table:
        p = palette()
        table = Table.grid(padding=(0, 2))
        table.add_column("agent", style=f"bold {p.info}")
        table.add_column("window", style=p.muted)
        table.add_column("bar")
        table.add_column("source")
        for info in self.infos:
            for window in info.windows:
                status = info.status.value
                table.add_row(
                    info.agent_id,
                    window.label,
                    usage_bar(window.fraction),
                    Text(status.upper(), style=_status_style(status)),
                )
        if not self.infos:
            table.add_row("—", "—", Text("no data yet", style=f"italic {p.muted}"), "")
        return table


class RoutingPanel(Static):
    """Why the router picked what it picked, including what it passed over."""

    decision: reactive[RoutingDecision | None] = reactive(None, layout=True)

    def render(self) -> Text:
        p = palette()
        d = self.decision
        if d is None:
            return Text("No routing decision yet.", style=f"italic {p.muted}")
        text = Text()
        text.append("▸ ", style=p.accent)
        text.append(f"{d.selected_agent}", style=f"bold {p.ok}")
        if d.selected_model:
            text.append(f"  {d.selected_model}", style=p.muted)
        text.append("\n\n")
        text.append("mode        ", style=p.muted)
        text.append(f"{d.mode.value}\n", style=p.alt)
        text.append("confidence  ", style=p.muted)
        text.append(f"{d.confidence:.0%}\n", style=p.info)
        text.append("risk        ", style=p.muted)
        text.append(f"{d.risk.value}\n", style=p.warn)
        text.append("estimate    ", style=p.muted)
        text.append(f"~{d.estimated_usage.total_tokens:,} tokens\n", style=p.info)
        text.append("reason      ", style=p.muted)
        text.append(f"{d.reason}\n")
        if d.conservation:
            text.append("\nQUOTA CONSERVATION MODE\n", style=f"bold {p.danger}")
        if d.alternatives:
            text.append("\npassed over\n", style=f"bold {p.muted}")
            for alt in d.alternatives:
                text.append(f"  {alt.agent_id:<14}", style=p.muted)
                text.append(f"{alt.score:.2f}\n", style=p.alt)
        return text


class MetricsPanel(Static):
    """Aggregates come from SQL; this only formats what the query returned."""

    metrics: reactive[dict] = reactive(dict, layout=True)

    def render(self) -> Table:
        p = palette()
        m = self.metrics
        table = Table.grid(padding=(0, 2))
        table.add_column(style=p.muted)
        table.add_column(justify="right", style=f"bold {p.info}")
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


@dataclass(slots=True)
class AgentRow:
    """Flattened health for one agent. The panel must not call an adapter."""

    agent_id: str
    display_name: str
    healthy: bool
    detail: str


class AgentsPanel(Static):
    agents: reactive[list[AgentRow]] = reactive(list, layout=True)

    def render(self) -> Table:
        p = palette()
        table = Table.grid(padding=(0, 2))
        table.add_column(width=2)
        table.add_column(style=f"bold {p.info}")
        table.add_column(style=p.muted)
        table.add_column()
        if not self.agents:
            table.add_row("", Text("no agents registered", style=f"italic {p.muted}"), "", "")
            return table
        for row in self.agents:
            dot = Text("●" if row.healthy else "○", style=p.ok if row.healthy else p.danger)
            table.add_row(
                dot,
                row.display_name,
                row.agent_id,
                Text(row.detail, style="" if row.healthy else p.danger),
            )
        return table
