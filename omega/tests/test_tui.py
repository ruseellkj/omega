"""The terminal UI.

**Almost none of these start Textual.** The split between `state.py` and the
widgets is what makes that possible: events go through the adapter, assertions
are on `TuiState`, and no terminal is involved. A UI whose behaviour can only be
checked by driving a screen is a UI that stops being checked.

Two groups matter more than the rest:

* **`_render` parity.** `cli.py:_render` is the specification — it was written
  standalone so this swap touches one function. Anything it handles that the
  adapter does not is a regression, so each of its branches has a test here.
* **Steering.** `TIER-2.md` records `queue_steering` as wired, tested, and
  unreachable by a human, because a `print`/`input` REPL cannot take a keystroke
  mid-turn. That gap closing is the reason the TUI was worth a dependency, and
  the tests at the bottom are the actual claim.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from omega_agent.agent_events import (
    AgentEndEvent,
    AgentStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from omega_agent.events import TextDeltaEvent, TextEndEvent
from omega_agent.harness import Harness
from omega_agent.types import AssistantMessage, ToolCall, ToolResultMessage
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_coding.builtin_tools import build_tools
from omega_coding.tui.adapter import TuiEventAdapter
from omega_coding.tui.state import TuiState


def _adapter() -> tuple[TuiState, TuiEventAdapter]:
    state = TuiState()
    return state, TuiEventAdapter(state)


def _partial() -> AssistantMessage:
    return AssistantMessage(model="m", stop_reason="pending")


def _delta(text: str) -> MessageUpdateEvent:
    return MessageUpdateEvent(
        message=_partial(),
        stream_event=TextDeltaEvent(content_index=0, delta=text, partial=_partial()),
    )


# ------------------------------------------------------------ _render parity


def test_streamed_text_becomes_one_row_not_one_per_chunk() -> None:
    """The print renderer writes deltas with `end=""`, so they form one line.

    The equivalent here is appending to an open row. Adding a row per chunk would
    turn a two-sentence answer into forty transcript entries.
    """
    state, adapter = _adapter()

    for chunk in ("Hello", " there", ", friend"):
        adapter.apply(_delta(chunk))

    assert len(state.rows) == 1
    assert state.rows[0].text == "Hello there, friend"
    assert state.rows[0].kind == "assistant"


def test_text_end_closes_the_row() -> None:
    """`_render` prints a newline on `text_end`; the row equivalent is closing it
    so the next delta starts a new one."""
    state, adapter = _adapter()
    adapter.apply(_delta("first answer"))
    adapter.apply(
        MessageUpdateEvent(
            message=_partial(),
            stream_event=TextEndEvent(content_index=0, content="first answer", partial=_partial()),
        )
    )
    adapter.apply(_delta("second answer"))

    assert [row.text for row in state.rows] == ["first answer", "second answer"]


def test_a_tool_call_is_shown_with_its_arguments() -> None:
    state, adapter = _adapter()

    adapter.apply(
        ToolExecutionStartEvent(
            tool_call=ToolCall(id="1", name="read_file", arguments={"path": "a.py"})
        )
    )

    assert state.rows[0].kind == "tool"
    assert "read_file" in state.rows[0].text
    assert "a.py" in state.rows[0].text


def test_a_tool_result_shows_only_its_first_line() -> None:
    """Matching `_render`. A tool that returns 2,000 lines must not push the
    conversation off the screen — the row is a receipt, not the payload."""
    state, adapter = _adapter()

    adapter.apply(
        ToolExecutionEndEvent(
            tool_call=ToolCall(id="1", name="run_shell", arguments={}),
            result=ToolResultMessage(
                tool_call_id="1", tool_name="run_shell", content="first\nsecond\nthird"
            ),
        )
    )

    assert "first" in state.rows[0].text
    assert "second" not in state.rows[0].text


def test_a_failed_tool_is_marked_as_an_error() -> None:
    state, adapter = _adapter()

    adapter.apply(
        ToolExecutionEndEvent(
            tool_call=ToolCall(id="1", name="run_shell", arguments={}),
            result=ToolResultMessage(
                tool_call_id="1", tool_name="run_shell", content="boom", is_error=True
            ),
        )
    )

    assert state.rows[0].is_error is True


def test_cancellation_is_reported_as_a_notice_not_an_error() -> None:
    """It was asked for. A crash-shaped message for "I pressed Ctrl-C" is noise —
    the print renderer already draws this distinction and the TUI keeps it."""
    state, adapter = _adapter()

    adapter.apply(AgentEndEvent(reason="aborted"))

    assert state.rows[-1].kind == "notice"
    assert state.rows[-1].is_error is False
    assert "cancelled" in state.rows[-1].text


def test_a_real_failure_is_reported_as_an_error() -> None:
    state, adapter = _adapter()

    adapter.apply(AgentEndEvent(reason="error", error_message="the provider refused"))

    assert state.rows[-1].is_error is True
    assert "the provider refused" in state.rows[-1].text


def test_a_clean_finish_adds_no_row() -> None:
    """`stop` is the only success, and success needs no announcement."""
    state, adapter = _adapter()

    adapter.apply(AgentEndEvent(reason="stop"))

    assert state.rows == []


# ----------------------------------------------------------------- the state


def test_an_empty_assistant_row_is_dropped() -> None:
    """A turn that goes straight to a tool call opens a row and streams nothing.

    Left in, the transcript shows a blank bubble above every tool call, which
    reads as a rendering bug rather than as nothing having been said.
    """
    state, adapter = _adapter()
    adapter.apply(_delta(""))

    adapter.apply(
        ToolExecutionStartEvent(tool_call=ToolCall(id="1", name="read_file", arguments={}))
    )

    assert [row.kind for row in state.rows] == ["tool"]


def test_the_status_line_says_only_what_is_true() -> None:
    """Not the whimsical vocabulary other agents use. `status.py` settled this
    for the print REPL: omega cannot tell thinking from waiting on a socket, so
    it says "working"."""
    state, adapter = _adapter()
    assert state.status == "ready"

    adapter.apply(AgentStartEvent())
    assert state.status == "working"

    state.queued = 2
    assert "2 queued" in state.status


def test_running_is_true_only_between_start_and_end() -> None:
    state, adapter = _adapter()
    assert state.running is False

    adapter.apply(AgentStartEvent())
    assert state.running is True

    adapter.apply(AgentEndEvent(reason="stop"))
    assert state.running is False


# --------------------------------------------------- the reason the TUI exists


async def test_steering_typed_during_a_turn_reaches_the_model(tmp_path: Path) -> None:
    """**The gap this whole package closes.**

    `TIER-2.md`, known rough edges:

        "Steering cannot actually be typed yet. The queues are wired and the loop
        drains them between turns, but a print/input REPL has no way to accept a
        keystroke while a turn is running."

    The queue and the drain have both existed since Tier 2. What was missing was
    a frontend that can take input while a turn is in flight — which is what an
    always-live input box is. This drives the same call the input box makes.
    """
    harness = Harness(
        provider=FakeProvider(
            [tool_turn("run_shell", {"command": "echo one"}), text_turn("done")]
        ),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )

    steered = False
    async for event in harness.run("start the task"):
        # Mid-turn, exactly where a keystroke would land: after the model has
        # asked for a tool and before the turn is over.
        if not steered and event.type == "tool_execution_end":
            harness.queue_steering("actually, use pytest not unittest")
            steered = True

    assert steered, "the fixture has to reach a tool result"
    assert any(
        "use pytest not unittest" in str(getattr(m, "content", "")) for m in harness.messages
    ), "the steering message reached the transcript, so the model saw it"


async def test_steering_lands_after_the_tool_result_not_before(tmp_path: Path) -> None:
    """Order is the property, not just arrival.

    The loop drains the queue *between* turns — after the tool result is
    recorded, before the next request. Landing earlier would mean re-ordering a
    transcript the provider validates.
    """
    harness = Harness(
        provider=FakeProvider(
            [tool_turn("run_shell", {"command": "echo one"}), text_turn("done")]
        ),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )

    async for event in harness.run("start"):
        if event.type == "tool_execution_end":
            harness.queue_steering("steer me")

    kinds = [type(m).__name__ for m in harness.messages]
    steer_at = next(
        i for i, m in enumerate(harness.messages) if "steer me" in str(getattr(m, "content", ""))
    )
    result_at = kinds.index("ToolResultMessage")

    assert steer_at > result_at, "guidance arrives after the result it reacts to"


# ------------------------------------------------- the app, driven headlessly


async def test_the_app_renders_a_turn(tmp_path: Path) -> None:
    """Textual's own test driver, so this needs no terminal.

    Worth having beyond the state tests: it proves the worker, the redraw and
    the input box are actually wired to each other, which state assertions
    cannot show.
    """
    from omega_coding.tui.app import OmegaApp

    harness = Harness(
        provider=FakeProvider(
            [tool_turn("run_shell", {"command": "echo hi"}), text_turn("All done.")]
        ),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )
    app = OmegaApp(harness)

    async with app.run_test() as pilot:
        app.query_one("Input").value = "do the thing"  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause(0.3)

    kinds = [row.kind for row in app.state.rows]
    assert kinds[0] == "user"
    assert "tool" in kinds
    assert kinds[-1] == "assistant"
    assert app.state.running is False


async def test_typing_while_a_turn_runs_steers_instead_of_starting_another(
    tmp_path: Path,
) -> None:
    """**The app-level half of the claim, and the one a fast fake hides.**

    Driving this with the ordinary fake provider proved nothing: it finishes
    before the second keystroke lands, so the input started a *second turn* and
    the test would have passed against a build with no steering at all. The
    provider here is deliberately slow enough that the turn is still in flight.
    """
    from omega_coding.tui.app import OmegaApp

    class SlowProvider:
        """Wraps the fake and puts a real pause inside the stream."""

        def __init__(self) -> None:
            # A tool call *then* an answer, so the loop iterates twice. This is
            # not padding: the loop drains steering **between** iterations, so a
            # single text-only turn ends the run with the queue untouched and the
            # guidance waits for the next `run()`. Correct, and easy to mistake
            # for a broken queue.
            self._inner = FakeProvider(
                [tool_turn("run_shell", {"command": "echo one"}), text_turn("done")]
            )

        def stream_response(self, **kwargs: object) -> object:
            inner = self._inner.stream_response(**kwargs)  # type: ignore[arg-type]

            async def slowed() -> object:
                async for event in inner:  # type: ignore[attr-defined]
                    await asyncio.sleep(0.25)
                    yield event

            return slowed()

    harness = Harness(
        provider=SlowProvider(),  # type: ignore[arg-type]
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )
    app = OmegaApp(harness)

    async with app.run_test() as pilot:
        app.query_one("Input").value = "start"  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert app.state.running is True, "the turn must still be in flight"
        assert app.state.queued == 0

        app.query_one("Input").value = "use pytest not unittest"  # type: ignore[attr-defined]
        await pilot.press("enter")
        queued_rows = [r for r in app.state.rows if "steering queued" in r.text]

        assert queued_rows, "typing mid-turn is acknowledged on screen"
        assert app.state.queued == 1
        await pilot.pause(2.0)

    user_rows = [r for r in app.state.rows if r.kind == "user"]
    assert len(user_rows) == 1, "it steered rather than starting a second turn"
    assert any(
        "use pytest not unittest" in str(getattr(m, "content", "")) for m in harness.messages
    ), "and the guidance reached the transcript"
