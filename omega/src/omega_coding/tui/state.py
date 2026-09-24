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
terminal.** Most behavioural tests in `tests/test_tui.py` drive events through
the adapter and assert on this object. None of them start Textual.

## One tool call is one row

It was two: `tool_execution_start` added a row, `tool_execution_end` added
another. That is right for a printer, where output only ever grows downward, and
wrong for a screen, where the thing that started and the thing that finished are
the same thing. A collapsible row that says "ran npm test" and opens to show the
output cannot be built from two rows that do not know about each other — so the
start opens a row and the end fills it in, matched by `call_id`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from omega_agent.types import (
    AgentMessage,
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from omega_coding.status import describe

#: What a row is. `notice` covers cancellation, errors and the steering
#: acknowledgement — anything omega says about itself rather than relaying.
RowKind = Literal["user", "assistant", "tool", "notice"]

#: How many prompts to keep for the up-arrow. Pi's number
#: (`packages/tui/src/components/editor.ts:406`), and large enough that the cap
#: is never the reason you cannot find what you typed.
HISTORY_LIMIT = 100


@dataclass(slots=True)
class Row:
    """One line-group in the transcript pane."""

    kind: RowKind
    text: str
    #: Tool and notice rows. Renders the failure marker rather than the success
    #: one, matching `_render`'s `x` versus `<`.
    is_error: bool = False

    # ---------------------------------------------------------- tool rows only
    #: Which call this row belongs to, so `tool_execution_end` can find the row
    #: `tool_execution_start` opened. Empty on every other kind.
    call_id: str = ""
    tool_name: str = ""
    #: Kept whole rather than pre-formatted, because the widget wants to show
    #: them expanded and the collapsed title wants a summary of them. Formatting
    #: at the point of storage would force the row to pick one.
    arguments: dict[str, Any] = field(default_factory=dict)
    #: The tool's full output, for the expanded view. The collapsed view shows
    #: `text`, which is one line.
    output: str = ""
    #: True between start and end. A row that still reads "running npm test"
    #: after it finished looks hung, so the widget needs to know which it is.
    running: bool = False


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

    #: What is happening right now, in words — "reading loop.py", "running npm
    #: test". Written by the adapter from `status.describe`, so the UI and the
    #: REPL's status line say the same thing about the same call.
    activity: str = ""

    #: True while the model is emitting reasoning. Distinct from `activity`
    #: because "thinking" is a real state the provider reports, not a synonym
    #: for "waiting" — `status.py` makes the same distinction and says why.
    thinking: bool = False

    #: Running token totals, read off `message_end`. The TUI ignored that event
    #: entirely before, which is why it could not show cost while the REPL could.
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0

    #: Prompts already sent, oldest first, for the up-arrow.
    history: list[str] = field(default_factory=list)
    #: Where the up-arrow has walked to, or None when not browsing.
    cursor: int | None = None
    #: What was half-typed when browsing started, so walking back past the end
    #: restores it instead of discarding it.
    draft: str = ""

    # ------------------------------------------------------------------- rows

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

    # -------------------------------------------------------------- tool rows

    def open_tool(self, call_id: str, tool_name: str, arguments: dict[str, Any], label: str) -> int:
        """Start a tool row. Filled in later by `close_tool`."""
        self.rows.append(
            Row(
                kind="tool",
                text=label,
                call_id=call_id,
                tool_name=tool_name,
                arguments=dict(arguments),
                running=True,
            )
        )
        return len(self.rows) - 1

    def close_tool(self, call_id: str, *, label: str, output: str, is_error: bool) -> None:
        """Finish the row this call opened.

        Searched from the end because the matching row is almost always the last
        one, and because a `call_id` is only unique within a turn — scanning
        backwards finds the current call rather than a same-id one from a
        resumed session.

        A call with no open row is **appended rather than dropped**. That can
        only happen if a result arrived without a start, which would be a bug
        somewhere above; losing the output would hide it.
        """
        for row in reversed(self.rows):
            if row.kind == "tool" and row.call_id == call_id and row.running:
                row.text = label
                row.output = output
                row.is_error = is_error
                row.running = False
                return
        self.rows.append(
            Row(kind="tool", text=label, call_id=call_id, output=output, is_error=is_error)
        )

    # ---------------------------------------------------------- stored sessions

    def load_messages(self, messages: Iterable[AgentMessage]) -> None:
        """Draw a stored conversation: the rows its live turns drew, again.

        **Parity with the adapter is the specification**, and the test for this
        compares against a live turn rather than a hand-written list. A resumed
        screen that looks different from the one you left reads as a different
        conversation. So it follows the live order rather than block order: an
        assistant's text rows first, because the stream closes before any tool
        starts, then one row per tool call, closed by its result, with
        `describe` labels. Thinking draws nothing, live or here.

        A failed or cancelled turn ends in a notice. Live, that comes from
        `agent_end`, which is not stored. Its reason is, on the assistant message
        (`loop.py:123` copies it from there), so the notice is rebuilt from it in
        the adapter's wording.

        Tau does the same job in the same place (`tau_coding/tui/state.py:297`,
        `load_messages`), but walks blocks in order, because its live renderer
        does too.
        """
        calls: dict[str, ToolCall] = {}
        for message in messages:
            if isinstance(message, UserMessage):
                self.add("user", message.content)
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextContent) and block.text.strip():
                        self.add("assistant", block.text)
                for call in message.tool_calls:
                    calls[call.id] = call
                    self.open_tool(call.id, call.name, call.arguments, describe(call))
                if message.stop_reason == "aborted":
                    self.add("notice", "cancelled")
                elif message.stop_reason == "error":
                    detail = f"error: {message.error_message or ''}".strip()
                    self.add("notice", detail, is_error=True)
            elif isinstance(message, ToolResultMessage):
                opened = calls.get(message.tool_call_id)
                self.close_tool(
                    message.tool_call_id,
                    label=describe(opened, done=True) if opened is not None else message.tool_name,
                    output=message.text,
                    is_error=message.is_error,
                )

    # ---------------------------------------------------------------- history

    def remember(self, prompt: str) -> None:
        """Keep a sent prompt for the up-arrow, and stop browsing.

        Consecutive duplicates are not stored: pressing enter twice on the same
        text should not mean pressing up twice to get past it. Pi does the same
        (`editor.ts:399-407`).
        """
        if prompt and (not self.history or self.history[-1] != prompt):
            self.history.append(prompt)
            del self.history[:-HISTORY_LIMIT]
        self.cursor = None
        self.draft = ""

    def previous_prompt(self, current: str) -> str | None:
        """Walk back one prompt. None when there is nothing to walk back to.

        `current` is captured on the *first* press so that a half-written prompt
        survives a look at what came before. Without that, the up-arrow is a
        destructive key, which is not what any shell has trained anyone to
        expect.
        """
        if not self.history:
            return None
        if self.cursor is None:
            self.draft = current
            self.cursor = len(self.history) - 1
        elif self.cursor > 0:
            self.cursor -= 1
        return self.history[self.cursor]

    def next_prompt(self) -> str | None:
        """Walk forward one prompt, ending on the draft that was interrupted."""
        if self.cursor is None:
            return None
        if self.cursor >= len(self.history) - 1:
            self.cursor = None
            return self.draft
        self.cursor += 1
        return self.history[self.cursor]

    # ----------------------------------------------------------------- status

    @property
    def status(self) -> str:
        """One line describing what is happening, in words that are true.

        Deliberately not the whimsical vocabulary other agents use. `status.py`
        settled this for the print REPL — "working" is the honest label, because
        omega cannot tell thinking from waiting on a socket, and says "thinking"
        only when the provider actually reported it.
        """
        if not self.running:
            return "ready"
        if self.thinking:
            label = "thinking"
        elif self.activity:
            label = self.activity
        else:
            label = "working"
        return f"{label} · {self.queued} queued" if self.queued else label
