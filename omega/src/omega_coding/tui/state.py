"""What the screen should show — state, not widgets.

**The split is the load-bearing part of this package.** Widgets read this and
render it; nothing in here imports Textual or knows a widget exists. That is what
keeps `adapter.py` at about a hundred lines: it translates events into state
changes and never touches the display.

Collapse the two and the adapter has to know which widget to poke for which
event, which is how a UI layer ends up re-implementing the loop. Tau keeps the
same separation (`tui/state.py`, 552 lines, with no widget imports) and gets a
99-line adapter out of it.

A second reason, less obvious and more useful: **state is testable without a
terminal.** Every behavioural test in `tests/test_tui.py` drives events through
the adapter and asserts on this object. None of them start Textual.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: What a row is. `notice` covers cancellation, errors and the steering
#: acknowledgement — anything omega says about itself rather than relaying.
RowKind = Literal["user", "assistant", "tool", "notice"]


@dataclass(slots=True)
class Row:
    """One line-group in the transcript pane."""

    kind: RowKind
    text: str
    #: Tool rows only. Renders the failure marker rather than the success one,
    #: matching `_render`'s `x` versus `<`.
    is_error: bool = False


@dataclass(slots=True)
class TuiState:
    """Everything the screen needs, and nothing about how it is drawn."""

    rows: list[Row] = field(default_factory=list)

    #: True between `agent_start` and `agent_end`. Drives the status line, and
    #: decides whether typed text is a new prompt or a steering message.
    running: bool = False

    #: Index of the assistant row currently being streamed into, or None.
    #: Streaming appends to an existing row rather than adding one per chunk —
    #: without this the transcript would be one row per token.
    streaming: int | None = None

    #: How many steering messages are waiting. Shown so that typing during a
    #: turn has visible feedback; a queue you cannot see is one you assume is
    #: broken.
    queued: int = 0

    #: Last reason the agent stopped. `stop` is the only success.
    last_reason: str | None = None

    def add(self, kind: RowKind, text: str, *, is_error: bool = False) -> int:
        self.rows.append(Row(kind=kind, text=text, is_error=is_error))
        return len(self.rows) - 1

    def append_stream(self, delta: str) -> None:
        """Add streamed text to the open assistant row, opening one if needed."""
        if self.streaming is None:
            self.streaming = self.add("assistant", "")
        self.rows[self.streaming].text += delta

    def close_stream(self) -> None:
        """Finish the open assistant row.

        A row that streamed nothing is removed rather than left blank: a turn
        that goes straight to a tool call produces exactly that, and an empty
        bubble in the transcript reads as a bug.
        """
        if self.streaming is None:
            return
        if not self.rows[self.streaming].text.strip():
            del self.rows[self.streaming]
        self.streaming = None

    @property
    def status(self) -> str:
        """One line describing what is happening, in words that are true.

        Deliberately not the whimsical vocabulary other agents use. `status.py`
        settled this for the print REPL — "working" is the honest label, because
        omega cannot tell thinking from waiting on a socket.
        """
        if not self.running:
            return "ready"
        if self.queued:
            return f"working · {self.queued} queued"
        return "working"
