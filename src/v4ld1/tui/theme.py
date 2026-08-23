"""The TUI palettes.

Kept apart from the widgets because colour is the one thing users ask to change
and nobody should have to read layout code to do it.

Two colour systems have to agree here. Textual CSS resolves `$primary` and
friends from the registered `Theme`; Rich renderables (`Table`, `Text`,
`RichLog.write`) know nothing about Textual theme variables and need literal
hex. So every theme ships with a matching `Palette` of hex strings, and
`use_palette()` swaps the one the widgets read.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.theme import Theme


@dataclass(frozen=True, slots=True)
class Palette:
    """Hex values for the Rich side of the app. Mirrors the Textual theme."""

    ok: str
    warn: str
    danger: str
    muted: str
    info: str
    alt: str
    accent: str


# --- pastel -----------------------------------------------------------------
# Muted pastels on a near-black ground: enough contrast to read for hours,
# not enough saturation to fight the agent output that scrolls through it.
PASTEL_DARK = Theme(
    name="v4ld1-pastel",
    dark=True,
    background="#16171f",
    surface="#1d1f29",
    panel="#252734",
    foreground="#d8dbe8",
    primary="#a8c0e8",
    secondary="#c4b3e0",
    accent="#e8b4c8",
    success="#a4d4b4",
    warning="#e8cfa0",
    error="#e8a8a8",
    variables={
        "border": "#3a3d4e",
        "border-blurred": "#2c2f3d",
        "text-muted": "#8b90a6",
        "text-disabled": "#5c6178",
        "block-cursor-background": "#a8c0e8",
        "block-cursor-foreground": "#16171f",
        "input-cursor-background": "#e8b4c8",
        "input-selection-background": "#a8c0e8 35%",
        "footer-key-foreground": "#e8b4c8",
        "footer-description-foreground": "#8b90a6",
        "scrollbar": "#2c2f3d",
        "scrollbar-hover": "#3a3d4e",
        "scrollbar-active": "#a8c0e8",
    },
)

PASTEL_PALETTE = Palette(
    ok="#a4d4b4",
    warn="#e8cfa0",
    danger="#e8a8a8",
    muted="#8b90a6",
    info="#a8c0e8",
    alt="#c4b3e0",
    accent="#e8b4c8",
)

# --- gruvbox ----------------------------------------------------------------
# Upstream gruvbox "dark, hard" background with the standard bright accents.
GRUVBOX_DARK = Theme(
    name="gruvbox-dark",
    dark=True,
    background="#1d2021",   # bg0_h
    surface="#282828",      # bg0
    panel="#3c3836",        # bg1
    foreground="#ebdbb2",   # fg1
    primary="#83a598",      # bright blue
    secondary="#d3869b",    # bright purple
    accent="#fabd2f",       # bright yellow
    success="#b8bb26",      # bright green
    warning="#fe8019",      # bright orange
    error="#fb4934",        # bright red
    variables={
        "border": "#504945",        # bg2
        "border-blurred": "#3c3836",
        "text-muted": "#a89984",    # fg4
        "text-disabled": "#928374",  # gray
        "block-cursor-background": "#83a598",
        "block-cursor-foreground": "#1d2021",
        "input-cursor-background": "#fabd2f",
        "input-selection-background": "#83a598 35%",
        "footer-key-foreground": "#fabd2f",
        "footer-description-foreground": "#a89984",
        "scrollbar": "#3c3836",
        "scrollbar-hover": "#504945",
        "scrollbar-active": "#83a598",
    },
)

GRUVBOX_PALETTE = Palette(
    ok="#b8bb26",
    warn="#fe8019",
    danger="#fb4934",
    muted="#a89984",
    info="#83a598",
    alt="#d3869b",
    accent="#fabd2f",
)

THEMES = {
    GRUVBOX_DARK.name: (GRUVBOX_DARK, GRUVBOX_PALETTE),
    PASTEL_DARK.name: (PASTEL_DARK, PASTEL_PALETTE),
}
DEFAULT_THEME = GRUVBOX_DARK.name

_active: Palette = GRUVBOX_PALETTE


def use_palette(theme_name: str) -> Palette:
    """Point the Rich renderables at the palette matching `theme_name`.

    Unknown names keep the current palette rather than raising: a user typo in
    config should recolour nothing, not crash the only UI they have.
    """
    global _active
    entry = THEMES.get(theme_name)
    if entry is not None:
        _active = entry[1]
    return _active


def palette() -> Palette:
    """The palette the widgets should draw with right now."""
    return _active
