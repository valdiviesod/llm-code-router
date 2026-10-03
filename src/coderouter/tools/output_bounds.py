"""Bounded, structured output for tool results.

Before this module the only bound anywhere in the router was a blunt byte cut
(`BuiltinToolHost.max_bytes` on `read_file`, a `[-2000:]` tail on validation
detail). A byte cut keeps whatever happens to be at the end — which for test
output is often a wall of passing dots, and for a log is the part that repeats.

Each kind of output gets the bound that preserves what a reader actually
needs:

- `tests`: failures first. Passing output is noise; the failures and the
  summary line are the signal.
- `diff`: changed files first, then as much of the diff body as fits.
- `log`: consecutive duplicate lines collapse to `x N`, then head/tail.
- `prose`: head and tail, where objectives and conclusions live.
"""

from __future__ import annotations

import re
from typing import Literal

ToolOutputKind = Literal["tests", "diff", "log", "prose"]

#: Lines that carry test-failure signal, for the `tests` kind.
#: A line that *also* says "passed" in the same breath ("1 failed, 2 passed")
#: is a summary, not a passing one, so `PASSED` alone is not enough.
_FAILURE_LINE = re.compile(
    r"(?i)\b(?:FAILED|FAIL\b|ERROR\b|AssertionError|Assertion Failed|Traceback"
    r"|short test summary|\b\d+ failed\b)"
)

#: Files in a unified diff, for the `diff` kind.
_DIFF_FILE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)

_ELIDED = "...[{} chars elided]..."


def collapse_repeats(text: str) -> str:
    """Consecutive duplicate lines collapse to `line  x N`."""
    lines = text.splitlines()
    marked: list[str] = []
    i = 0
    while i < len(lines):
        j = i
        while j < len(lines) and lines[j] == lines[i]:
            j += 1
        run = j - i
        if run > 1:
            marked.append(f"{lines[i]}  x {run}")
        else:
            marked.append(lines[i])
        i = j
    return "\n".join(marked)


def _head_tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 3:]
    return f"{head}\n{_ELIDED.format(len(text) - len(head) - len(tail))}\n{tail}"


def failures_only(text: str, max_chars: int) -> str:
    """Keep failing lines plus the last summary line; drop passes.

    When a run fails, the signal is the failed-test blocks and the summary
    line; passing output is noise that does not help a reader who already
    saw a failure. When nothing failed-looking, fall back to head+tail.
    """
    lines = text.splitlines()
    failures = [ln for ln in lines if _FAILURE_LINE.search(ln)]
    if not failures:
        return _head_tail(text, max_chars)
    # The summary is the last line that mentions a count; keep it if it is
    # not already in the failures set, but never keep a passing-only line.
    summary = next(
        (ln for ln in reversed(lines) if re.search(r"\d+\s+passed", ln, re.I)),
        "",
    )
    body_lines = list(dict.fromkeys(failures))
    if summary and summary not in body_lines:
        body_lines.append(summary)
    return _head_tail("\n".join(body_lines), max_chars)


def changed_files_first(text: str, max_chars: int) -> str:
    """List the changed files, then fit as much of the diff as remains."""
    files = _DIFF_FILE.findall(text)
    if not files:
        return _head_tail(text, max_chars)
    header = "changed files:\n" + "\n".join(
        f"- {f}" for f in dict.fromkeys(files)
    )
    remaining = max(max_chars - len(header), 200)
    return f"{header}\n\n{_head_tail(text, remaining)}"


def bound_output(text: str, kind: ToolOutputKind = "prose",
                 max_chars: int = 4_000) -> str:
    """Bound tool output by kind. The strategies differ; the contract is one:
    the result is never longer than roughly `max_chars` and the signal —
    failures, changed files, the end of the story — survives the cut."""
    text = text.strip()
    if not text:
        return ""
    if kind == "tests":
        return failures_only(text, max_chars)
    if kind == "diff":
        return changed_files_first(text, max_chars)
    if kind == "log":
        return _head_tail(collapse_repeats(text), max_chars)
    return _head_tail(text, max_chars)
