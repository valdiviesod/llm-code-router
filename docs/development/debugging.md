# Debugging

## Logs

Structured JSON, one record per line, at `$CODEROUTER_DATA_DIR/logs/coderouter.log`
(default `~/.local/share/coderouter/logs/`), rotating at 5 MB.

```bash
tail -f ~/.local/share/coderouter/logs/coderouter.log | jq .
jq 'select(.level=="ERROR")' ~/.local/share/coderouter/logs/coderouter.log
```

Set `log_level: DEBUG` in the config for more. Secrets are redacted by the
formatter itself, so logs are safe to paste — but read them first.

## Inspecting decisions

Everything is in SQLite:

```bash
sqlite3 ~/.local/share/coderouter/coderouter.db \
  "SELECT created_at, selected_agent, confidence, reason FROM routing_decisions
   ORDER BY created_at DESC LIMIT 10;"
```

Useful tables: `routing_decisions`, `runs`, `usage_events`, `agent_stats`,
`validations`, `audit_log`.

## Reproducing an agent call

Every adapter builds its argv in `_argv()`. Log it, then run the same command by
hand — that isolates "the CLI failed" from "coderouter drove it wrong".

## The TUI eats my errors

It does not: exceptions from a run are written to the stream panel in red, and
the full record is in the log file. Press F5 to refresh panels.
