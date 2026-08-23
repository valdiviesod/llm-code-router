"""Configuration loading and validation.

Config is a plain dataclass tree so the rest of the system never touches raw
dicts. Unknown keys are rejected loudly rather than silently ignored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from .core.models import RoutingMode
from .errors import ConfigError

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("V4LD1_CONFIG")
    or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "v4ld1" / "config.yaml"
)
DEFAULT_DATA_DIR = Path(
    os.environ.get("V4LD1_DATA_DIR")
    or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "v4ld1"
)


@dataclass(slots=True)
class AgentConfig:
    enabled: bool = True
    command: str = ""
    default_model: str | None = None
    # Model per task complexity, e.g. {"trivial": "haiku", "critical": "opus"}.
    # Keys are Complexity values; missing keys fall back to default_model, and
    # an empty mapping defers to the CLI's own default.
    model_tiers: dict[str, str] = field(default_factory=dict)
    # Limits are per-plan and change over time: never hardcoded, only configured.
    # None means "unknown" and forces UsageStatus.UNKNOWN for that window.
    window_hours: float = 5.0
    window_limit_tokens: int | None = None
    weekly_limit_tokens: int | None = None
    reserve_percent: float = 15.0
    extra_args: list[str] = field(default_factory=list)
    timeout_s: int = 1800


CLASSIFIER_MODES = ("heuristic", "auto", "llm")


@dataclass(slots=True)
class RoutingConfig:
    learning: bool = True
    forecasting: bool = True
    escalation: bool = True
    max_attempts: int = 2
    safe_threshold_percent: float = 85.0
    # How prompts are classified: "heuristic" (regex only, zero tokens),
    # "llm" (always ask a model) or "auto" (ask only when the heuristic is
    # unsure). A classification call bills ~16-24k tokens on both CLIs, so
    # "llm" is measurably more expensive than it looks; "auto" is the default.
    classifier: str = "auto"
    classifier_threshold: float = 0.7
    # Config key in AgentConfig.model_tiers naming the model to classify with.
    classifier_tier: str = "trivial"
    classifier_cache_hours: int = 168
    classifier_max_chars: int = 2000
    classifier_timeout_s: int = 120


@dataclass(slots=True)
class TokenSavingConfig:
    enabled: bool = True
    aggressive: bool = False
    max_context_files: int = 25
    max_file_bytes: int = 40_000


@dataclass(slots=True)
class ConcurrencyConfig:
    globally: int = 2
    per_agent: int = 1


@dataclass(slots=True)
class SecurityConfig:
    # SAFE runs unattended, ASK prompts, BLOCK never runs.
    ask_patterns: list[str] = field(default_factory=lambda: [
        r"\bgit\s+push\b", r"\bnpm\s+publish\b", r"\bdocker\s+", r"\bsudo\b",
    ])
    block_patterns: list[str] = field(default_factory=lambda: [
        r"\brm\s+-rf\s+/(?!\w)", r"\bDROP\s+DATABASE\b", r"\bmkfs\b",
        r":\(\)\{.*\};:",
    ])
    audit: bool = True


@dataclass(slots=True)
class Config:
    mode: RoutingMode = RoutingMode.AUTO
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    token_saving: TokenSavingConfig = field(default_factory=TokenSavingConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    data_dir: Path = DEFAULT_DATA_DIR
    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "v4ld1.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def agent(self, agent_id: str) -> AgentConfig:
        return self.agents.get(agent_id, AgentConfig())


def _build(cls: type, data: Any, path: str) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected mapping, got {type(data).__name__}")
    known = {f.name: f for f in fields(cls)}
    aliases = {"global": "globally"}
    kwargs: dict[str, Any] = {}
    for raw_key, value in data.items():
        key = aliases.get(raw_key, raw_key)
        if key not in known:
            raise ConfigError(f"{path}.{raw_key}: unknown option")
        ftype = known[key].type
        if isinstance(ftype, type) and is_dataclass(ftype):
            value = _build(ftype, value, f"{path}.{raw_key}")
        elif ftype is Path or ftype == "Path":
            value = Path(value).expanduser()
        kwargs[key] = value
    return cls(**kwargs)


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return Config()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")

    cfg = Config()
    for key, value in raw.items():
        if key == "mode":
            try:
                cfg.mode = RoutingMode(str(value).lower())
            except ValueError as exc:
                raise ConfigError(f"mode: unknown routing mode {value!r}") from exc
        elif key == "agents":
            if not isinstance(value, dict):
                raise ConfigError("agents: expected mapping of agent id -> settings")
            cfg.agents = {
                aid: _build(AgentConfig, sub, f"agents.{aid}") for aid, sub in value.items()
            }
        elif key == "routing":
            cfg.routing = _build(RoutingConfig, value, "routing")
            if cfg.routing.classifier not in CLASSIFIER_MODES:
                raise ConfigError(
                    f"routing.classifier: expected one of {', '.join(CLASSIFIER_MODES)}, "
                    f"got {cfg.routing.classifier!r}"
                )
        elif key == "token_saving":
            cfg.token_saving = _build(TokenSavingConfig, value, "token_saving")
        elif key == "concurrency":
            cfg.concurrency = _build(ConcurrencyConfig, value, "concurrency")
        elif key == "security":
            cfg.security = _build(SecurityConfig, value, "security")
        elif key == "data_dir":
            cfg.data_dir = Path(str(value)).expanduser()
        elif key == "log_level":
            cfg.log_level = str(value).upper()
        else:
            raise ConfigError(f"{key}: unknown top-level option")
    return cfg


DEFAULT_CONFIG_YAML = """\
# v4ld1 configuration. Limits are plan-specific: leave them null if unknown,
# usage will then be reported as ESTIMATED/UNKNOWN rather than invented.
mode: auto

agents:
  claude:
    enabled: true
    command: claude
    # Model per complexity tier. `classifier_tier` decides which one the
    # router itself uses to classify prompts.
    model_tiers:
      trivial: haiku
      low: haiku
      medium: sonnet
      high: sonnet
      critical: opus
    window_hours: 5
    window_limit_tokens: null
    weekly_limit_tokens: null
    reserve_percent: 15
  antigravity:
    enabled: true
    command: agy
    window_hours: 5
    window_limit_tokens: null
    weekly_limit_tokens: null
    reserve_percent: 15

routing:
  learning: true
  forecasting: true
  escalation: true
  # heuristic = regex only, zero tokens. auto = ask a model only when the
  # regex pass is unsure. llm = ask on every prompt (a classification call
  # bills roughly 16-24k tokens, so this is not the cheap option).
  classifier: auto
  classifier_threshold: 0.7
  classifier_tier: trivial

token_saving:
  enabled: true
  aggressive: false

concurrency:
  global: 2
"""


def write_default_config(path: Path | None = None) -> Path:
    path = path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(DEFAULT_CONFIG_YAML)
    return path
