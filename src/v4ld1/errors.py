"""Typed error hierarchy. Every failure the orchestrator can produce is one of these."""


class V4ld1Error(Exception):
    """Base for all v4ld1 errors."""


class ConfigError(V4ld1Error):
    """Configuration missing, malformed, or invalid."""


class AgentUnavailable(V4ld1Error):
    """Adapter cannot run: binary missing, not authenticated, or health check failed."""


class AgentExecutionError(V4ld1Error):
    """Adapter ran but the underlying CLI failed."""

    def __init__(self, message: str, *, exit_code: int | None = None, stderr: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


class NoViableAgent(V4ld1Error):
    """Router found no agent able to take the task under current constraints."""


class QuotaExceeded(V4ld1Error):
    """Projected usage would breach the configured limit or reserve."""
