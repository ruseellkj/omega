"""Ten agent events → screen state.

**This file is the test of whether Tier 1's event vocabulary was designed for a
renderer or for a printer.** It is short because the events already say what a UI
needs; if it had to reach into the harness, inspect messages, or track flags of
its own, the vocabulary would have been wrong and this is where that would show.

`cli.py:_render` is the specification, not a rough guide. It was written
standalone precisely so this swap touches one function, and every branch it has
is reproduced here:

| `_render` | here |
|---|---|
| `text_delta` → `print(delta, end="")` | `append_stream(delta)` |
| `text_end` → `print()` | `close_stream()` |
| `tool_execution_start` → `→ name(args)` | a `tool` row |
| `tool_execution_end` → `<` or `x` + first line | a `tool` row with `is_error` |
| `agent_end` aborted → `[cancelled]` | a `notice` row |
| `agent_end` not stop → `[reason] message` | a `notice` row |

Anything `_render` handles and this does not is a regression, not a
simplification.

**It touches no widget.** Everything here mutates `TuiState`; the display reacts
to that. See `state.py` for why the split earns its keep.
"""

from __future__ import annotations

from omega_agent.agent_events import AgentEvent
from omega_coding.tui.state import TuiState

#: Long tool arguments and long results are clipped in the row, not on screen.
#: The same limits `cli.py` uses, so the two frontends agree about what "one
#: line of tool output" means.
ARGUMENT_PREVIEW = 80
RESULT_PREVIEW = 100


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


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

        elif event.type == "tool_execution_start":
            # Close the stream first: a turn that says "let me check" and then
            # calls a tool must show the text above the call, not merged into it.
            state.close_stream()
            call = event.tool_call
            state.add("tool", f"→ {call.name}({_clip(str(call.arguments), ARGUMENT_PREVIEW)})")

        elif event.type == "tool_execution_end":
            result = event.result
            first_line = result.text.splitlines()[0] if result.text else ""
            marker = "x" if result.is_error else "<"
            state.add(
                "tool",
                f"  {marker} {_clip(first_line, RESULT_PREVIEW)}",
                is_error=result.is_error,
            )

        elif event.type == "agent_end":
            state.close_stream()
            state.running = False
            state.last_reason = event.reason
            if event.reason == "aborted":
                # Not a failure — it was asked for. Said plainly, because a
                # crash-shaped message for "I pressed Ctrl-C" is noise.
                state.add("notice", "cancelled")
            elif event.reason != "stop":
                detail = f"{event.reason}: {event.error_message or ''}".strip()
                state.add("notice", detail, is_error=True)
