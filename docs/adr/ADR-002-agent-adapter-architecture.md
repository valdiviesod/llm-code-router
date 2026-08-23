# ADR-002 — Agent adapter architecture

**Status** Accepted — 2026-08-22

## Context

The product ships with two agents and is expected to grow to ten or more (Codex,
Gemini, Qwen, DeepSeek, raw provider APIs, Ollama, OpenRouter, local models). The
failure mode to avoid is obvious and common: `if agent == "claude"` spreading
through the router, the scheduler, the TUI and the schema until adding an agent
means touching every file.

## Decision

A single abstract `AgentAdapter` plus an `AgentRegistry` that discovers adapters
from `coderouter.agents.*` packages and from the `coderouter.agents` entry-point group.
Behavioural differences are expressed as `Capability` enum values that adapters
declare and the router consumes.

## Consequences

- The core, router, scheduler, TUI and storage never name a concrete agent.
- Adding an agent is one directory plus a row in the contract-test parameters.
- Out-of-tree plugins work without vendoring anything.
- Capabilities must be declared honestly; an overclaimed capability sends an
  agent work it will fail. The contract tests check that the declaration exists,
  not that it is true — that part is on the adapter author.
- Some agent-specific nuance (Antigravity's Go-style `--print=` flag) is
  confined to its adapter, where it belongs.
