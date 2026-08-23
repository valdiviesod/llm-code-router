from datetime import UTC, datetime

from v4ld1.core.models import (
    Candidate,
    Risk,
    RoutingDecision,
    RoutingMode,
    UsageEstimate,
    UsageInfo,
    UsageStatus,
    UsageWindow,
)
from v4ld1.tui.app import V4ld1App
from v4ld1.tui.widgets import RoutingPanel, UsagePanel, usage_bar


def test_usage_bar_marks_unknown_limits():
    assert "no limit" in usage_bar(None).plain


def test_usage_bar_scales():
    assert usage_bar(0.5, width=10).plain.startswith("█████░░░░░")


async def test_app_starts_and_shows_panels(config, db):
    app = V4ld1App(config, db)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#usage", UsagePanel)
        assert app.query_one("#routing-panel", RoutingPanel)


async def test_mode_switch_binding(config, db):
    app = V4ld1App(config, db)
    async with app.run_test() as pilot:
        await pilot.press("f2")
        assert app.mode_override is RoutingMode.ECONOMY
        await pilot.press("f1")
        assert app.mode_override is None


def test_routing_panel_renders_conservation_banner():
    panel = RoutingPanel()
    now = datetime.now(UTC)
    panel.decision = RoutingDecision(
        task_id="t", selected_agent="claude", selected_model=None, reason="because",
        alternatives=[Candidate("antigravity", 0.5, None)], confidence=0.8,
        estimated_usage=UsageEstimate(10, 5), risk=Risk.LOW, mode=RoutingMode.AUTO,
        conservation=True,
    )
    assert "QUOTA CONSERVATION MODE" in panel.render().plain


def test_usage_panel_labels_estimated_data():
    panel = UsagePanel()
    now = datetime.now(UTC)
    panel.infos = [UsageInfo("claude", [
        UsageWindow("5h", 50, 100, now, now, UsageStatus.ESTIMATED)
    ], UsageStatus.ESTIMATED)]
    assert panel.render().row_count == 1
