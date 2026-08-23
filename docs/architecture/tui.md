# TUI

Textual application under `src/coderouter/tui/`. It renders orchestrator state and
forwards user intent; it computes nothing. Every number it shows was produced by
SQL, by an adapter, or by the usage estimator — see
[ADR-001](../adr/ADR-001-textual-tui.md).

## Files

| File | Responsibility |
|---|---|
| `theme.py` | The palette. One `textual.theme.Theme` plus the hex constants Rich needs. |
| `styles.tcss` | Layout and borders only. Colours are `$variables` from the theme. |
| `widgets.py` | Reactive, presentation-only panels. No adapter calls, no queries. |
| `app.py` | Composition, key bindings, the run worker. |

## Theming

`theme.py` holds every theme in `THEMES`; all of them are registered in
`on_mount` and `DEFAULT_THEME` (`gruvbox-dark`) is activated. `Ctrl+T` cycles.

| Theme | Notes |
|---|---|
| `gruvbox-dark` | Default. Upstream gruvbox *dark, hard* (`#1d2021`) with the bright accents. |
| `coderouter-pastel` | Desaturated pastels on near-black. |

Colours belong in exactly one of two places:

- **CSS** uses `$primary`, `$accent`, `$surface`, `$border`, … — never a literal
  hex value, so a new theme retints the whole app without touching layout.
- **Rich renderables** (`Table`, `Text`, `RichLog.write`) cannot read Textual
  theme variables. They call `palette()` at render time and use its hex fields
  (`ok`, `warn`, `danger`, `muted`, `info`, `alt`, `accent`).

Writing `[$accent]…[/]` into a `RichLog` raises `MarkupError`: that widget parses
*Rich* markup, which has no notion of `$` variables. Use the palette.

Adding a theme means adding a `Theme` **and** its `Palette` to `THEMES`. The two
halves are one entry so they cannot drift apart.

## Invariants the TUI has to keep

- **Never invent a usage number.** `usage_bar(None)` prints `no limit set`; it
  never draws an empty bar, because an empty bar reads as "plenty of quota left"
  and that is a figure nobody reported. The source tag (`CONFIRMED` /
  `ESTIMATED`) is shown on every row.
- **Agent output is data, not markup.** Results are written as `rich.text.Text`,
  so a `[` in a diff cannot be parsed as a style tag or inject styling.
- **Event payloads go through `redact_secrets`** before they reach the log pane.
- **One run at a time.** The worker is `exclusive=True` and the prompt is
  disabled while a graph is in flight — overlapping runs interleave in the log
  and double-spend quota the router budgeted for a single task.
- **A broken adapter shows as unhealthy**, it does not blank the Agents panel:
  `action_refresh` catches per-adapter health-check failures.
- **No provider branching.** The panels iterate `orchestrator.registry`; nothing
  in `tui/` knows an agent's name.
- **Nothing may write to the terminal but Textual.** `on_mount` calls
  `_detach_console_logging()`, which removes stream handlers from the *root*
  logger and installs a `NullHandler`. `setup_logging` only rebinds the `coderouter`
  logger, so without this, records from asyncio, `warnings` or a dependency fall
  through to `logging.lastResort` — a stderr handler that activates exactly when
  root has no handlers — and paint over the running app. On screen that is
  indistinguishable from a crash. The handlers are restored on unmount.
- **A long run must never look dead.** `agents.timeout_s` defaults to 1800, so a
  dispatch can legitimately be silent for half an hour. The status line ticks
  every second with elapsed time and the cancel hint.

## Keys

| Key | Action |
|---|---|
| `F1`–`F4` | Routing mode: auto / economy / balanced / quality |
| `F5` | Refresh usage, metrics and agent health |
| `Ctrl+T` | Cycle theme |
| `Ctrl+L` | Clear the stream pane |
| `Esc` | Cancel the running task graph |
| `Ctrl+Y` | Copy the last agent reply |
| `Ctrl+S` | Copy every agent reply of the session |
| `Ctrl+C` | Copy the selection; else cancel the run; else quit |
| `Ctrl+Q` | Quit |

Textual deliberately unbinds `Ctrl+C` (it is *copy* when an `Input` has focus)
and replaces it with a notification telling you to press `Ctrl+Q`. That leaves a
long-running app with no reflexive way out, so the app rebinds it as a
`priority` binding: cancel while busy, quit while idle.

A priority binding on `Ctrl+C` also shadows Textual's own `screen.copy_text`,
which is what makes a mouse selection copyable. So `action_interrupt` checks
`screen.get_selected_text()` first and copies when there is a selection — the
cancel/quit behaviour only applies when there is nothing selected.

Selecting with the mouse copies what the pane *renders*: wrapped, styled,
interleaved with routing notes. That is the wrong artifact when the reply is a
diff or a command, so the app also keeps the raw `AgentResult.output` of every
run in `_replies` and copies it verbatim under `Ctrl+Y` (last) and `Ctrl+S`
(session). Copying goes through `App.copy_to_clipboard`, i.e. an OSC 52 escape
handled by the terminal — no X display, and it works over SSH when the terminal
allows it. Copied text never reaches the log or the database.

Bindings deliberately avoid keys an `Input` consumes, because the prompt holds
focus almost all the time. Usage, metrics and health also refresh on a
`REFRESH_SECONDS` interval.
