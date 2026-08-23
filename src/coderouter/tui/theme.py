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
    name="coderouter-pastel",
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

# --- opencode ---------------------------------------------------------------
# OpenCode dark aesthetic: deep slate-obsidian with electric cyan and purple accents.
OPENCODE_DARK = Theme(
    name="opencode-dark",
    dark=True,
    background="#0d0f18",
    surface="#131622",
    panel="#1a1e2e",
    foreground="#e2e8f0",
    primary="#38bdf8",
    secondary="#a855f7",
    accent="#38bdf8",
    success="#34d399",
    warning="#fbbf24",
    error="#f87171",
    variables={
        "border": "#282d42",
        "border-blurred": "#1e2233",
        "text-muted": "#64748b",
        "text-disabled": "#475569",
        "block-cursor-background": "#38bdf8",
        "block-cursor-foreground": "#0d0f18",
        "input-cursor-background": "#38bdf8",
        "input-selection-background": "#38bdf8 30%",
        "footer-key-foreground": "#38bdf8",
        "footer-description-foreground": "#64748b",
        "scrollbar": "#1e2233",
        "scrollbar-hover": "#282d42",
        "scrollbar-active": "#38bdf8",
    },
)

OPENCODE_PALETTE = Palette(
    ok="#34d399",
    warn="#fbbf24",
    danger="#f87171",
    muted="#64748b",
    info="#38bdf8",
    alt="#a855f7",
    accent="#38bdf8",
)

OPENCODE_CYBER = Theme(
    name="opencode-cyber",
    dark=True,
    background="#08090d",
    surface="#0f1118",
    panel="#151824",
    foreground="#f1f5f9",
    primary="#00f2fe",
    secondary="#b388ff",
    accent="#00f2fe",
    success="#00e676",
    warning="#ffd600",
    error="#ff1744",
    variables={
        "border": "#1f2438",
        "border-blurred": "#141724",
        "text-muted": "#546e7a",
        "text-disabled": "#37474f",
        "block-cursor-background": "#00f2fe",
        "block-cursor-foreground": "#08090d",
        "input-cursor-background": "#00f2fe",
        "input-selection-background": "#00f2fe 30%",
        "footer-key-foreground": "#00f2fe",
        "footer-description-foreground": "#546e7a",
        "scrollbar": "#141724",
        "scrollbar-hover": "#1f2438",
        "scrollbar-active": "#00f2fe",
    },
)

OPENCODE_CYBER_PALETTE = Palette(
    ok="#00e676",
    warn="#ffd600",
    danger="#ff1744",
    muted="#546e7a",
    info="#00f2fe",
    alt="#b388ff",
    accent="#00f2fe",
)

THEMES = {
    GRUVBOX_DARK.name: (GRUVBOX_DARK, GRUVBOX_PALETTE),
    OPENCODE_DARK.name: (OPENCODE_DARK, OPENCODE_PALETTE),
    OPENCODE_CYBER.name: (OPENCODE_CYBER, OPENCODE_CYBER_PALETTE),
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
