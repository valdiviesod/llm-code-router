# Orchestrator (`v4ld1/core/`)

**Why** Somebody has to own the end-to-end flow. If that logic leaks into the TUI
or the adapters, every new agent or new front end has to re-implement it.

**Responsibility** Analyse a prompt, plan it, route it, execute it, validate the
result, persist everything, and escalate on failure.

**Depends on** `AgentRegistry` (interface only), `RoutingEngine`, `UsageManager`,
`ContextManager`, `ValidationEngine`, `WorktreeManager`, `Database`.

**Must not**
- Import any concrete adapter module.
- Contain provider-specific branching.
- Render anything. It emits typed events; the TUI decides how to show them.

**Key types** `Task`, `TaskOutcome`, `HandoffPackage`, `TaskGraph`.

**Escalation** On failure the orchestrator builds a `HandoffPackage` (objective,
files changed, findings, test results, errors, remaining work, risk) and retries
once on a *different* agent. The identical prompt is never re-sent — the second
attempt always knows what the first one did and why it failed.

**Extending** New pipeline stages go in `run_task`. New decomposition strategies
go in `core/task_graph.py`, behind `build_graph()`.
