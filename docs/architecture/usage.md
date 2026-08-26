# UsageManager (`coderouter/usage/`)

**Why** Neither Claude Code nor the Antigravity CLI exposes subscription quota.
Pretending otherwise would be the single most damaging thing this tool could do,
because every routing decision depends on it.

**Responsibility** Account for the tokens coderouter itself spent, in a rolling
window, and compare against limits the user configured.

## Provenance is part of the data

| Status | Meaning |
|---|---|
| `CONFIRMED` | The provider reported it. |
| `ESTIMATED` | coderouter summed its own runs against a user-configured limit. |
| `UNKNOWN` | No limit configured, so no fraction can be computed. |

The TUI and `router usage` always print this tag. Limits are never hardcoded —
they are plan-specific and change. `router doctor` warns when they are unset.

## Forecasting

```
projected = (window_used + task_estimate) / window_limit
usable    = min(safe_threshold, 1 - reserve_percent)
safe      = projected <= usable
```

An unsafe forecast vetoes the candidate. The reserve is only spent on CRITICAL
tasks or an explicit user override.

## Reservations

Committed usage is derived from SQL, and a usage event only exists *after* a run
finishes. Between the routing decision and that event, the tokens are spoken for
but invisible. With `concurrency.globally > 1` that window is a real overspend:
two tasks each see 30k remaining, each estimates 20k, both proceed.

`QuotaLedger` closes it by making intent visible:

```
available = limit - committed(SQL) - outstanding_reservations
```

```
reserve()  ->  execute  ->  commit(actual)     # or release() if it never ran
```

`QuotaBook.reserve()` performs the affordability check and the hold under one
lock, so two racing callers cannot both be granted the last of a pool's
headroom. Outstanding reservations also count towards `pressure()`, so in-flight
work influences routing the same way spent tokens do. `run_task` releases the
hold if the attempt is cancelled or raises, and commits it once the run is on
the books; committing above the estimate logs a warning, because systematic
under-estimation is how a reserve gets breached even with reservations in place.

A pool with no configured limit is still reserved against — the total stays
visible — but it never blocks, because an unknown limit yields no fraction to
compare against.

**Limitation** Reservations are held in the `router` process. Two `router`
processes sharing one subscription still race. The upgrade path is a
`reservations` table behind the same API; no caller would change.

## Conservation mode

When **every** agent with a known limit is at or above 75% of its window, the
decision is flagged `conservation` and the TUI shows QUOTA CONSERVATION MODE.

**Must not** Report an estimate as if it were a provider figure. Sum rows in
Python — the totals come from SQL `SUM` over `usage_events`.

**Extending** If an agent ever gains a real quota API, implement `get_usage()` in
its adapter and return `CONFIRMED`; the manager prefers it automatically.
