# Configuration

Default location `~/.config/coderouter/config.yaml`, overridable with `--config` or
`$CODEROUTER_CONFIG`. Unknown keys are a hard error, not a silent ignore.

```yaml
mode: auto            # auto | economy | balanced | quality | maximum

agents:
  claude:
    enabled: true
    command: claude
    default_model: null       # null defers to the CLI's own default
    model_tiers:              # cheapest model that matches the task
      trivial: haiku
      low: haiku
      medium: sonnet
      high: opus
      critical: opus
    window_hours: 5
    window_limit_tokens: null # your plan's limit; null means UNKNOWN
    weekly_limit_tokens: null
    reserve_percent: 15
    timeout_s: 1800
    extra_args: []
  antigravity:
    enabled: true
    command: agy

routing:
  learning: true
  forecasting: true
  escalation: true
  max_attempts: 2
  safe_threshold_percent: 85

token_saving:
  enabled: true
  aggressive: false
  max_context_files: 25
  max_file_bytes: 40000

concurrency:
  global: 2
  per_agent: 1

security:
  ask_patterns: ['\bgit\s+push\b', '\bsudo\b']
  block_patterns: ['\brm\s+-rf\s+/(?!\w)', '\bDROP\s+DATABASE\b']
  audit: true

log_level: INFO
```

## Usage limits

`window_limit_tokens` and `weekly_limit_tokens` are **your plan's** limits. They
are not shipped as defaults because they are plan-specific and they change. Leave
them `null` and usage is reported as UNKNOWN; set them and it becomes ESTIMATED,
which is what enables forecasting, reserves and conservation mode.

## Model tiers

The single biggest lever for making a premium quota last. Keys are complexity
levels; values are model ids your CLI accepts (`claude --help` lists its aliases,
`agy models` lists real ids). QUALITY and MAXIMUM modes ignore the downgrade and
always take the top tier. Omit the block to let the CLI choose.

Asymmetric reserves pair well with this: give the premium agent a larger
`reserve_percent` so it stays available for the work that actually needs it,
and a smaller one to the agent meant to absorb bulk implementation.

## Reserves

`reserve_percent` is quota the router refuses to spend on ordinary work. It is
released only for CRITICAL tasks, an explicit `--agent` override, or when there is
no viable alternative.

## Modes

| Mode | Behaviour |
|---|---|
| `auto` | Resolves per task: trivial → economy, critical → quality, else balanced |
| `economy` | Protects quota above all |
| `balanced` | Even weighting |
| `quality` | Prioritises capability fit and track record |
| `maximum` | Quality first; allows multi-agent decomposition and review |
