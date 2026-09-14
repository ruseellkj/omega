"""Output budgets.

Beginner-failure #2: one `npm install` prints 40,000 lines, all of it lands in
the conversation, and the context window is gone from a single tool call.

The fix is not "send less". It is **send less and say what you dropped**:

* keep the **tail** — compiler errors and stack traces are at the end
* say which limit was hit and the absolute line range, so the model knows the
  scale of what it cannot see
* write the whole thing to a temp file and hand over the path

That last point is the design principle: *truncation is not data loss if you
tell the model how to get the rest.* The context window is a viewport, not the
storage. A model given a path can `sed -n '400,500p'` its way through the rest.

Limits match Pi and Tau, which arrived at the same numbers independently.
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from omega_coding.redact import redact

MAX_LINES = 2_000
MAX_BYTES = 50 * 1024


@dataclass(frozen=True, slots=True)
class Truncation:
    """What was cut. Goes in `ToolResult.details`, never to the model."""

    truncated: bool
    truncated_by: str | None  # "lines" | "bytes"
    total_lines: int
    output_lines: int
    full_output_path: str | None


def _format_size(num_bytes: int) -> str:
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f}KB"
    return f"{num_bytes}B"


def _spill(text: str, label: str) -> str:
    """Write the full output somewhere the model can read it back — **masked**.

    The masking is not decoration. This file is written *inside* the tool, which
    is before `after_tool_call` runs, so at Tier 2 a call whose result was
    correctly masked still left a plain copy of the secret here — and handed the
    model the path to it. Worse than the in-transcript leak in one respect: a
    transcript is at least ephemeral, and nothing cleans this file up, so it
    outlives the session that produced it.

    Masked *here* rather than by having callers pass a sanitiser in. There is one
    `_spill`, and one place cannot be forgotten; a sanitiser threaded through
    every caller has the same shape as a confinement check copied into four
    tools, which `paths.py` argues against at length. The cost is that an
    output-budget module now knows secrets exist. Worth paying: what this writes
    is a transcript by another name, and the rule for transcripts is that
    credentials stay out of them.
    """
    cleaned, _found = redact(text)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=f"-{label}.txt", prefix="omega-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(cleaned)
        path = handle.name
    return path


#: How long a spill file is worth keeping. Long enough that a path handed to the
#: model on Monday still resolves on Tuesday; short enough that the directory does
#: not grow without bound.
SPILL_MAX_AGE_DAYS = 7

#: Every spill file starts with this, which is what makes sweeping them safe: the
#: pattern cannot match anything omega did not write.
_SPILL_PREFIX = "omega-"


def sweep_old_spills(
    *, max_age_days: float = SPILL_MAX_AGE_DAYS, now: float | None = None
) -> int:
    """Delete spill files older than `max_age_days`. Returns how many went.

    **Fixing a real leak.** `_spill` writes with `delete=False`, because the whole
    point is that the path outlives the call — the model is handed it and may read
    it turns later. Nothing deleted them afterwards, so they accumulated forever:
    a real machine had files from three separate days sitting in the temp
    directory, each up to 50 KB of command output.

    Deliberately age-based rather than session-scoped. A spill file has no session
    id — `truncate_output` is called from inside a tool and never learns one — and
    plumbing one down through the tool factory to reach it would couple output
    budgets to session management for no gain. Age is the property that actually
    matters: nobody reads Tuesday's truncated build log.

    Failures are swallowed per file. A sweep that cannot delete something (a
    permission, a race with a second omega) must not stop omega starting.
    """
    directory = Path(tempfile.gettempdir())
    cutoff = (time.time() if now is None else now) - max_age_days * 86_400
    removed = 0

    try:
        candidates = list(directory.glob(f"{_SPILL_PREFIX}*.txt"))
    except OSError:
        return 0

    for path in candidates:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue

    return removed


def truncate_output(text: str, *, label: str = "output") -> tuple[str, Truncation]:
    """Return (text_for_the_model, what_was_cut).

    Under budget, the text is returned unchanged and nothing is written to disk.
    """
    encoded_len = len(text.encode("utf-8"))
    lines = text.splitlines()
    total_lines = len(lines)

    if total_lines <= MAX_LINES and encoded_len <= MAX_BYTES:
        return text, Truncation(
            truncated=False,
            truncated_by=None,
            total_lines=total_lines,
            output_lines=total_lines,
            full_output_path=None,
        )

    path = _spill(text, label)

    # Line budget first, then trim further if still over the byte budget.
    kept = lines[-MAX_LINES:] if total_lines > MAX_LINES else list(lines)
    truncated_by = "lines" if total_lines > MAX_LINES else "bytes"

    while kept and len("\n".join(kept).encode("utf-8")) > MAX_BYTES:
        kept.pop(0)
        truncated_by = "bytes"

    body = "\n".join(kept)
    start_line = total_lines - len(kept) + 1

    if truncated_by == "bytes":
        notice = (
            f"\n\n[Showing lines {start_line}-{total_lines} of {total_lines} "
            f"({_format_size(MAX_BYTES)} limit). Full output: {path}]"
        )
    else:
        notice = (
            f"\n\n[Showing lines {start_line}-{total_lines} of {total_lines}. "
            f"Full output: {path}]"
        )

    return body + notice, Truncation(
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        output_lines=len(kept),
        full_output_path=path,
    )
