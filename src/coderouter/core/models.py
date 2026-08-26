"""Provider-neutral domain types.

Nothing in this module may reference a concrete agent. The core, router,
scheduler, storage and TUI all speak these types; adapters translate them to
and from whatever their CLI happens to understand.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path


def _now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Complexity(str, Enum):
    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return list(Complexity).index(self)


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RoutingMode(str, Enum):
    AUTO = "auto"
    ECONOMY = "economy"
    BALANCED = "balanced"
    QUALITY = "quality"
    MAXIMUM = "maximum"


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UsageStatus(str, Enum):
    """Whether a usage figure came from the provider or from our own estimator."""

    CONFIRMED = "confirmed"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class Capability(str, Enum):
    """Things an agent can do. Adapters declare these; the router requires them."""

    CODE_EDIT = "code_edit"
    SHELL = "shell"
    LONG_CONTEXT = "long_context"
    DEEP_REASONING = "deep_reasoning"
    PLANNING = "planning"
    REVIEW = "review"
    MCP = "mcp"
    STREAMING = "streaming"
    CANCELLATION = "cancellation"
    MODEL_SELECTION = "model_selection"
    USAGE_REPORTING = "usage_reporting"
    # Can answer a short prompt as text/JSON without acting on the repository.
    # This is what makes an agent usable as the router's own classifier.
    STRUCTURED_COMPLETION = "structured_completion"


# Task types are open on purpose: the classifier may emit anything, these are
# only the well-known values used for statistics and routing hints.
TASK_TYPES = (
    "bug_fix", "feature", "refactor", "architecture", "research", "testing",
    "review", "documentation", "debugging", "frontend", "backend", "database",
    "devops", "security", "general",
)


@dataclass(slots=True)
class ModelInfo:
    id: str
    name: str
    agent_id: str
    supports_reasoning_effort: bool = False


@dataclass(slots=True)
class AgentCapabilities:
    capabilities: frozenset[Capability]
    max_context_tokens: int | None = None
    supports_worktrees: bool = True

    def has(self, *needed: Capability) -> bool:
        return all(c in self.capabilities for c in needed)


@dataclass(slots=True)
class Completion:
    """A one-shot answer to a question coderouter asked, not work done on a repo.
    Used by the LLMClassifier to classify tasks.
    Carries its own token counts because the call spends real subscription
    quota: anything coderouter spends on itself has to show up in the same
    accounting as the work it routes, or the usage figures become a lie.
    """

    text: str
    agent_id: str
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    # Populated only when the provider enforced a schema on its own output.
    structured: dict | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class HealthStatus:
    agent_id: str
    healthy: bool
    detail: str = ""
    version: str | None = None
    remediation: str = ""


@dataclass(slots=True)
class UsageEstimate:
    """What we think a task will cost, before running it."""

    input_tokens: int
    output_tokens: int
    confidence: float = 0.5

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class UsageWindow:
    label: str                 # e.g. "5h" or "weekly"
    used_tokens: int
    limit_tokens: int | None
    window_start: datetime
    window_end: datetime
    status: UsageStatus

    @property
    def fraction(self) -> float | None:
        if not self.limit_tokens:
            return None
        return min(self.used_tokens / self.limit_tokens, 1.0)

    @property
    def remaining_tokens(self) -> int | None:
        if self.limit_tokens is None:
            return None
        return max(self.limit_tokens - self.used_tokens, 0)


@dataclass(slots=True)
class UsageInfo:
    agent_id: str
    windows: list[UsageWindow]
    status: UsageStatus

    def window(self, label: str) -> UsageWindow | None:
        return next((w for w in self.windows if w.label == label), None)


@dataclass(slots=True)
class Task:
    prompt: str
    id: str = field(default_factory=lambda: new_id("task"))
    parent_id: str | None = None
    project_root: Path = field(default_factory=Path.cwd)
    task_type: str = "general"
    complexity: Complexity = Complexity.MEDIUM
    risk: Risk = Risk.LOW
    required_capabilities: frozenset[Capability] = frozenset({Capability.CODE_EDIT})
    context_files: list[Path] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    state: TaskState = TaskState.PENDING
    forced_agent: str | None = None
    handoff: HandoffPackage | None = None
    attempt: int = 0
    created_at: datetime = field(default_factory=_now)
    classification_source: str = "heuristic"
    classification_confidence: float = 0.5
    # Skills selected for this task by SkillInjector. Set on the task by the
    # orchestrator before the agent call so the adapter and the audit log can
    # see exactly which guidance was applied.
    skill_ids: list[str] = field(default_factory=list)
    # Tools the agent may call for this task. Populated by ToolSelector.
    selected_tool_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolCall:
    name: str
    detail: str = ""


@dataclass(slots=True)
class AgentResult:
    task_id: str
    agent_id: str
    model: str | None
    success: bool
    output: str
    files_changed: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    usage_status: UsageStatus = UsageStatus.ESTIMATED
    duration_s: float = 0.0
    cost_usd: float | None = None
    error: str | None = None
    session_id: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class Candidate:
    agent_id: str
    score: float
    model: str | None
    reasons: list[str] = field(default_factory=list)
    estimate: UsageEstimate | None = None


@dataclass(slots=True)
class RoutingDecision:
    task_id: str
    selected_agent: str
    selected_model: str | None
    reason: str
    alternatives: list[Candidate]
    confidence: float
    estimated_usage: UsageEstimate
    risk: Risk
    mode: RoutingMode
    conservation: bool = False
    quota_pool: str | None = None


@dataclass(slots=True)
class HandoffPackage:
    """Compact baton between agents. Never the full conversation."""

    objective: str
    decisions: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    tests: str = ""
    errors: list[str] = field(default_factory=list)
    remaining_work: list[str] = field(default_factory=list)
    risks: str = ""
    recommendations: list[str] = field(default_factory=list)

    def render(self, max_items: int = 5) -> str:
        def block(title: str, items: list[str]) -> str:
            if not items:
                return ""
            body = "\n".join(f"- {i}" for i in items[:max_items])
            return f"{title}:\n{body}\n\n"

        parts = [f"Task:\n{self.objective}\n\n"]
        parts.append(block("Decisions", self.decisions))
        parts.append(block("Files changed", self.files_changed))
        parts.append(block("Findings", self.findings))
        if self.tests:
            parts.append(f"Tests:\n{self.tests}\n\n")
        parts.append(block("Errors", self.errors))
        parts.append(block("Remaining", self.remaining_work))
        if self.risks:
            parts.append(f"Risk:\n{self.risks}\n\n")
        parts.append(block("Recommendations", self.recommendations))
        return "".join(p for p in parts if p).strip()
