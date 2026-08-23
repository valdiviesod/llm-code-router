# Installation

## Requirements

- Linux
- Python 3.11+
- git
- At least one agent CLI: Claude Code (`claude`) or Antigravity (`agy`)

## Install

```bash
git clone <repo> && cd coderouter
python3 -m venv .venv
.venv/bin/pip install -e .
```

Put `router` on your PATH:

```bash
ln -s "$PWD/.venv/bin/router" ~/.local/bin/router
```

## First run

```bash
router config --init   # writes ~/.config/coderouter/config.yaml
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
rm -rf ~/.config/coderouter ~/.local/share/coderouter   # config, database, logs
```
