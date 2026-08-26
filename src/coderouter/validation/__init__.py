"""Validation engines: testing (always) + review (opt-in)."""

from .testing import CheckResult, Stack, TestingEngine, detect_stacks

__all__ = ["CheckResult", "Stack", "TestingEngine", "detect_stacks"]
