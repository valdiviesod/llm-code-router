# Taste — Workflow

- Defines "deployed" as the app containing all changes and everything pushed to GitHub; deployment means committing/pushing all changes to GitHub. Confidence: 0.8
- Strongly prefers project-local, portable configuration: never modify global configs (agy/Claude Code), keep everything inside the project, use relative symlinks and no absolute paths. Confidence: 0.9
- Prefers a single canonical source of truth with symlinks over duplicated files (e.g., `.agents/skills/` as source, `.claude/skills/` as symlinks — no copies of SKILL.md). Confidence: 0.9
- Wants inspection before modifying, validation after changes (symlinks, realpath, broken links), and a final structured report (tables, totals, conflicts/errors). Confidence: 0.7
- Prefers an LLM-based classifier/router over hand-written prompt heuristics, choosing between available models based on usage to save tokens (subscription CLI only). Confidence: 0.6
- Prefers incremental implementation: audit the codebase first, preserve working functionality, extend existing sound abstractions, and replace only when necessary with documented reasons. Confidence: 0.8
- Demands backward compatibility unless a breaking change is explicitly justified and documented. Confidence: 0.8
- Optimizes for maximum engineering value per unit of token/subscription capacity — not just monetary cost, but also quota conservation, quality, reliability, and observability. Confidence: 0.7
- Audit-first workflow: build a dependency graph and fill an audit matrix (Exists/Correct/Tested/Production-ready/Action) before modifying anything. Confidence: 0.8
- Failure injection testing is mandatory: wants adversarial tests for timeouts, quota exhaustion, rate limits, invalid credentials, network loss, race conditions, concurrent reservations, worktree conflicts, and process crashes. Confidence: 0.8
- Real execution validation required — "do not stop at static analysis"; must actually run the system end-to-end (install, CLI, doctor, routing, dry-run, tasks, tests) before declaring anything complete. Confidence: 0.9
- Documentation must describe actual behavior only — "never document features that are not implemented"; wants comprehensive docs (architecture, configuration, providers, agents, tools, security, quotas, routing, troubleshooting, development). Confidence: 0.8
