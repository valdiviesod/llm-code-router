"""Typed error hierarchy. Every failure the orchestrator can produce is one of these."""


class CodeRouterError(Exception):
    """Base for all CodeRouter errors."""


V4ld1Error = CodeRouterError  # Backward compatibility alias
RouterError = CodeRouterError


class ConfigError(CodeRouterError):
    """Configuration missing, malformed, or invalid."""


class AgentUnavailable(CodeRouterError):
    """Adapter cannot run: binary missing, not authenticated, or health check failed."""


class AgentExecutionError(CodeRouterError):
    """Adapter ran but the underlying CLI failed."""

    def __init__(self, message: str, *, exit_code: int | None = None, stderr: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


class NoViableAgent(CodeRouterError):
    """Router found no agent able to take the task under current constraints."""


class QuotaExceeded(CodeRouterError):
    """Projected usage would breach the configured limit or reserve."""
