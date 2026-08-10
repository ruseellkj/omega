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
from dataclasses import dataclass

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
    """Write the full output somewhere the model can read it back."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=f"-{label}.txt", prefix="omega-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(text)
        path = handle.name
    return path


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
