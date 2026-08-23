# ContextManager (`v4ld1/context/`)

**Why** The cheapest token is the one never sent.

**Responsibility** Choose which project files an agent actually needs,
fingerprint that selection, and compress agent output for handoff.

## Selection

Keywords are extracted from the prompt, then candidate files are scored:
+3 for a filename match, +1 for a path match, +0.5 per keyword found in the first
4 KB. Vendor directories (`node_modules`, `.venv`, `.git`, `dist`, …) are never
walked. Only the top N survive; `aggressive: true` halves N.

## Fingerprinting

`sha256(relative path : size : mtime)` over the selected set, truncated to 32
hex chars. An identical fingerprint means the same context was already analysed,
so a cached summary in `context_cache` can be reused instead of re-sent.

## Summarisation

`summarize_output()` keeps the head and tail of long output — where the objective
and the conclusion live — and elides the middle with an explicit char count.

**Must not** Send whole directories, follow vendor trees, or silently truncate
without saying so (`ContextBundle.truncated`).
