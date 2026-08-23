# ADR-006 — LLM-backed Task Classification

**Status** Accepted — 2026-08-23

## Context

The initial task classifier (`src/coderouter/routing/classifier.py`) relied purely on English-only regular expressions. While zero-cost and deterministic, this rule-based approach fails silently on prompts in other languages or prompts that imply high risk without matching explicit English keywords (e.g., `"borra la tabla de usuarios en produccion"`). A silent classification failure that marks a destructive operation as `risk=low` misleads the router into choosing lower-tier models and bypassing conservation protections.

## Decision

We introduce an LLM-backed classifier (`LLMClassifier` in `src/coderouter/routing/llm_classifier.py`) that uses subscription-backed CLI adapters to classify prompts when necessary.

### 1. Hybrid `auto` mode as the default

Calling a model for classification is not free:
- Claude Code 2.1.239 minimal `-p` invocation with `--strict-mcp-config --no-session-persistence` from an empty directory bills ~16k tokens (~31k without lean flags).
- Antigravity (`agy` 1.1.17) with `--json-schema` bills ~23.7k total tokens.

Because a classification call costs 16–24k tokens on both supported CLIs, setting `llm` on every prompt would cost more than many small tasks themselves. Thus, `auto` is the default:
- The heuristic runs first and computes an evidence-based confidence score ($0.0 \dots 1.0$).
- If `confidence >= classifier_threshold` (default `0.7`), the heuristic classification is accepted with zero token spend.
- If `confidence < classifier_threshold`, the request escalates to a model and the response is cached in SQLite (`classification_cache`) for 7 days (`classifier_cache_hours: 168`).

### 2. Provider-neutral agent selection by quota pressure

In accordance with Invariant #1 (no provider branching outside `agents/<provider>/`), the classifier does not hardcode an agent ID. Instead, it inspects all registered agents that declare `Capability.STRUCTURED_COMPLETION` and chooses the one with the lowest quota pressure. The chosen model tier uses the configured `classifier_tier` (default `"trivial"`).

### 3. Fail-safe fallback without coercion

In accordance with Invariant #2 (never invent data), any unparseable response, missing required key, or out-of-enum value (`task_type`, `complexity`, `risk`) is rejected immediately. The classifier falls back to the heuristic result rather than coercing invalid responses into plausible-looking values.

### 4. Honest accounting of classifier token spend

Classifier token spend is recorded in `usage_events` with `kind="classification"`. Queries counting tokens (such as `tokens_since`) aggregate across all kinds, ensuring the router's quota tracking accounts for the tokens burned during classification.

## Consequences

- Multilingual and nuanced prompts are accurately classified with correct risk and complexity rankings.
- Prompts that match clear English heuristics remain zero-cost.
- Adding a new agent adapter with `Capability.STRUCTURED_COMPLETION` automatically enables it to participate in classification without modifying the classifier.
- Classification cache avoids repeated spend on identical prompts.
