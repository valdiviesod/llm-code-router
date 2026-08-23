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

Put `v4ld1` on your PATH:

```bash
ln -s "$PWD/.venv/bin/v4ld1" ~/.local/bin/v4ld1
```

## First run

```bash
v4ld1 config --init   # writes ~/.config/v4ld1/config.yaml
v4ld1 doctor          # verifies everything and explains what is missing
```

`doctor` exits non-zero if anything needs attention, so it works in CI too.

## Verify

```bash
cd your-project
v4ld1 "explain what this project does, change no files"
```

## Uninstall

```bash
rm ~/.local/bin/v4ld1
rm -rf ~/.config/v4ld1 ~/.local/share/v4ld1   # config, database, logs
```
