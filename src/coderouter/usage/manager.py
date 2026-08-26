"""Usage accounting and forecasting.

Neither Claude Code nor the Antigravity CLI exposes subscription quota, so this
module is the source of truth. It sums the tokens coderouter itself has spent inside
a rolling window (SQL SUM, never Python arithmetic over a fetched page) and
compares that against limits the *user* configured. Any figure derived this way
is reported as ESTIMATED; if the adapter ever gains a real quota API its
UsageInfo wins and is reported as CONFIRMED.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..agents.base.adapter import AgentAdapter
from ..config import Config
from ..core.models import UsageEstimate, UsageInfo, UsageStatus, UsageWindow
from ..storage.db import Database

WEEK_HOURS = 24 * 7


@dataclass(slots=True)
class Forecast:
    agent_id: str
    window: UsageWindow | None
    projected_fraction: float | None
    safe: bool
    reason: str


class UsageManager:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def _rolling_window(
        self, agent_id: str, label: str, hours: float, limit: int | None
    ) -> UsageWindow:
        now = datetime.now(UTC)
        start = now - timedelta(hours=hours)
        used = self.db.tokens_since(agent_id, start)
        return UsageWindow(
            label=label,
            used_tokens=used,
            limit_tokens=limit,
            window_start=start,
            window_end=now + timedelta(hours=hours),
            status=UsageStatus.ESTIMATED if limit else UsageStatus.UNKNOWN,
        )

    async def usage_for(self, adapter: AgentAdapter) -> UsageInfo:
        provider = await adapter.get_usage()
        if provider.status is UsageStatus.CONFIRMED and provider.windows:
            return provider
        cfg = self.config.agent(adapter.id)
        windows = [
            self._rolling_window(adapter.id, f"{cfg.window_hours:g}h",
                                 cfg.window_hours, cfg.window_limit_tokens),
            self._rolling_window(adapter.id, "weekly", WEEK_HOURS, cfg.weekly_limit_tokens),
        ]
        status = (UsageStatus.ESTIMATED
                  if any(w.limit_tokens for w in windows) else UsageStatus.UNKNOWN)
        return UsageInfo(adapter.id, windows, status)

    async def forecast(self, adapter: AgentAdapter, estimate: UsageEstimate) -> Forecast:
        cfg = self.config.agent(adapter.id)
        info = await self.usage_for(adapter)
        window = info.window(f"{cfg.window_hours:g}h")
        if window is None or window.limit_tokens is None:
            return Forecast(adapter.id, window, None, True,
                            "no configured limit; usage unknown")
        projected = (window.used_tokens + estimate.total_tokens) / window.limit_tokens
        threshold = self.config.routing.safe_threshold_percent / 100
        reserve_floor = 1 - cfg.reserve_percent / 100
        limit = min(threshold, reserve_floor)
        safe = projected <= limit
        reason = (f"projected {projected:.0%} vs limit {limit:.0%} "
                  f"(threshold {threshold:.0%}, reserve {cfg.reserve_percent:g}%)")
        return Forecast(adapter.id, window, projected, safe, reason)

    async def pressure(self, adapter: AgentAdapter) -> float:
        """0.0 = plenty of quota, 1.0 = exhausted. Unknown limits give 0.0 so a
        missing limit never silently penalises an agent."""
        info = await self.usage_for(adapter)
        cfg = self.config.agent(adapter.id)
        window = info.window(f"{cfg.window_hours:g}h")
        return window.fraction if window and window.fraction is not None else 0.0

    def pressure_for(self, agent_id: str) -> float:
        """Synchronous pressure for an agent id, used by the QuotaPool book.

        Returns 0.0 when the agent has no configured limit. The legacy
        `pressure(adapter)` is the async entry point used by the
        orchestrator; this one is the no-DB-context helper the book
        uses when it has already opened the database.
        """
        cfg = self.config.agent(agent_id)
        if cfg.window_limit_tokens is None or cfg.window_limit_tokens == 0:
            return 0.0
        start = (datetime.now(UTC) - timedelta(hours=cfg.window_hours)).isoformat()
        assert self.db.conn is not None
        row = self.db.conn.execute(
            "SELECT COALESCE(SUM(tokens),0) AS t FROM usage_events "
            "WHERE agent_id=? AND occurred_at>=?",
            (agent_id, start),
        ).fetchone()
        return min(int(row["t"]) / cfg.window_limit_tokens, 1.0)

    async def conservation_mode(self, adapters: list[AgentAdapter]) -> bool:
        """True when every agent with a known limit is under quota pressure."""
        known = []
        for adapter in adapters:
            cfg = self.config.agent(adapter.id)
            if cfg.window_limit_tokens:
                known.append(await self.pressure(adapter))
        return bool(known) and all(p >= 0.75 for p in known)
