"""Structured JSON logging to disk; the TUI reads records, never raw stdout."""

from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path

_SECRET_KEYS = ("token", "key", "secret", "password", "authorization")


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if any(s in k.lower() for s in _SECRET_KEYS) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload["fields"] = _redact(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "v4ld1.log", maxBytes=5_000_000, backupCount=3
    )
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("v4ld1")
    root.handlers = [handler]
    root.setLevel(level)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"v4ld1.{name}")


def log(logger: logging.Logger, level: int, msg: str, **fields: object) -> None:
    logger.log(level, msg, extra={"fields": fields})
