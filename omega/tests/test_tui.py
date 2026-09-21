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
from typing import Any

from omega_agent.agent_events import (
    AgentEndEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from omega_agent.events import (
    AssistantDoneEvent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
)
from omega_agent.harness import Harness
from omega_agent.types import AssistantMessage, ToolCall, ToolResultMessage, Usage
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_coding.builtin_tools import build_tools
from omega_coding.tui.adapter import TuiEventAdapter
from omega_coding.tui.state import HISTORY_LIMIT, TuiState


def _adapter() -> tuple[TuiState, TuiEventAdapter]:
    state = TuiState()
    return state, TuiEventAdapter(state)


def _partial() -> AssistantMessage:
    return AssistantMessage(model="m", stop_reason="pending")


def _done(message: AssistantMessage) -> AssistantDoneEvent:
    return AssistantDoneEvent(reason="stop", message=message)


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


def test_a_tool_call_is_described_in_words_and_keeps_its_arguments() -> None:
    """**Changed deliberately.** The row used to read `read_file({'path': 'a.py'})`.

    It now reads "reading a.py" — `status.describe`, the same string the REPL's
    status line shows for the same call, so the two frontends cannot drift.

    The raw arguments did not disappear; they moved from the text to a field,
    because the collapsed row wants a summary and the expanded one wants the
    whole call, and a pre-formatted string can only be one of those.
    """
    state, adapter = _adapter()

    adapter.apply(
        ToolExecutionStartEvent(
            tool_call=ToolCall(id="1", name="read_file", arguments={"path": "a.py"})
        )
    )

    row = state.rows[0]
    assert row.kind == "tool"
    assert row.text == "reading a.py"
    assert row.tool_name == "read_file"
    assert row.arguments == {"path": "a.py"}
    assert row.running is True, "still going, and the widget has to be able to say so"


def test_a_tool_result_is_kept_whole_but_summarised_in_the_title() -> None:
    """**Changed deliberately, and this is the point of the collapsible row.**

    `_render` showed the first line only, because a printer has nowhere to put
    the rest and a 2,000-line result would push the conversation off the screen.
    A screen does have somewhere: the row's title stays one line, the output
    lives in `output`, and expanding shows it.

    So the rule is no longer "keep the first line" — it is "the title is a
    receipt, the payload is still there".
    """
    state, adapter = _adapter()
    call = ToolCall(id="1", name="run_shell", arguments={"command": "ls"})

    adapter.apply(ToolExecutionStartEvent(tool_call=call))
    adapter.apply(
        ToolExecutionEndEvent(
            tool_call=call,
            result=ToolResultMessage(
                tool_call_id="1", tool_name="run_shell", content="first\nsecond\nthird"
            ),
        )
    )

    assert len(state.rows) == 1, "one call is one row, not a start row and an end row"
    row = state.rows[0]
    assert row.text == "ran ls", "the title is past tense once it has finished"
    assert "second" not in row.text, "the title stays one line"
    assert row.output == "first\nsecond\nthird", "nothing was thrown away"
    assert row.running is False


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


# ---------------------------------------------------------------- what is happening


def test_the_status_line_names_the_tool_instead_of_saying_working() -> None:
    """**The second half of the complaint.** The UI said "working" for every
    tool call, while `status.py` had been computing "reading loop.py" for the
    REPL since Tier 2. Same call, same words, both frontends."""
    state, adapter = _adapter()
    adapter.apply(AgentStartEvent())

    assert state.status == "working", "nothing has started yet"

    adapter.apply(
        ToolExecutionStartEvent(
            tool_call=ToolCall(id="1", name="read_file", arguments={"path": "loop.py"})
        )
    )

    assert state.status == "reading loop.py"


def test_the_activity_clears_when_the_tool_finishes() -> None:
    """A label that outlives its call describes the past and reads as hung."""
    state, adapter = _adapter()
    call = ToolCall(id="1", name="run_shell", arguments={"command": "ls"})
    adapter.apply(AgentStartEvent())
    adapter.apply(ToolExecutionStartEvent(tool_call=call))

    adapter.apply(
        ToolExecutionEndEvent(
            tool_call=call,
            result=ToolResultMessage(tool_call_id="1", tool_name="run_shell", content="a"),
        )
    )

    assert state.activity == ""
    assert state.status == "working", "back to the honest default, not the old label"


def test_thinking_is_shown_only_when_the_provider_reports_it() -> None:
    """`status.py:60-66` argues this at length: "thinking" must not become a
    synonym for "waiting on a socket", or the real state becomes unreadable."""
    state, adapter = _adapter()
    adapter.apply(AgentStartEvent())

    adapter.apply(
        MessageUpdateEvent(
            message=_partial(),
            stream_event=ThinkingStartEvent(content_index=0, partial=_partial()),
        )
    )
    assert state.status == "thinking"

    adapter.apply(
        MessageUpdateEvent(
            message=_partial(),
            stream_event=ThinkingEndEvent(content_index=0, content="...", partial=_partial()),
        )
    )
    assert state.status == "working"


def test_token_counts_reach_the_ui_from_message_end() -> None:
    """The adapter ignored `message_end` entirely, which is the only event
    carrying `usage` — so the REPL could show cost and the UI could not."""
    state, adapter = _adapter()
    message = AssistantMessage(
        model="m",
        stop_reason="stop",
        usage=Usage(input=120, output=30, cache_read=90),
    )

    adapter.apply(MessageEndEvent(message=message, stream_event=_done(message)))

    assert (state.tokens_in, state.tokens_out, state.tokens_cached) == (120, 30, 90)


# ------------------------------------------------------------------ prompt history


def test_up_walks_back_through_what_you_typed() -> None:
    state = TuiState()
    for prompt in ("first", "second", "third"):
        state.remember(prompt)

    assert state.previous_prompt("") == "third"
    assert state.previous_prompt("") == "second"
    assert state.previous_prompt("") == "first"
    assert state.previous_prompt("") == "first", "the oldest is a floor, not a wrap"


def test_walking_back_and_forward_returns_the_draft_you_interrupted() -> None:
    """**The reason the up-arrow is safe to press.** Without this it is a
    destructive key: it replaces whatever you were halfway through typing and
    gives you no way back. Pi captures the draft for the same reason
    (`editor.ts:435-437`)."""
    state = TuiState()
    state.remember("an old prompt")

    assert state.previous_prompt("half-written") == "an old prompt"
    assert state.next_prompt() == "half-written"
    assert state.next_prompt() is None, "already back at the draft; nothing further forward"


def test_sending_the_same_prompt_twice_stores_it_once() -> None:
    """Otherwise pressing enter twice means pressing up twice to get past it."""
    state = TuiState()
    state.remember("again")
    state.remember("again")

    assert state.history == ["again"]


def test_history_stops_growing_at_the_cap() -> None:
    state = TuiState()
    for index in range(HISTORY_LIMIT + 25):
        state.remember(f"prompt {index}")

    assert len(state.history) == HISTORY_LIMIT
    assert state.history[-1] == f"prompt {HISTORY_LIMIT + 24}", "the newest survives"
    assert state.history[0] == "prompt 25", "the oldest is what gets dropped"


def test_sending_a_prompt_stops_browsing() -> None:
    """Otherwise the next up-arrow resumes from wherever the last one left off,
    which is not where anyone expects to land."""
    state = TuiState()
    state.remember("one")
    state.previous_prompt("")

    state.remember("two")

    assert state.cursor is None
    assert state.previous_prompt("") == "two"


async def test_a_modal_owns_the_keyboard_while_it_is_up(tmp_path: Path) -> None:
    """**`/login` was unusable and every unit test passed.**

    `OmegaApp.on_key` claims `up`, `down` and `escape` for the prompt and calls
    `event.prevent_default()`, which suppresses Textual's binding dispatch. With
    a modal open that meant `up` never reached its `OptionList` and `escape`
    never reached the modal's Cancel binding: you could open the provider picker
    and then neither move nor leave it.

    Found by pushing the same `ChoiceModal` onto a bare `App`, where it behaved
    correctly — which is what proved the modal was not at fault. The assertions
    below are the three behaviours that were broken, in the app that broke them.
    """
    from textual.widgets import OptionList

    from omega_coding import auth
    from omega_coding.commands import CommandContext
    from omega_coding.tui.app import OmegaApp
    from omega_coding.tui.login import ChoiceModal
    from omega_coding.tui.widgets import PromptInput

    harness = Harness(
        provider=auth.LoginRequiredProvider("anthropic"),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )
    context = CommandContext(
        harness=harness,
        store=None,
        tracker=None,  # type: ignore[arg-type]
        model="m",
        system="s",
        tools=[],
        hooks=harness.hooks,
        reload_provider=lambda: True,
    )
    app = OmegaApp(harness, context=context, provider_name="anthropic")

    async with app.run_test() as pilot:
        app.query_one(PromptInput).value = "/login"
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ChoiceModal), "the picker never opened"

        options = app.screen.query_one(OptionList)
        assert options.highlighted == 0

        await pilot.press("down")
        await pilot.pause(0.12)
        assert options.highlighted == 1, "down did not move the selection"

        await pilot.press("up")
        await pilot.pause(0.12)
        assert options.highlighted == 0, "up did not move the selection"

        await pilot.press("escape")
        await pilot.pause(0.3)
        assert not isinstance(app.screen, ChoiceModal), "escape did not close the picker"


def _app(tmp_path: Path, turns: list[Any] | None = None, **kwargs: Any) -> Any:
    from omega_ai.fake import FakeProvider, text_turn
    from omega_coding.tui.app import OmegaApp

    return OmegaApp(
        Harness(
            provider=FakeProvider(turns if turns is not None else [text_turn("ok")]),
            model="claude-sonnet-5",
            system="s",
            tools=build_tools(tmp_path),
        ),
        **kwargs,
    )


async def test_one_ctrl_c_arms_and_the_second_exits(tmp_path: Path) -> None:
    """**The middle revision of this key was the dangerous one.**

    Originally Ctrl-C on an idle prompt did nothing and the way out was Ctrl-Q,
    which you had to already know. The fix made an idle Ctrl-C exit immediately —
    trading that for something worse, because Ctrl-C is the reflex for "stop
    that" and there is no undo for a closed session.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)

        await pilot.press("ctrl+c")
        await pilot.pause(0.15)
        assert app.is_running, "one Ctrl-C closed the session"
        assert "again to exit" in " ".join(row.text for row in app.state.rows)

        await pilot.press("ctrl+c")
        await pilot.pause(0.3)
        assert not app.is_running, "the second Ctrl-C did not exit"


async def test_ctrl_d_exits_on_the_first_press(tmp_path: Path) -> None:
    """`priority=True` is what makes this work at all.

    Textual's `Input` binds `delete,ctrl+d` to `delete_right`, and the prompt has
    focus for almost the whole session — so without the priority flag the key
    deletes a character and the app never sees it. Measured before the fix: the
    app stayed open.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await pilot.press("ctrl+d")
        await pilot.pause(0.3)
        assert not app.is_running


async def test_ctrl_q_no_longer_exits(tmp_path: Path) -> None:
    """Textual ships `ctrl+q → quit` on `App` itself, so *not* binding it leaves
    it quitting anyway — a third exit with none of the confirmation the other
    two insist on."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await pilot.press("ctrl+q")
        await pilot.pause(0.3)
        assert app.is_running, "ctrl+q still exits"
        assert "ctrl+c twice" in " ".join(row.text for row in app.state.rows)


async def test_the_splash_becomes_a_compact_header(tmp_path: Path) -> None:
    """A splash earns six rows exactly once — before you have asked anything.

    Afterwards those rows belong to the answer, so it shrinks to the three facts
    worth keeping: which build, which model, and where. Mounted **once**, which
    is the assertion that matters: `remove()` is deferred in Textual, so a guard
    keyed off the splash still being present mounted a second header and raised
    `DuplicateIds` mid-turn.
    """
    from omega_ai.fake import text_turn
    from omega_coding.tui.widgets import PromptInput, Splash

    app = _app(tmp_path, [text_turn("one"), text_turn("two")], provider_name="anthropic")
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        assert app.query(Splash), "the splash never appeared"

        for prompt in ("hi", "again"):
            app.query_one(PromptInput).value = prompt
            await pilot.press("enter")
            await pilot.pause(0.8)

        assert not app.query(Splash), "the splash outstayed its welcome"
        assert len(app.query("#header")) == 1, "the header was mounted more than once"


async def test_ctrl_o_shows_the_facts_when_there_is_nothing_to_expand(
    tmp_path: Path,
) -> None:
    """It was never broken — it was dead half the time.

    Expanding tool rows works (one press opens, the next closes). But before the
    first tool call there are no rows, which is most of a session and exactly
    when someone new presses it. Pi's `ctrl+o` means "more", so the fallback
    shows the startup facts rather than nothing.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await pilot.press("ctrl+o")
        await pilot.pause(0.25)

        shown = " ".join(row.text for row in app.state.rows)
        assert "claude-sonnet-5" in shown, "ctrl+o showed nothing at all"


async def test_a_queued_message_comes_back_for_editing(tmp_path: Path) -> None:
    """**The queue was write-only**, so a mid-turn typo was final.

    A correction sent while the model is working committed to the next request,
    and the only way out was cancelling the whole turn. Up-arrow on an empty
    prompt now takes the last one back, exactly as typed.
    """
    from omega_coding.tui.widgets import PromptInput

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        app.state.running = True
        app.harness.queue_steering("use pytest not unittset")

        field = app.query_one(PromptInput)
        field.value = ""
        await pilot.press("up")
        await pilot.pause(0.25)

        assert field.value == "use pytest not unittset", "the queued message did not come back"
        assert app.harness.unqueue_steering() is None, "it is still queued as well"


async def test_recall_does_not_eat_what_you_are_typing(tmp_path: Path) -> None:
    """Guarded on an empty prompt. Up-arrow with text in the box has always
    meant history, and a queued message silently replacing a half-typed line
    would be the worst kind of helpful."""
    from omega_coding.tui.widgets import PromptInput

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        app.harness.queue_steering("queued")

        field = app.query_one(PromptInput)
        field.value = "half typed"
        await pilot.press("up")
        await pilot.pause(0.2)

        assert field.value != "queued", "it overwrote what was being typed"
        assert app.harness.unqueue_steering() == "queued", "it was consumed anyway"


def test_no_theme_draws_a_rule_down_the_left_of_a_row() -> None:
    """**Reported from a screenshot**, which is the only way it could be.

    A `border` colour on a role draws a vertical bar beside every row of that
    kind. `user` already used `""`, so user messages had none — but `notice` kept
    one in all four themes, which put a bar beside every error and every
    "unknown command". Four themes meant four places to miss.

    The mechanism stays: `""` is the already-supported "no rule" value and the
    code path for it is tested. Wanting the bar back is a colour in a JSON file,
    not a code change.
    """
    import json

    themes = Path("src/omega_coding/tui/themes")
    assert list(themes.glob("*.json")), "no themes found — the check would pass vacuously"

    for path in sorted(themes.glob("*.json")):
        roles = json.loads(path.read_text())["roles"]
        drawn = {name: spec["border"] for name, spec in roles.items() if spec["border"]}
        assert not drawn, f"{path.name} still draws a rule for {sorted(drawn)}"


def test_the_home_directory_is_not_shown_as_a_dot() -> None:
    """`Path.home().relative_to(home)` is `.`, so the naive form rendered the
    home directory as `~/.` — visible to anyone who ran omega from `~`, and
    invisible to a suite whose every test runs in a temporary directory."""
    from omega_coding.tui import banner

    assert banner.short_path(Path.home()) == "~"
    assert banner.short_path(Path.home() / "code" / "thing") == "~/code/thing"


def test_the_header_does_not_invent_a_provider_called_none() -> None:
    """`"none"` is the status column's word for "signed in to nothing", and it
    reads correctly under a label. Beside a model name it reads as a provider
    *called* none — so the header says what it means instead."""
    from omega_coding.tui import banner

    def header(provider: str) -> str:
        return banner.compact_header(
            version="v0",
            model="claude-sonnet-5",
            provider=provider,
            path="~",
            colour="#fff",
            muted="#888",
        )

    assert "not signed in" in header("none")
    assert "not signed in" in header("")
    assert "· anthropic" in header("anthropic")
    assert "not signed in" not in header("anthropic")


async def test_the_exit_keys_are_the_ones_the_footer_advertises(tmp_path: Path) -> None:
    """**A footer that omits the key you press is worse than no footer.**

    It read `^d Exit  ^o Expand tools` — no Ctrl-C, the key that stops a turn and
    is half the exit pair. Two causes, and only the second was findable by
    reading the resolved bindings rather than the declared ones:

    * Textual marks its own `ctrl+c` binding `system=True`, which hides it.
    * With that cleared, the *active* `ctrl+c` still came from `PromptInput` —
      Textual's `Input` binds `ctrl+c,super+c` to `copy` with `show=False`, and
      the prompt holds focus almost always.

    `priority=True` moves resolution back to the app, which also makes the key
    mean one thing everywhere instead of "copy" inside the box and "stop"
    outside it.

    Asserted on `active_bindings` — what the footer actually renders from — not
    on `BINDINGS`, which is what looked correct the whole time it was wrong.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.3)

        resolved = {
            str(key): active
            for key, active in app.active_bindings.items()
            if str(key) in {"ctrl+c", "ctrl+d", "ctrl+o"}
        }

        assert set(resolved) == {"ctrl+c", "ctrl+d", "ctrl+o"}, "a key stopped resolving"
        for key, active in resolved.items():
            assert active.binding.show, f"{key} is hidden from the footer"
            assert type(active.node).__name__ == "OmegaApp", (
                f"{key} resolves from {type(active.node).__name__}, so the footer "
                "shows that widget's description instead of omega's"
            )

        assert resolved["ctrl+c"].binding.description == "Stop / exit"
        assert resolved["ctrl+d"].binding.description == "Exit"
