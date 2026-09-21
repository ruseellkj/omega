"""Ten agent events → screen state.

**This file is the test of whether Tier 1's event vocabulary was designed for a
renderer or for a printer.** It is short because the events already say what a UI
needs; if it had to reach into the harness, inspect messages, or track flags of
its own, the vocabulary would have been wrong and this is where that would show.

`cli.py:_render` was the specification, and for one tier this file matched it
branch for branch. It no longer does, and the places it diverges are the places a
screen can do something a printer cannot:

| `_render` (print) | here (screen) |
|---|---|
| `text_delta` → `print(delta, end="")` | `append_stream(delta)` |
| `text_end` → `print()` | `close_stream()` |
| `tool_execution_start` → `→ name(args)` | opens a row titled `describe(call)` |
| `tool_execution_end` → first line only | **closes that same row**, keeping all output |
| *(ignored)* | `thinking_start/end` → `state.thinking` |
| *(ignored)* | `message_end` → token counts |
| `agent_end` aborted → `[cancelled]` | a `notice` row |
| `agent_end` not stop → `[reason] message` | a `notice` row |

The three that differ, and why:

* **the tool row.** A printer has nowhere to put 2,000 lines of output, so
  `_render` keeps the first and drops the rest. A collapsible row has somewhere,
  so the title is a receipt and the payload stays in `output`.
* **thinking.** `status.py:271-277` already acted on these events; the UI
  dropping them meant the REPL could say "thinking" and the screen could not.
  An oversight, not a decision.
* **`message_end`.** The only event carrying `usage` (`cost.py:103` reads the
  same one). Ignoring it is why the TUI could not show what a turn cost.

**Still true: anything `_render` handles and this drops is a regression.** The
rows above that say "ignored" go the other way — the print renderer is the one
behind now, and that is fine, because a status line it erases cannot hold state a
screen keeps.

**It touches no widget.** Everything here mutates `TuiState`; the display reacts
to that. See `state.py` for why the split earns its keep.
"""

from __future__ import annotations

from omega_agent.agent_events import AgentEvent
from omega_coding.status import describe
from omega_coding.tui.state import TuiState


class TuiEventAdapter:
    """Applies agent events to `TuiState`. Holds nothing of its own.

    Stateless apart from the state it is given — which matters because the app
    may rebuild widgets at any time and must never lose transcript history to a
    redraw.
    """

    def __init__(self, state: TuiState) -> None:
        self.state = state

    def apply(self, event: AgentEvent) -> None:
        state = self.state

        if event.type == "agent_start":
            state.running = True
            state.last_reason = None

        elif event.type == "message_update":
            # The twelve provider events still travel inside the ten agent ones;
            # this is where they arrive, exactly as in the print renderer.
            raw = event.stream_event
            if raw.type == "text_delta":
                state.append_stream(raw.delta)
            elif raw.type == "text_end":
                state.close_stream()
            elif raw.type == "thinking_start":
                # Dropped until now, while `status.py:271-277` acted on it — so
                # the REPL could say "thinking" and the UI could not. The state
                # is real and provider-reported; refusing to show it was an
                # oversight, not a decision.
                state.thinking = True
            elif raw.type == "thinking_end":
                state.thinking = False

        elif event.type == "tool_execution_start":
            # Close the stream first: a turn that says "let me check" and then
            # calls a tool must show the text above the call, not merged into it.
            state.close_stream()
            call = event.tool_call
            # `describe` rather than `name(args)`: "running npm test" is the same
            # information in the words a person would use, and it is the string
            # the REPL's status line already shows for this call.
            state.activity = describe(call)
            state.open_tool(call.id, call.name, call.arguments, state.activity)

        elif event.type == "tool_execution_end":
            result = event.result
            call = event.tool_call
            state.close_tool(
                call.id,
                label=describe(call, done=True),
                output=result.text,
                is_error=result.is_error,
            )
            # The step is over; anything still shown would be describing the
            # past. `agent_end` clears it too, for the turn that ends here.
            state.activity = ""

        elif event.type == "message_end":
            # The only event carrying `usage`, which is why the TUI had no token
            # counts while the REPL did (`cost.py:103` reads the same event).
            usage = event.message.usage
            state.tokens_in += usage.input
            state.tokens_out += usage.output
            state.tokens_cached += usage.cache_read
            state.thinking = False

        elif event.type == "agent_end":
            state.close_stream()
            state.activity = ""
            state.thinking = False
            state.running = False
            state.last_reason = event.reason
            if event.reason == "aborted":
                # Not a failure — it was asked for. Said plainly, because a
                # crash-shaped message for "I pressed Ctrl-C" is noise.
                state.add("notice", "cancelled")
            elif event.reason != "stop":
                detail = f"{event.reason}: {event.error_message or ''}".strip()
                state.add("notice", detail, is_error=True)
