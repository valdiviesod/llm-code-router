# Taste — Workflow

- Defines "deployed" as the app containing all changes and everything pushed to GitHub; deployment means committing/pushing all changes to GitHub. Confidence: 0.8
- Strongly prefers project-local, portable configuration: never modify global configs (agy/Claude Code), keep everything inside the project, use relative symlinks and no absolute paths. Confidence: 0.9
- Prefers a single canonical source of truth with symlinks over duplicated files (e.g., `.agents/skills/` as source, `.claude/skills/` as symlinks — no copies of SKILL.md). Confidence: 0.9
- Wants inspection before modifying, validation after changes (symlinks, realpath, broken links), and a final structured report (tables, totals, conflicts/errors). Confidence: 0.7
- Prefers an LLM-based classifier/router over hand-written prompt heuristics, choosing between available models based on usage to save tokens (subscription CLI only). Confidence: 0.6
- Prefers incremental implementation: audit the codebase first, preserve working functionality, extend existing sound abstractions, and replace only when necessary with documented reasons. Confidence: 0.8
- Demands backward compatibility unless a breaking change is explicitly justified and documented. Confidence: 0.8
- Optimizes for maximum engineering value per unit of token/subscription capacity — not just monetary cost, but also quota conservation, quality, reliability, and observability. Confidence: 0.7
