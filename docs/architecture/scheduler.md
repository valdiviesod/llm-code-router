# Scheduling and the task graph

**Why** Big tasks are cheaper and more reliable when split. Small tasks are more
expensive when split. So splitting must be conservative.

## TaskGraph (`core/task_graph.py`)

A DAG with cycle detection (Kahn's algorithm) and a `ready()` set: pending tasks
whose dependencies are all DONE.

`build_graph()` decomposes only when complexity is HIGH or CRITICAL, into
architecture → implementation → tests → review. Anything less runs as one task,
because every extra subtask is another agent run and another chunk of quota.

## Execution (`core/orchestrator.run_graph`)

- Ready tasks run concurrently, bounded by `concurrency.global` (an
  `asyncio.Semaphore`).
- **More than one ready task means every one of them gets its own git worktree.**
  A single ready task works in place, because there is nobody to collide with.
- Each completed task's `HandoffPackage` becomes the next task's context, so the
  baton is compact rather than a full transcript.
- A task that raises is marked FAILED and logged; siblings still finish.

**Must not** Run parallel agents against a shared working tree. Ever.
