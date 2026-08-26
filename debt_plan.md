# Technical debt plan

**Written** 2026-08-26, after the production-hardening audit (PR #1).

This file records what is *known to be missing or wrong*, with the evidence that
established it. It is not a wishlist. Everything here was found by reading the
code, running it, or both — nothing is included on suspicion alone.

Rules for this file:

- An item leaves only when it is implemented **and** tested, or when it is
  explicitly rejected with a reason.
- Anything claimed as working in `README.md` or `ARCHITECTURE.md` and found not
  to work belongs here immediately, and the claim gets removed from the docs at
  the same time.
- Effort is a rough band (S ≈ a day, M ≈ a few days, L ≈ a week or more), not a
  commitment.

---

## P0 — The token estimator is not calibrated

**This is the most important item in the file.** Every quota decision rests on
it.

### Evidence

One real run, 2026-08-26, prompt `"add a subtract function to calc.py,
mirroring add"`, routed to `claude`:

| | Tokens |
|---|---|
| Estimated (`AgentAdapter.estimate`) | 2,626 |
| Actually spent | 115,924 |
| **Ratio** | **44x** |

Recorded in the database; reproducible with `router explain <task-id>`.

### Root cause

`agents/base/adapter.py:99` estimates a **single-shot completion**:

```python
input_tokens = (prompt_bytes + context_bytes) // BYTES_PER_TOKEN + 2_000
output_tokens = int(input_tokens * (0.3 + 0.2 * task.complexity.rank))
```

It counts the prompt, the files the context manager selected, and a flat 2k.

But the adapters do not perform a single-shot completion. They drive an
**agentic CLI** that reads files we never selected, runs tools, and iterates
over many turns. The estimate models the wrong thing. This is a structural
mismatch, not a constant that needs nudging — no value of `BYTES_PER_TOKEN`
closes a 44x gap.

### Why it matters beyond being wrong

Quota reservations (`usage/reservation.py`) are only as good as the estimate
feeding them. A reservation for 2,626 tokens against a run that spends 115,924
does not prevent an overspend — it *authorises* one, with the paperwork of
having been checked. The `commit()` path logs `reservation under-estimated`,
so the miss is visible, but visibility is not a fix.

The same estimate drives:

- `UsageManager.forecast()` — the hard quota veto
- `QuotaBook.can_fund()` / `reserve()` — affordability
- `router plan` — the number a user reads before deciding to run something

All four are currently reporting a figure that is off by more than an order of
magnitude.

### Fix

The calibration data **already exists**. `db.avg_tokens(agent_id, task_type)`
aggregates observed spend per agent × task type, and `agent_stats` has been
accumulating since v0.1.0.

1. Estimate from **observed history first**: `avg_tokens(agent, task_type)`,
   scaled by complexity. Fall back to the current heuristic only below a minimum
   sample count, the same way `_history` already falls back to `NEUTRAL_PRIOR`.
2. Keep the heuristic as the cold-start prior, but multiply it by an
   **observed correction factor** — the running ratio of actual to estimated —
   rather than trusting it raw.
3. Report `UsageEstimate.confidence` honestly: high when it comes from enough
   samples, low when it is the cold-start guess. `router plan` should print the
   provenance, the same way usage figures carry `CONFIRMED`/`ESTIMATED`/`UNKNOWN`
   (ADR-005).
4. Add a regression test that fails if the estimator is more than ~3x off on
   recorded historical runs.

**Effort** M. **Blocks** any honest use of quota pressure, conservation mode, or
the model cascade below.

---

## P1 — Reservations are process-local

**Stated ceiling, not a bug.** `usage/reservation.py` holds reservations in
memory, guarded by a `threading.Lock`.

That is correct for one `router` process owning its own scheduler and
concurrency limit. Two `router` processes sharing one subscription — two
terminals, or a TUI plus a CLI run — still race exactly as before reservations
existed.

**Fix** A `reservations` table with the same API: `hold` / `commit` / `release`,
with the affordability check and the insert in one SQLite transaction. Callers
do not change. Needs a stale-reservation reaper, since a killed process cannot
release its own holds — a hold older than the window is dead by definition.

**Effort** M. Documented in `docs/architecture/usage.md`.

---

## P1 — Not implemented, but advertised or implied

Each of these appears in the project's ambitions and does not exist. Unlike the
tools subsystem below, none of them is half-built or advertised as working —
they are simply absent.

| Item | What is missing | Effort |
|---|---|---|
| **Model cascade** | Escalate cheap → medium → premium only when justified, and weight the decision by remaining capacity. Today `_model_for` picks a tier from complexity alone and never retries at a higher tier. Depends on P0. | M |
| **Permissions engine** | `allow` / `deny` / `ask` per capability (`filesystem.write`, `shell.execute`, `git.push`, …). Today `CommandPolicy` classifies shell strings for the builtin `run_command` tool only. The agent CLIs run under **their own** permission systems, which coderouter neither configures nor intercepts. | L |
| **Sandbox** | Filesystem, network and resource boundaries for child processes. Worktrees isolate *files between agents*; they do not stop an agent leaving the workspace. | L |
| **Tool output summarisation** | Bounded, structured output per tool: failures-only for tests, changed-files-first for diffs, collapsed repeats for logs. Today the only bound anywhere is `BuiltinExecutor.max_bytes = 40_000`, a blunt byte cut on `read_file`. Probably the single largest available token saving — but see the item below first. | M |
| **AST / symbol index** | The context manager ranks whole files. No symbol graph, no call paths, no dependency-aware retrieval. | L |
| **Autopilot** | The end-to-end autonomous loop, including the confidence threshold at which it must stop rather than continue. | L |
| **Benchmark suite** | Local, repeatable tasks measuring success, tokens, latency and retries per model. Without it, "adaptive routing" has nothing to be evaluated against. | M |
| **Failure injection tests** | Provider timeout, rate limit, invalid credentials, malformed response, context overflow, process crash mid-run. The recovery paths exist and are largely untested. | M |

---

## P1 — The tools subsystem is inert

Found while checking the claims in this file. Recorded here rather than fixed in
PR #1, because fixing it is a design decision, not a bug fix.

### Three separate findings

**1. Two of the four builtin tools have no implementation.**
`builtin_tools()` declares `read_file`, `grep`, `edit_file` and `run_command`,
each with a description and a schema. `BuiltinExecutor` implements exactly two
methods: `read_file` and `run_command`. `grep` and `edit_file` do not exist.
`router tools` lists all four.

**2. `run_command` does not run commands.** It classifies the string through
`CommandPolicy` and returns the verdict:

```python
def run_command(self, command: str) -> dict[str, Any]:
    decision, reason = self.policy.classify(command)
    if decision is Decision.BLOCK:
        return {"ok": False, "error": f"blocked: {reason}"}
    if decision is Decision.ASK:
        return {"ok": False, "error": f"requires_approval: {reason}"}
    return {"ok": True, "decision": decision.value, "reason": reason}
```

The SAFE branch returns the decision and stops. There is also no approval flow
anywhere, so the ASK branch can never be satisfied.

**3. Nothing calls `BuiltinExecutor` at all**, and tool selection goes nowhere.
`BuiltinExecutor` has zero references outside its own module.
`task.selected_tool_ids` is assigned once in `orchestrator.py:359` and **never
read** — not into the prompt, not into the adapter invocation.

### What this means

The tool selector runs, budgets tokens, picks tools, and discards the result.
Actual tool use is done entirely by the agent CLIs' own native tools, which
coderouter neither selects nor sees.

That is not necessarily the wrong architecture — delegating tool execution to
an already-good agent CLI is defensible. What is wrong is that the code says
otherwise: `tools/__init__.py` describes builtin tools as a source the agent
draws from, `router tools` presents them as available, and the selector exists
to budget something nobody consumes.

### Decision required, then fix

Either:

- **(a)** Delete the builtin executor and the selector, and reframe the tools
  subsystem as *MCP discovery plus capability reporting* — which is the part
  that actually works. Honest, and removes ~300 lines.
- **(b)** Wire it up: implement `grep` and `edit_file`, make `run_command`
  execute with bounded output, build the approval flow the ASK verdict needs,
  and inject `selected_tool_ids` into the prompt so the budget means something.

**(a) is the smaller, more honest change** and is the recommendation, unless
coderouter is meant to run tools for agents that have none of their own. **(b)
is a prerequisite for the sandbox and permissions items above** — there is no
point sandboxing an executor nothing calls.

Until this is decided, `docs/` and `router tools` overstate what exists.

**Effort** S for (a), L for (b).

---

## P2 — Audited superficially or not at all

Listed so nobody mistakes silence for a clean bill of health.

| Area | State |
|---|---|
| `context/manager.py` | Not audited. It decides what every agent sees, so its selection quality is directly a token cost. |
| `tui/` | Not audited. ~790 lines across three modules. |
| `agents/claude/adapter.py`, `agents/antigravity/adapter.py` | Contract-tested against captured payloads, not read line by line. The captured payloads in `NATIVE_PAYLOADS` are the guardrail; they are only as good as the last capture. |
| `routing/llm_classifier.py` | Read enough to confirm it spends and records quota honestly. Its cache invalidation was not examined. |

---

## Fixed in PR #1 — kept for the record

So the same class of bug is recognisable next time.

- **Isolated runs destroyed their own work.** `merge()` had no callers; the
  `finally` ran `worktree remove --force`. Parallel execution and every
  speculative race produced no file changes. See
  [ADR-007](docs/adr/ADR-007-worktree-integration.md).
- **Speculative dispatch was dead on arrival** — a wrong import, `__dict__` on a
  `slots=True` dataclass, and a double-persisted run that double-counted the
  winner's tokens.
- **MCP had no `initialize` handshake** and no response-id correlation.
- **Quota accounting escaped for injected adapters** — the book was built from a
  fresh registry rather than the live one.
- **`risk="low"` as a string against a `Risk`-typed field**, with
  `# type: ignore[arg-type]` suppressing the exact error that would have caught
  it.

The pattern in all five: **the tests passed**. They asserted the shape of what
the code did, not the outcome the user needed. A worktree test that checks a
worktree was created and removed passes happily while the work is destroyed.

The lesson, and the standard for new tests here: **assert the outcome, not the
mechanism.** `assert (repo / "generated.py").exists()` catches the bug;
`assert not wt.path.exists()` celebrates it.

---

## Suggested order

1. **P0 estimator.** Everything quota-related is currently built on a number
   that is 44x wrong. Nothing else in this file is worth doing first.
2. **Decide (a) or (b) on the tools subsystem.** Everything else in the tools,
   sandbox and permissions space is blocked behind that choice, and the docs
   are currently wrong either way.
3. **Tool output summarisation** — if (b). The largest token saving available,
   and independent of the estimator.
4. **Failure injection.** The recovery paths are written and unproven; this is
   cheap relative to what it de-risks.
5. **Benchmark suite**, so cascade and adaptive routing have something to be
   measured against.
6. Everything else.
