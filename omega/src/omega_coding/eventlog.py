"""A structured log of what the agent did — the second listener.

Tier 2's answer to "why did it do that" was to read the printed output. That
works while `print` is the interface and stops working the moment a TUI owns the
screen, which is the other half of Tier 3. So the log comes first: without it,
the TUI would remove the only debugging surface omega has.

## The seam it proves

`harness.add_listener` has existed since Tier 2 with exactly one subscriber, the
cost tracker. That was a claim about the design — that the event stream could
carry more than one consumer — and this is the evidence. **The harness gains
nothing**: a listener is a callable, and this is one.

## Why it does not write every event

Ten event types arrive; five are written. `message_update` fires once per
streamed chunk, so logging it would make the file mostly fragments of text that
also appear, assembled, in the message that follows. A log nobody can read is a
log nobody reads.

## Why it never writes `stream_event`

Not size — **safety**. Every message event carries the raw provider event beside
the assembled message, and that raw event holds a *delta*. A credential split
across two chunks matches no pattern in either half, so `before_record` cannot
mask it and neither can anything else. The assembled message can be masked and
is. Writing the delta would put on disk exactly what the redaction work spent
several fixes keeping off it.

The rule is therefore mechanical rather than a judgement call: **the log records
assembled messages, never fragments.**
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_agent.agent_events import AgentEvent
from omega_coding.redact import redact

#: Written by default; a debug log you have to remember to switch on is one you
#: do not have when it matters. Swept by age, like the truncation spill files.
MAX_AGE_DAYS = 7


class EventLog:
    """A harness listener that appends agent events to a JSONL file.

    Synchronous and failure-tolerant on purpose. `AgentListener` is a plain
    callable returning nothing, so a logger that raised or blocked would be able
    to stall the loop it is only watching — and losing a log line is never worth
    losing the turn that produced it.
    """

    #: Only these. See the module docstring: the rest are fragments.
    RECORDED = frozenset(
        {
            "agent_start",
            "agent_end",
            "message_end",
            "tool_execution_start",
            "tool_execution_end",
        }
    )

    def __init__(self, path: Path | Callable[[], Path]) -> None:
        #: Resolved per write, not at construction. **Found by running it:** the
        #: session id does not exist until the first turn creates it, so a path
        #: fixed at construction named every session `unsaved.jsonl` and appended
        #: them all to one file. A callable lets the CLI name the log after the
        #: session it belongs to without knowing the id in advance.
        self._resolve = path if callable(path) else (lambda: path)
        self.written = 0
        self.failed = False

    @property
    def path(self) -> Path:
        return self._resolve()

    def __call__(self, event: AgentEvent) -> None:
        if event.type not in self.RECORDED:
            return

        record: dict[str, Any] = {
            "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": event.type,
        }

        # Field by field, never `model_dump()` of the whole event: a blanket dump
        # would sweep `stream_event` in with everything else, and that is the one
        # field that must not be written.
        message = getattr(event, "message", None)
        if message is not None:
            record["role"] = message.role
            record["text"] = message.text
            record["stop_reason"] = message.stop_reason
            record["usage"] = message.usage.model_dump()
            record["tool_calls"] = [call.name for call in message.tool_calls]

        call = getattr(event, "tool_call", None)
        if call is not None:
            record["tool"] = call.name
            # **Masked here, and this was the sixth redaction bypass.**
            #
            # `harness._clean_event` runs `before_record` over the `message` an
            # event carries and over a tool `result` — but never over
            # `tool_call`, which is a separate copy living on the two tool
            # events. The transcript is safe because `redact_message` does walk
            # into tool-call arguments; this log was not, because it reads the
            # event's own copy.
            #
            # Found by a test that failed with redaction fully enabled:
            # `echo sk-ant-…` landed in `~/.omega/logs/*.jsonl` in plain text.
            #
            # Fixed at the sink rather than by widening the hook: `before_record`
            # is typed for `AgentMessage`, and a `ToolCall` is not one. A log
            # that masks what it writes needs no change in `omega_agent` at all.
            record["arguments"] = json.loads(redact(json.dumps(call.arguments, default=str))[0])

        result = getattr(event, "result", None)
        if result is not None:
            record["is_error"] = result.is_error
            record["output"] = result.text

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
            self.written += 1
        except OSError:
            # A full disk or an unwritable directory must not end the session.
            # Recorded so the CLI can report it once rather than every event.
            self.failed = True


def sweep_old_logs(directory: Path, *, max_age_days: int = MAX_AGE_DAYS) -> int:
    """Remove logs older than `max_age_days`. Returns how many went.

    Same reasoning as `truncate.sweep_old_spills`, and swept at startup for the
    same reason: the run that leaves a log behind is the one that crashed, so a
    sweep at exit would miss exactly the cases worth keeping.
    """
    if not directory.is_dir():
        return 0

    cutoff = datetime.now(UTC).timestamp() - max_age_days * 86_400
    removed = 0
    for path in sorted(directory.glob("*.jsonl")):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
