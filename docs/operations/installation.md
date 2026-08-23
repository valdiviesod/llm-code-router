# Installation

## Requirements

- Linux
- Python 3.11+
- git
- At least one agent CLI: Claude Code (`claude`) or Antigravity (`agy`)

## Install

```bash
git clone <repo> && cd v4ld1
python3 -m venv .venv
.venv/bin/pip install -e .
```

Put `router` on your PATH:

```bash
ln -s "$PWD/.venv/bin/router" ~/.local/bin/router
```

## First run

```bash
router config --init   # writes ~/.config/v4ld1/config.yaml
router doctor          # verifies everything and explains what is missing
```

`doctor` exits non-zero if anything needs attention, so it works in CI too.

## Verify

```bash
cd your-project
router "explain what this project does, change no files"
```

## Uninstall

```bash
rm ~/.local/bin/router
rm -rf ~/.config/v4ld1 ~/.local/share/v4ld1   # config, database, logs
```
