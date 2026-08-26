"""Validation public surface.

This module is the seam the orchestrator and tests import from. The actual
testing logic lives in `validation.testing`; the optional review engine
lives in `validation.review`. Splitting them keeps a single-responsibility
module per concern and lets the review engine evolve independently.
"""

from __future__ import annotations

from .testing import CheckResult, Stack, TestingEngine, detect_stacks

__all__ = ["CheckResult", "Stack", "TestingEngine", "detect_stacks"]


class ValidationEngine(TestingEngine):
    """Backwards-compatible alias.

    Every existing caller imports `ValidationEngine`; the testing-only refactor
    must not break them. New callers should prefer `TestingEngine` directly.
    """
