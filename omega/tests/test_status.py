"""The status line.

Two things are worth defending here, and neither is the spinner.

**The labels say what is happening.** Claude Code shows ~184 whimsical gerunds;
this shows the tool and its target. Both beat silence, but only one of them
survives being asked "what is it doing?".

**It never shares a row with the transcript.** The status line writes to stderr
and the model writes to stdout, into the same terminal. Whoever writes second
wins, so every event about to produce output must erase the line first. That is
what most of these tests are actually checking.
"""

from __future__ import annotations

import asyncio
import io

from omega_agent.agent_events import (
    AgentEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnStartEvent,
)
from omega_agent.events import TextDeltaEvent, ThinkingEndEvent, ThinkingStartEvent
from omega_agent.types import AssistantMessage, ToolCall, ToolResultMessage
from omega_coding.status import (
    ELAPSED_AFTER_SECONDS,
    FRAMES,
    WAITING,
    StatusLine,
    describe,
)


def _line(stream: io.StringIO | None = None, *, clock: list[float] | None = None) -> StatusLine:
    ticks = clock if clock is not None else [0.0]
    return StatusLine(stream or io.StringIO(), enabled=True, now=lambda: ticks[0])


def _call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id="1", name=name, arguments=arguments)


# ------------------------------------------------------------------- labels


def test_labels_name_the_tool_and_its_target() -> None:
    """The whole argument for this over a bare spinner.

    "reading src/loop.py" answers "what is it doing"; *Percolating* does not.
    The target matters as much as the verb — which file is the part you want.
    """
    assert describe(_call("read_file", path="src/loop.py")) == "reading src/loop.py"
    assert describe(_call("write_file", path="notes.md")) == "writing notes.md"
    assert describe(_call("edit_file", path="src/cli.py")) == "editing src/cli.py"
    assert describe(_call("run_shell", command="uv run pytest")) == "running uv run pytest"


def test_a_long_command_degrades_to_its_first_word() -> None:
    """A wrapped status line cannot be erased with one carriage return.

    So a long command loses its arguments rather than its containment: you still
    learn it is a `uv` run, and the line still fits.
    """
    label = describe(_call("run_shell", command="uv run pytest -q tests/ --maxfail=1 -x -vv"))

    assert label == "running uv…"
    assert len(label) < 40


def test_a_long_path_is_clipped_not_wrapped() -> None:
    label = describe(_call("read_file", path="src/" + "very-long-directory/" * 5 + "file.py"))

    assert label.startswith("reading ")
    assert label.endswith("…")
    assert len(label) <= 48


def test_an_unknown_tool_falls_back_to_its_name() -> None:
    """Never invent a verb for a tool this module has not been told about."""
    assert describe(_call("some_future_tool", thing=1)) == "some_future_tool"


def test_an_empty_command_still_says_something_true() -> None:
    assert describe(_call("run_shell", command="")) == "running a command"


# ------------------------------------------------------------------ rendering


def test_the_line_shows_a_frame_and_the_label() -> None:
    status = _line()
    status.start(WAITING)

    rendered = status.render()

    assert rendered.startswith(FRAMES[0])
    assert f"{WAITING}…" in rendered


def test_elapsed_time_appears_only_once_it_is_worth_showing() -> None:
    """A duration on every turn is noise. One that shows up when something is
    taking a while is a signal."""
    clock = [0.0]
    status = _line(clock=clock)
    status.start(WAITING)

    assert "(" not in status.render(), "nothing to report yet"

    clock[0] = ELAPSED_AFTER_SECONDS + 3
    assert "(4s)" in status.render()


def test_frames_advance() -> None:
    status = _line()
    status.start(WAITING)

    assert [status.render()[0] for _ in range(3)] == list(FRAMES[:3])


# ------------------------------------------- it must never share a row with output


def test_a_text_delta_clears_the_line() -> None:
    """The model is about to speak on stdout. A spinner beside its first token
    is the failure this whole class exists to avoid."""
    stream = io.StringIO()
    status = _line(stream)
    status.start(WAITING)
    status._draw()  # noqa: SLF001 - simulating one animation tick

    status.observe(
        MessageUpdateEvent(
            message=AssistantMessage(model="m"),
            stream_event=TextDeltaEvent(
                content_index=0, delta="Hello", partial=AssistantMessage(model="m")
            ),
        )
    )

    assert stream.getvalue().endswith("\r"), "line erased, cursor back at the start"


def test_a_tool_call_swaps_the_label() -> None:
    stream = io.StringIO()
    status = _line(stream)
    status.start(WAITING)
    status._draw()  # noqa: SLF001

    status.observe(ToolExecutionStartEvent(tool_call=_call("read_file", path="a.py")))

    assert "reading a.py" in status.render()


def test_a_finished_tool_returns_to_waiting() -> None:
    status = _line()
    status.observe(ToolExecutionStartEvent(tool_call=_call("read_file", path="a.py")))
    status.observe(
        ToolExecutionEndEvent(
            tool_call=_call("read_file", path="a.py"),
            result=ToolResultMessage(
                tool_call_id="1", tool_name="read_file", content="x", is_error=False
            ),
        )
    )

    assert f"{WAITING}…" in status.render()


def test_the_end_of_a_run_leaves_nothing_behind() -> None:
    """Whatever happened, the terminal is handed back clean."""
    stream = io.StringIO()
    status = _line(stream)
    status.observe(TurnStartEvent(turn=1))
    status._draw()  # noqa: SLF001

    status.observe(AgentEndEvent(reason="stop"))

    assert stream.getvalue().endswith("\r")


# ------------------------------------------------------------------ plumbing


def test_nothing_is_written_when_stderr_is_not_a_terminal() -> None:
    """`omega > answer.txt` must not collect spinner frames.

    Also what keeps every other test in this file deterministic.
    """
    stream = io.StringIO()
    status = StatusLine(stream, enabled=False)
    status.start(WAITING)
    status._draw()  # noqa: SLF001
    status.clear()

    assert stream.getvalue() == ""


def test_clearing_twice_is_harmless() -> None:
    stream = io.StringIO()
    status = _line(stream)
    status.clear()
    status.clear()

    assert stream.getvalue() == "", "nothing drawn, so nothing to erase"


def test_a_shorter_label_does_not_leave_a_tail() -> None:
    """Rewriting a short line over a long one would leave the old ending visible."""
    stream = io.StringIO()
    status = _line(stream)
    status.start("running a fairly long command indeed")
    status._draw()  # noqa: SLF001
    status.start("ok")
    status._draw()  # noqa: SLF001

    last = stream.getvalue().split("\r")[-1]
    assert last.rstrip() != last, "padded out to cover what was there before"


async def test_the_animation_starts_and_stops_cleanly() -> None:
    """The task must not outlive the turn — a stray frame arriving after the
    prompt has printed is worse than no spinner at all."""
    stream = io.StringIO()
    status = _line(stream)

    async with status:
        await asyncio.sleep(0.25)
        assert stream.getvalue(), "it drew something while the turn ran"

    assert status._task is None  # noqa: SLF001
    assert stream.getvalue().endswith("\r"), "and cleaned up after itself"


# ------------------------------------------------- "thinking" means thinking


def test_the_idle_label_is_not_thinking() -> None:
    """The word is reserved, and this test is why.

    `events.py` declares `thinking_start` / `thinking_delta` / `thinking_end`,
    and `omega_ai/anthropic.py` emits them when extended thinking is on. Using
    "thinking" for "waiting on the network" would claim a capability that may not
    be switched on, and would make the genuine state indistinguishable from an
    ordinary pause.
    """
    assert WAITING != "thinking"

    status = _line()
    status.observe(TurnStartEvent(turn=1))

    assert "thinking" not in status.render()
    assert f"{WAITING}…" in status.render()


def test_thinking_is_shown_only_while_the_model_is_thinking() -> None:
    """And when it really is thinking, say so — that is the label being earned."""
    status = _line()
    status.observe(TurnStartEvent(turn=1))
    assert f"{WAITING}…" in status.render()

    status.observe(
        MessageUpdateEvent(
            message=AssistantMessage(model="m"),
            stream_event=ThinkingStartEvent(
                content_index=0, partial=AssistantMessage(model="m")
            ),
        )
    )
    assert "thinking…" in status.render()

    status.observe(
        MessageUpdateEvent(
            message=AssistantMessage(model="m"),
            stream_event=ThinkingEndEvent(
                content_index=0, content="…", partial=AssistantMessage(model="m")
            ),
        )
    )
    assert f"{WAITING}…" in status.render(), "back to waiting once it stops"


async def test_clearing_stops_the_animation_from_redrawing() -> None:
    """The bug that shuffled a real answer into nonsense.

    `clear()` used to erase the line and leave the animation task running. It
    woke 100ms later and redrew — and because every frame starts with a carriage
    return, that redraw landed on top of whatever the model had streamed to
    stdout in the meantime. The reply came out interleaved and padded.

    Erasing is not enough. Drawing has to stop until something says what is
    happening next.
    """
    stream = io.StringIO()
    status = _line(stream)

    async with status:
        await asyncio.sleep(0.15)          # let it draw at least once
        assert stream.getvalue(), "the spinner was running"

        status.clear()                     # the model starts streaming
        before = len(stream.getvalue())
        await asyncio.sleep(0.35)          # three frame intervals

        assert len(stream.getvalue()) == before, "nothing may be drawn while cleared"


async def test_a_new_label_resumes_drawing() -> None:
    """The pause must lift, or the spinner never comes back after the first reply."""
    stream = io.StringIO()
    status = _line(stream)

    async with status:
        status.clear()
        await asyncio.sleep(0.2)
        quiet = len(stream.getvalue())

        status.start("running tests")      # a tool call begins
        await asyncio.sleep(0.25)

        assert len(stream.getvalue()) > quiet, "drawing resumed"
        assert "running tests" in status.render()
