import asyncio
import logging
from datetime import UTC, datetime

from textual.widgets import Input

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
from v4ld1.tui.theme import DEFAULT_THEME, GRUVBOX_DARK, THEMES, palette
from v4ld1.tui.widgets import AgentRow, AgentsPanel, RoutingPanel, UsagePanel, usage_bar


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
    assert "quota conservation mode" in panel.render().plain.lower()


def test_usage_panel_labels_estimated_data():
    panel = UsagePanel()
    now = datetime.now(UTC)
    panel.infos = [UsageInfo("claude", [
        UsageWindow("5h", 50, 100, now, now, UsageStatus.ESTIMATED)
    ], UsageStatus.ESTIMATED)]
    assert panel.render().row_count == 1


def test_agents_panel_lists_unhealthy_detail():
    panel = AgentsPanel()
    panel.agents = [AgentRow("claude", "Claude Code", False, "cli not found")]
    rendered = panel.render()
    assert rendered.row_count == 1
    assert rendered.columns[3]._cells[0].plain == "cli not found"


async def test_prompt_is_locked_while_a_run_is_in_flight(config, db):
    app = V4ld1App(config, db)
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        app._set_busy(True)
        assert prompt.disabled
        app._set_busy(False)
        assert not prompt.disabled


async def test_gruvbox_is_the_default_theme(config, db):
    app = V4ld1App(config, db)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == GRUVBOX_DARK.name == DEFAULT_THEME
        assert app.current_theme.dark


async def test_cycling_theme_repoints_the_rich_palette(config, db):
    """CSS and Rich renderables must never disagree about the active theme."""
    app = V4ld1App(config, db)
    async with app.run_test() as pilot:
        await pilot.pause()
        seen = set()
        for _ in range(len(THEMES)):
            app.action_cycle_theme()
            await pilot.pause()
            assert palette() is THEMES[app.theme][1]
            seen.add(app.theme)
        assert seen == set(THEMES)


async def test_ctrl_c_quits_when_idle(config, db):
    app = V4ld1App(config, db)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not app.busy
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app._exit


async def test_ctrl_c_cancels_the_run_instead_of_quitting(config, db):
    """A reflexive ctrl+c mid-run must stop the run, not drop the session."""
    app = V4ld1App(config, db)
    async with app.run_test() as pilot:
        await pilot.pause()

        started = asyncio.Event()

        async def forever() -> None:
            app._set_busy(True)
            started.set()
            try:
                await asyncio.sleep(3600)
            finally:
                app._set_busy(False)

        app._worker = app.run_worker(forever(), exclusive=True, exit_on_error=False)
        await started.wait()
        await pilot.press("ctrl+c")
        for _ in range(50):
            await pilot.pause()
            if not app.busy:
                break
        assert not app.busy
        assert not app._exit


async def test_tui_detaches_console_log_handlers(config, db):
    """Anything writing to stderr paints over the TUI and looks like a crash."""
    root = logging.getLogger()
    noisy = logging.StreamHandler()
    root.addHandler(noisy)
    try:
        app = V4ld1App(config, db)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert noisy not in logging.getLogger().handlers
            assert any(isinstance(h, logging.NullHandler) for h in logging.getLogger().handlers)
    finally:
        if noisy in root.handlers:
            root.removeHandler(noisy)


async def test_ctrl_c_copies_selection_instead_of_quitting(config, db):
    app = V4ld1App(config, db)
    async with app.run_test() as pilot:
        app._replies.append("diff --git a/x b/x")
        copied: list[str] = []
        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
        app.screen.get_selected_text = lambda: "selected text"  # type: ignore[method-assign]
        await pilot.press("ctrl+c")
        assert copied == ["selected text"]
        assert app.is_running


async def test_copy_last_reply_yields_raw_agent_output(config, db):
    app = V4ld1App(config, db)
    async with app.run_test() as pilot:
        copied: list[str] = []
        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
        app._replies.append("line one\nline two")
        await pilot.press("ctrl+y")
        assert copied == ["line one\nline two"]
