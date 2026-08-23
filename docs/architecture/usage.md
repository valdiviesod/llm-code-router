# UsageManager (`v4ld1/usage/`)

**Why** Neither Claude Code nor the Antigravity CLI exposes subscription quota.
Pretending otherwise would be the single most damaging thing this tool could do,
because every routing decision depends on it.

**Responsibility** Account for the tokens v4ld1 itself spent, in a rolling
window, and compare against limits the user configured.

## Provenance is part of the data

| Status | Meaning |
|---|---|
| `CONFIRMED` | The provider reported it. |
| `ESTIMATED` | v4ld1 summed its own runs against a user-configured limit. |
| `UNKNOWN` | No limit configured, so no fraction can be computed. |

The TUI and `v4ld1 usage` always print this tag. Limits are never hardcoded —
they are plan-specific and change. `v4ld1 doctor` warns when they are unset.

## Forecasting

```
projected = (window_used + task_estimate) / window_limit
usable    = min(safe_threshold, 1 - reserve_percent)
safe      = projected <= usable
```

An unsafe forecast vetoes the candidate. The reserve is only spent on CRITICAL
tasks or an explicit user override.

## Conservation mode

When **every** agent with a known limit is at or above 75% of its window, the
decision is flagged `conservation` and the TUI shows QUOTA CONSERVATION MODE.

**Must not** Report an estimate as if it were a provider figure. Sum rows in
Python — the totals come from SQL `SUM` over `usage_events`.

**Extending** If an agent ever gains a real quota API, implement `get_usage()` in
its adapter and return `CONFIRMED`; the manager prefers it automatically.
