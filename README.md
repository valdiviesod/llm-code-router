# ⚡ CodeRouter

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status: MVP v0.1.0](https://img.shields.io/badge/status-v0.1.0%20MVP-orange.svg)](#-project-status--limitations)
[![Architecture](https://img.shields.io/badge/architecture-modular%20adapters-purple.svg)](ARCHITECTURE.md)

An intelligent, local AI coding agent orchestrator and router for Linux. It sits between you and your AI coding agents, evaluates the complexity and risk of your prompt, decomposes high-complexity tasks, routes subtasks to the most cost-effective capable agent, executes parallel tasks inside isolated Git worktrees, validates results against your test suite, and tracks token consumption over time.

---

> ### ⚠️ Project Status: Early MVP (v0.1.0) & Known Limitations
>
> **coderouter is currently in an early development stage (initial MVP).** While the core routing engine, heuristics, Git worktree manager, security boundary, and Textual TUI are functional, there are several foundational features still in progress:
>
> 1. **Tool Calling & Function Calling for Routed LLMs:**
>    - Current execution relies primarily on autonomous CLI agents ([Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) and [Antigravity CLI](https://github.com/google/antigravity)) running non-interactively in sub-processes.
>    - The router does **not yet provide a generic bidirectional Tool Calling / Function Calling harness** for raw LLM completions or direct API models (e.g. OpenAI, DeepSeek, Ollama, local models).
>    - Full [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server/client bridging and interactive tool loops for custom routed models are actively under design for upcoming releases.
> 2. **Telemetry & Quota Estimation:**
>    - CLI agent providers do not expose real-time subscription quotas via machine-readable APIs. Quota windows and pressures are calculated using **local estimations** tracked in SQLite based on token counts from runs executed through coderouter.
> 3. **Context Selection:**
>    - Context selection uses heuristic file ranking and fingerprinting; semantic vector indexing is planned for future milestones.
>
> Contributions, feedback, and issue reports are welcome as we expand capabilities!

---

```
$ router "implement JWT authentication and refresh token rotation"

complexity=high risk=medium type=security context_files=8
decomposed into 3 subtasks:
  [routed] architecture design → claude (deep reasoning required, quota pressure low)
  [routed] token handlers & auth middleware → antigravity
  [routed] unit & integration tests → antigravity
[isolated] 2 parallel worktrees spawned under .worktrees/
[validated] ruff: PASS | pytest: 24 passed in 1.4s
```

---

## 🎯 The Philosophy

The goal is not to blindly throw the biggest, most expensive frontier model at every single prompt.

The goal is **maximum engineering quality per unit of token/quota spend**.

coderouter picks the leanest, cheapest model or agent that is genuinely capable of solving the task at hand, escalating to deep-reasoning frontier models only when the task's complexity, architectural scope, or security risk justifies the spend.

---

## ✨ Key Features

- **🧠 Multi-Agent Intelligent Routing:** Dynamically scores available agents using a weighted formula taking into account capabilities, model tiers, task risk, historical success rate, and active quota pressure.
- **⚡ Dual Classification Engine:** Zero-token instant heuristic regex classification for common tasks, with seamless fallback to structured LLM classification when ambiguous.
- **🌲 Git Worktree Isolation:** Sibling subtasks run concurrently in dedicated Git worktrees (`.worktrees/task_<id>`), ensuring agents never overwrite each other's working trees.
- **🛡️ Built-in Security & Secret Redaction:** Strict command policy (`SAFE`, `ASK`, `BLOCK`) preventing destructive commands (`rm -rf /`, `mkfs`, raw SQL drops) and automatic redaction of API keys, bearer tokens, and credentials from all logs and databases.
- **🔍 Stack Detection & Automatic Validation:** Detects your project stack (Python, Node, Go, Rust) and automatically triggers linters (`ruff`, `eslint`, `golangci-lint`) and test runners (`pytest`, `npm test`, `go test`) on changed diffs.
- **📊 Local SQLite Persistence & Usage Ledger:** Stores complete execution history, audit logs, and per-agent token metrics. Automatically triggers **Quota Conservation Mode** when quota ceilings are approached.
- **💻 OpenCode-Inspired TUI & Rich CLI:** Built on Textual with clean terminal aesthetics, live log streaming, interactive widget dashboards, copyable agent output, and hotkey mode switching (`F1`-`F5`).

---

## 🏗️ Architecture

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

Read the full architecture breakdown in [ARCHITECTURE.md](ARCHITECTURE.md) and [docs/architecture/](docs/architecture/).

---

## 🚀 Quickstart

### Prerequisites

- **Linux** (x86_64 or aarch64)
- **Python 3.11+**
- **Git**
- At least one supported coding agent CLI installed and authenticated:
  - [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) (`claude`)
  - [Antigravity CLI](https://github.com/google/antigravity) (`agy`)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/valdiviesod/coderouter.git
cd coderouter

# 2. Set up virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Initialize default configuration
router config --init

# 4. Verify your environment
router doctor
```

`router doctor` inspects installed agent binaries, model configurations, database connectivity, and limits, reporting actionable guidance for any missing dependencies.

---

## 💻 Usage

### Interactive TUI

Launch the full-screen terminal interface:

```bash
router
```

#### TUI Keyboard Shortcuts

| Key | Action |
|---|---|
| `F1` | Reset mode to **AUTO** |
| `F2` | Force **ECONOMY** mode (prefers fast/cheap tiers) |
| `F3` | Force **BALANCED** mode |
| `F4` | Force **QUALITY** mode |
| `F5` | Force **MAXIMUM** mode (always picks flagship frontier tier) |
| `Ctrl+C` / `q` | Quit |

### Command Line Interface (CLI)

```bash
# Run a single task through the router
router "fix typo in database connection timeout"

# Force a specific routing mode
router --mode economy "generate unit tests for user service"

# Force a specific agent adapter
router --agent claude "refactor orchestrator state machine"

# Inspect system status and token usage
router status

# List registered agents, health, and declared capabilities
router agents

# View token consumption and estimated quota windows
router usage

# Run diagnostic health check
router doctor

# View or reset configuration
router config --init
```

---

## ⚙️ Configuration

Configuration is stored in `~/.config/coderouter/config.yaml` (or locally via `.coderouter.yaml`):

```yaml
general:
  default_mode: auto          # auto | economy | balanced | quality | maximum
  worktree_dir: .worktrees
  db_path: ~/.local/share/coderouter/coderouter.db

agents:
  claude:
    enabled: true
    command: claude
    default_model: sonnet
    model_tiers:
      flagship: opus
      standard: sonnet
      fast: haiku
    window_limit_tokens: 500000    # Optional estimation ceiling (e.g. 5h window)
    reserve_percent: 15

  antigravity:
    enabled: true
    command: agy
    default_model: gemini-2.5-pro
    model_tiers:
      flagship: gemini-2.5-pro
      standard: gemini-2.5-flash
      fast: gemini-2.5-flash-lite
    window_limit_tokens: 1000000
    reserve_percent: 10

routing:
  classifier: auto            # heuristic | auto
  classifier_threshold: 0.85
  cost_weight: 0.3
  performance_weight: 0.3
  quota_weight: 0.4

security:
  policy: strict              # strict | standard | permissive
  require_confirmation_for:
    - git_push
    - docker
    - sudo
```

---

## 🔌 Extending: Adding New Agents

Adding support for another agent CLI or provider requires writing a single adapter in `src/coderouter/agents/<name>/adapter.py` that implements `AgentAdapter`:

```python
from coderouter.agents.base.adapter import AgentAdapter
from coderouter.agents.base.registry import register
from coderouter.core.models import AgentCapabilities, Capability, AgentResult, Task

@register
class MyCustomAgentAdapter(AgentAdapter):
    @property
    def id(self) -> str:
        return "custom_agent"

    @property
    def display_name(self) -> str:
        return "My Custom Agent"

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(capabilities=frozenset({
            Capability.CODE_EDIT,
            Capability.SHELL,
            Capability.STRUCTURED_COMPLETION,
        }))

    async def execute(self, task: Task) -> AgentResult:
        # Translate provider-neutral Task into CLI invocation or API call
        ...
```

No changes to the router, orchestrator, TUI, or database are required. See [docs/extending/adding-agent.md](docs/extending/adding-agent.md).

---

## 🗺️ Roadmap

- [ ] **Universal Tool Calling & Function Calling:** Native MCP-compliant tool execution loop for raw LLM routing (OpenAI, DeepSeek, Ollama, OpenRouter).
- [ ] **Interactive Multi-Turn Tool Bridge:** Bidirectional tool execution and file-system sandbox for direct API providers.
- [ ] **Additional Adapters:** Codex, OpenAI API, DeepSeek, Qwen 2.5 Coder, Ollama (local offline models).
- [ ] **Collaborative Multi-Agent Debates:** Cross-agent peer review loops where one agent reviews or red-teams code produced by another.
- [ ] **Vector-based Context Selection:** Semantic indexing of project repositories with AST-aware context slicing.
- [ ] **Live Token & Cost Streaming:** Real-time token generation telemetry graphs in the Textual TUI.

---

## 🔒 Security

coderouter is built with defense-in-depth:
- Dangerous shell patterns are hard-blocked by `security/policy.py`.
- Secrets, tokens, and authorization headers are scrubbed from outputs prior to logging or persistence.
- Review our full security model in [SECURITY.md](SECURITY.md).

---

## 🤝 Contributing

Contributions are welcome! Please check out [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/development/setup.md](docs/development/setup.md) for local development setup and guidelines.

To run tests and linters:

```bash
ruff check .
mypy src/coderouter --ignore-missing-imports
pytest
```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

