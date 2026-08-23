# Architecture

```
                       coderouter CLI / TUI
                              │
                     ┌────────▼────────┐
                     │   Orchestrator  │   analyse → plan → route →
                     │  (core/)        │   execute → validate → learn
                     └────────┬────────┘
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        RoutingEngine     TaskGraph      UsageManager
        (routing/)        (core/)        (usage/)
              │               │               │
              └───────────────┼───────────────┘
                     ┌────────▼────────┐
                     │  AgentRegistry  │  ← plugins, entry points
                     └────────┬────────┘
                 ┌────────────┴────────────┐
                 ▼                         ▼
          ClaudeCodeAdapter          AntigravityAdapter
                 │                         │
             claude CLI                  agy CLI

  Cross-cutting: ContextManager (context/), ValidationEngine (validation/),
  WorktreeManager (git/), CommandPolicy (security/), Database (storage/)
```

## Layer rules

| Layer | May depend on | Must never |
|---|---|---|
| `core/` | models, registry interface | know any concrete agent |
| `routing/` | core models, registry, usage, storage | import an adapter module |
| `agents/<x>/` | `agents/base`, core models | import routing, tui, orchestrator |
| `tui/` | orchestrator, models | compute business logic |
| `storage/` | nothing in coderouter | know about agents |

The single enforcement rule: **no `if agent == "claude"` anywhere outside
`agents/claude/`.** Behaviour differences are expressed as `Capability` values
that adapters declare and the router reads. Adapters execute tasks (`execute()`)
and may optionally answer out-of-band structured questions (`complete()`) if they declare
`Capability.STRUCTURED_COMPLETION`.

## Request lifecycle

1. **Classify** (`routing/classifier.py`, `routing/llm_classifier.py`) — rule-based
   heuristic with evidence scoring, escalating to an LLM via `complete()` when
   uncertain (`auto` mode): task type, complexity, risk, required capabilities.
2. **Select context** (`context/manager.py`) — rank project files by relevance,
   fingerprint the selection so identical context can be reused.
3. **Plan** (`core/task_graph.py`) — decompose only if complexity is HIGH or
   above; each subtask is another agent run and another chunk of quota.
4. **Route** (`routing/engine.py`) — weighted scoring over quota, history, fit
   and cost, with a hard quota veto.
5. **Execute** (`agents/…`) — subprocess, isolated in a git worktree when
   siblings run in parallel.
6. **Validate** (`validation/engine.py`) — only checks the detected stack
   supports, only when the tree actually changed.
7. **Learn** (`storage/db.py`) — per agent × task type × complexity counters that
   feed the next routing decision.
8. **Escalate** (`core/orchestrator.py`) — on failure, retry once on a *different*
   agent with a compact `HandoffPackage`, never the same prompt twice.

## Component docs

`docs/architecture/` covers, for each component: why it exists, its
responsibility, its dependencies, what it must **not** do, and how to extend it.
