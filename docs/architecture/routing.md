# Routing (`coderouter/routing/`)

**Why** Choosing an agent by hand defeats the purpose. Choosing one by a black
box is untrustworthy. So: an explainable scorer.

**Responsibility** Turn a `Task` into a `RoutingDecision` that can justify itself.

## Classifier

Rule-based regex matching over the prompt: task type, complexity
(TRIVIAL…CRITICAL), risk, and required capabilities. It is deterministic, free,
and testable. An LLM pre-pass can replace it later behind the same `classify()`
signature — that would cost tokens, which is exactly what this system exists to
save.

## Scoring

Each candidate gets a score in 0..1 from four named factors:

| Factor | Meaning |
|---|---|
| `quota` | `1 - window pressure` |
| `history` | success rate for this agent × task type (neutral prior below 5 runs) |
| `fit` | fraction of required capabilities the agent declares |
| `cost` | average tokens for this task type, normalised against peers |

Weights per mode (each column sums to 1.0):

| Mode | quota | history | fit | cost |
|---|---|---|---|---|
| ECONOMY | 0.55 | 0.20 | 0.15 | 0.10 |
| BALANCED | 0.30 | 0.30 | 0.30 | 0.10 |
| QUALITY | 0.10 | 0.35 | 0.50 | 0.05 |
| MAXIMUM | 0.05 | 0.30 | 0.60 | 0.05 |

`AUTO` is not a weight set: it resolves to ECONOMY, BALANCED or QUALITY based on
the task's own complexity.

## Model tiering

Choosing the agent is only half the decision. Without tiering every run costs
top-tier rates, trivial ones included, which is the fastest way to burn a premium
quota on work that never needed it.

`agents.<id>.model_tiers` maps a complexity level to a model id:

```yaml
model_tiers:
  trivial: haiku
  medium: sonnet
  critical: opus
```

`RoutingEngine._model_for()` then picks the cheapest model that still matches the
task. Rules:

- QUALITY and MAXIMUM skip the downgrade and always take the highest tier.
- An agent that does not declare `Capability.MODEL_SELECTION` is left on
  `default_model`.
- A missing tier key falls back to `default_model`; no tiers at all defers to the
  CLI's own default rather than guessing an id that may not exist for the account.

Measured on this machine: the same trivial task cost $0.48 on opus and $0.045 on
haiku.

## Vetoes and overrides

- A candidate whose forecast breaches the safe threshold or the reserve scores
  **0** — unless the task is CRITICAL, which may tap the reserve deliberately.
- `task.forced_agent` (from `--agent` or the TUI) always wins, and the decision
  records "user override" as the reason.
- If every candidate is vetoed, `NoViableAgent` is raised rather than silently
  overspending.

**Must not** Import an adapter module, or hardcode an agent id.

**Extending** A new strategy implements the same `decide()` signature. Machine
learning would replace the four-factor scorer, not the vetoes or the decision
shape. See [../extending/adding-router-strategy.md](../extending/adding-router-strategy.md).
