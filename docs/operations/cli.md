# CLI reference

The console command is `router` (`coderouter` is an alias).

```
router "<prompt>"          run a task
router                     open the TUI
```

## Inspecting before spending

| Command | What it does |
|---|---|
| `router plan "<prompt>"` | **Dry run.** Classification, task graph, the routing decision per task with its named score contributions, rejected candidates and why, token estimate, parallelism. Executes nothing. |
| `router explain <task-id>` | Why a past task was routed that way, what was rejected, and whether each run's work was actually integrated. |

`plan` is the debugging surface for the router. One honest caveat: with
`classification.mode: llm` or `auto`, classifying the prompt is itself a model
call and does spend quota. That spend is recorded like any other.

## Inspecting the installation

| Command | What it does |
|---|---|
| `router doctor` | Probes the install: agents, auth, models, config, database and schema version, worktree creation, leftover worktrees and unmerged branches, skills, tools, command policy, and an MCP handshake per configured server. |
| `router agents` | Agents, health and capabilities. |
| `router models` | The models each agent reports, tagged with the configured tier. |
| `router skills` | Discovered skills. |
| `router tools` | Registered tools with kind and risk. |
| `router mcp [--probe]` | Configured MCP servers; `--probe` connects and lists their tools. |

## Usage and history

| Command | What it does |
|---|---|
| `router usage` | Usage windows per agent, with the provenance tag. |
| `router quota` | Quota pools: spent, reserved by runs in flight, remaining. |
| `router status` | Aggregate metrics. |
| `router history [-n N]` | Recent runs, showing whether each landed or was stranded. |

An unknown limit is reported as `UNKNOWN`, never as a percentage. A truncated
history page says so and is not summed — see
[ADR-005](../adr/ADR-005-usage-estimation.md).

## Configuration and memory

| Command | What it does |
|---|---|
| `router config [--init]` | Show, or write, the config file. |
| `router memory list\|add\|forget` | Project-scoped notes. |

## Global options

| Option | Applies to |
|---|---|
| `--config PATH` | every command |
| `--mode economy\|balanced\|quality\|maximum` | `run`, `plan` |
| `--agent ID` | `run`, `plan` |
