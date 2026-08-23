# Adding a routing strategy

The router is a weighted scorer on purpose: every contribution is named, so a
decision can explain itself in the TUI.

## Adjusting weights

Add or edit an entry in `WEIGHTS` in `routing/engine.py`. Each column must sum to
1.0 — there is a test that enforces it.

## Adding a factor

1. Write a `_factor(self, adapter, task) -> tuple[float, str]` method returning a
   0..1 score and a human-readable reason.
2. Add its weight to every mode in `WEIGHTS` and rebalance so they still sum to 1.
3. Append its reason string to `Candidate.reasons` so the Routing view shows it.

## Replacing the scorer entirely

Implement a class with the same `decide(task, *, mode_override) -> RoutingDecision`
signature and inject it into `Orchestrator`. Keep these invariants:

- Capability filtering happens before scoring.
- The quota forecast veto still applies (CRITICAL tasks may tap the reserve).
- `task.forced_agent` still wins.
- `NoViableAgent` is raised rather than overspending.

An ML ranker replaces the four-factor scorer only. The vetoes and the decision
shape are safety properties, not heuristics.
