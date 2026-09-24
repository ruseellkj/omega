"""The parts of the screen you actually touch.

These need Textual's pilot rather than the state tests' bare adapter, because
every one of them is about a widget: what a keypress reaches, what survives a
redraw, what is on screen at all. `state.py` and `adapter.py` stay Textual-free
so the other tests can run headless; this file is where that stops being
possible, and that is the intended division.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from textual import events
from textual.containers import VerticalScroll
from textual.widgets.input import Selection

from conftest import RecordingClipboard  # the suite's own conftest
from omega_agent.agent_events import AgentStartEvent, ToolExecutionStartEvent
from omega_agent.harness import Harness
from omega_agent.session import JsonlSessionStore
from omega_agent.types import ToolCall
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_coding.builtin_tools import build_tools
from omega_coding.commands import CommandContext
from omega_coding.cost import CostTracker
from omega_coding.status import FRAMES
from omega_coding.tui import banner, themes
from omega_coding.tui.app import OmegaApp
from omega_coding.tui.banner import BANNER
from omega_coding.tui.widgets import (
    CommandPalette,
    InterruptBar,
    PromptBox,
    PromptInput,
    Splash,
    StatusBar,
    ToolRow,
    TranscriptRow,
)


class _Slow:
    """A provider with a pause in it, so a turn is still running when a key
    arrives. `FakeProvider` finishes in under a millisecond, and every test that
    tries to catch a turn mid-flight without this is racing the clock."""

    def __init__(self, inner: FakeProvider, delay: float = 0.25) -> None:
        self._inner = inner
        self._delay = delay

    async def stream_response(self, **kwargs: object) -> AsyncIterator[object]:
        async for event in self._inner.stream_response(**kwargs):  # type: ignore[arg-type]
            await asyncio.sleep(self._delay)
            yield event


def _app(tmp_path: Path) -> OmegaApp:
    harness = Harness(
        provider=FakeProvider(
            [tool_turn("run_shell", {"command": "echo hi"}), text_turn("All done.")]
        ),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )
    return OmegaApp(harness)


async def _type(pilot: object, app: OmegaApp, text: str) -> None:
    """Set the input and fire the change event the palette listens for."""
    field = app.query_one("Input")
    field.value = text  # type: ignore[attr-defined]
    await pilot.pause()  # type: ignore[attr-defined]


# -------------------------------------------------------------- the / palette


async def test_typing_a_slash_opens_the_list_of_commands(tmp_path: Path) -> None:
    """**The ask, literally: "ASAP I type / it should show it".**

    Nothing listed the commands before — `app.py` never even called `dispatch`,
    so `/help` inside the UI did nothing at all.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        palette = app.query_one(CommandPalette)
        assert not palette.has_class("visible"), "closed until asked for"

        await _type(pilot, app, "/")

        assert palette.has_class("visible")
        assert palette.option_count >= 9


async def test_the_list_narrows_as_you_type(tmp_path: Path) -> None:
    """Prefix match, in `COMMANDS` order, so `/c` always lists the same five the
    same way. `config` joined the list with `/config`."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await _type(pilot, app, "/c")

        palette = app.query_one(CommandPalette)
        shown = [palette.get_option_at_index(i).id for i in range(palette.option_count)]
        assert shown == ["clear", "cost", "context", "compact", "config"]


async def test_the_list_closes_once_the_command_is_complete(tmp_path: Path) -> None:
    """A space means an argument follows, and `/resume ` wants a session id —
    not nine more command names. `dispatch` partitions on that same space."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await _type(pilot, app, "/resume")
        assert app.query_one(CommandPalette).has_class("visible")

        await _type(pilot, app, "/resume ")

        assert not app.query_one(CommandPalette).has_class("visible")


async def test_tab_completes_the_highlighted_command(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await _type(pilot, app, "/comp")
        await pilot.press("tab")

        assert app.query_one("Input").value == "/compact "  # type: ignore[attr-defined]
        assert not app.query_one(CommandPalette).has_class("visible")


async def test_an_unknown_command_leaves_the_list_closed(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await _type(pilot, app, "/zzzz")

        assert not app.query_one(CommandPalette).has_class("visible")


# ----------------------------------------------------------- arrows, and who gets them


async def test_up_recalls_the_previous_prompt(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.state.remember("the thing I asked before")

        await pilot.press("up")

        assert app.query_one("Input").value == "the thing I asked before"  # type: ignore[attr-defined]


async def test_up_moves_in_the_palette_rather_than_history_when_it_is_open(
    tmp_path: Path,
) -> None:
    """**Tau's precedence** (`tui/app.py:4851-4905`), and the right way round:
    while a list is open, up obviously means "move in this list".

    Worth a test because getting it backwards is invisible until the one moment
    it matters — you open the palette, press up out of habit, and your prompt is
    replaced by something you typed yesterday.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.state.remember("an old prompt")
        await _type(pilot, app, "/c")
        palette = app.query_one(CommandPalette)
        palette.highlighted = 1

        await pilot.press("up")

        assert palette.highlighted == 0, "the palette moved"
        assert app.query_one("Input").value == "/c", "history did not fire"  # type: ignore[attr-defined]


# ------------------------------------------------------------------ tool rows


async def test_a_tool_call_renders_as_one_row_you_can_open(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.query_one("Input").value = "do the thing"  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause(0.4)

        tools = app.query(ToolRow)
        assert len(tools) == 1, "one call is one row"
        row = tools.first()
        assert row.collapsed, "a receipt by default"
        assert "ran echo hi" in str(row.title)
        assert "✓" in str(row.title)


async def test_an_expanded_tool_row_survives_the_next_event(tmp_path: Path) -> None:
    """**The reason the redraw had to stop being a rebuild.**

    `refresh_view` used to call `remove_children()` and mount everything again.
    That is fine for `Static` rows and fatal for a collapsible one: the widget
    holding "the user opened this" is thrown away, so the next streamed token
    snaps it shut. Verified by reverting — restoring the rebuild fails here.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.query_one("Input").value = "do the thing"  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause(0.4)

        row = app.query(ToolRow).first()
        row.collapsed = False

        # Anything at all that provokes a redraw.
        app.state.add("notice", "something else happened")
        app.refresh_view()
        await pilot.pause()

        assert app.query(ToolRow).first().collapsed is False, "the redraw closed it"


# ---------------------------------------------------------------- splash, theme


async def test_the_banner_shows_on_an_empty_transcript_and_then_gets_out_of_the_way(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        assert app.query(Splash)

        app.state.add("user", "hello")
        app.refresh_view()
        await pilot.pause()

        assert not app.query(Splash)
        assert len(app.query(TranscriptRow)) == 1


async def test_switching_theme_repaints_rows_that_already_exist(tmp_path: Path) -> None:
    """A theme that only applied to future rows would look broken exactly once —
    on the switch, which is the only moment anyone is looking at it."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.state.add("user", "hello")
        app.refresh_view()
        await pilot.pause()
        before = app.query(TranscriptRow).first().styles.background

        await app._run_command("/theme oxblood-light")
        await pilot.pause()

        after = app.query(TranscriptRow).first().styles.background
        assert before != after
        assert app._theme.name == "oxblood-light"


async def test_an_unknown_theme_says_so_instead_of_crashing(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await app._run_command("/theme nope")
        await pilot.pause()

        assert app.state.rows[-1].is_error
        assert "nope" in app.state.rows[-1].text


def _app_with_commands(tmp_path: Path, provider: Any = None) -> OmegaApp:
    """Like `_app`, but wired for `/` commands that need `context.harness` and
    `context.store` — `/clear` among them, which `dispatch` refuses to touch
    without a `CommandContext`."""
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    harness = Harness(
        provider=provider if provider is not None else FakeProvider([text_turn("ok")] * 20),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
        store=store,
    )
    context = CommandContext(
        harness=harness,
        store=store,
        tracker=CostTracker(),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
        hooks=harness.hooks,
    )
    return OmegaApp(harness, context=context)


async def test_clear_empties_the_transcript_on_screen(tmp_path: Path) -> None:
    """**The reported bug:** `/clear` started a fresh session underneath, but the
    old turns stayed on screen — `_run_command` only ever appends a notice row,
    it never touches `state.rows`, so `/clear` looked like it did nothing.
    """
    app = _app_with_commands(tmp_path)
    async with app.run_test() as pilot:
        app.state.add("user", "hello")
        app.state.add("assistant", "hi there")
        app.refresh_view()
        await pilot.pause()
        assert len(app.query(TranscriptRow)) == 2

        await app._run_command("/clear")
        await pilot.pause()

        # Only the "Cleared." notice should remain — the two old turns, on
        # screen and in state alike, must be gone rather than just superseded.
        assert not any(row.kind in ("user", "assistant") for row in app.state.rows)
        shown = [str(row.render()) for row in app.query(TranscriptRow)]
        assert not any("hello" in text or "hi there" in text for text in shown)
        # The notice must not be drawn in the widget "hello" used to occupy:
        # `refresh_view` updates rows in place by index, and `row-user` is a
        # class set once at construction, so a reused widget kept your padding.
        assert all("row-user" not in row.classes for row in app.query(TranscriptRow))


async def _save_turns(harness: Harness, questions: int) -> str:
    """Run real turns so a session exists on disk, and return its id."""
    for number in range(questions):
        async for _ in harness.run(f"question {number}"):
            pass
    assert harness.session_id is not None
    return harness.session_id


async def test_resume_puts_the_stored_conversation_on_screen(tmp_path: Path) -> None:
    """**The reported bug:** `/resume` loaded the messages into the harness and
    said how many there were, and the screen showed none of them — so you were
    continuing a conversation you could not read.
    """
    app = _app_with_commands(tmp_path)
    async with app.run_test() as pilot:
        earlier = await _save_turns(app.harness, 12)
        app.harness.start_new_session()
        app.state.add("user", "a question from the session being left")
        app.refresh_view()
        await pilot.pause()

        await app._run_command(f"/resume {earlier}")
        # Longer than a bare pause: `scroll_end` lands after the layout pass for
        # the rows just mounted. Measured: `scroll_y` is 0 on the next frame and
        # at the bottom by 0.1s.
        await pilot.pause(0.3)

        shown = [str(row.render()) for row in app.query(TranscriptRow)]
        assert sum("question" in text for text in shown) == 12, shown
        assert not any("being left" in text for text in shown), "the old screen survived"
        assert "Resumed" in shown[-1]

        # Twelve turns do not fit in 24 rows. Scrolling is the pane's job; what
        # this checks is that the rows are really in it, and that you land on
        # the latest turn with the older ones above.
        pane = app.query_one("#transcript", VerticalScroll)
        assert pane.max_scroll_y > 0
        assert pane.scroll_y == pane.max_scroll_y


async def test_starting_on_a_resumed_session_shows_it(tmp_path: Path) -> None:
    """`omega --resume <id>` and `--continue` resume in `cli.py`, before the app
    exists, and print the count to a terminal the app then takes over. The app
    has to draw what the harness already holds, or you start on a blank splash."""
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    first = Harness(
        provider=FakeProvider([text_turn("the earlier answer")]),
        model="m",
        system="s",
        tools=[],
        store=store,
    )
    earlier = await _save_turns(first, 1)
    harness = Harness(provider=FakeProvider([]), model="m", system="s", tools=[], store=store)
    harness.resume(earlier)

    app = OmegaApp(harness)
    async with app.run_test() as pilot:
        await pilot.pause()
        shown = [str(row.render()) for row in app.query(TranscriptRow)]
        assert any("question 0" in text for text in shown)
        assert any("the earlier answer" in text for text in shown)
        assert not app.query(Splash)


async def _ask(pilot: Any, app: OmegaApp, text: str, wait: float = 0.3) -> None:
    app.query_one("Input").value = text  # type: ignore[attr-defined]
    await pilot.press("enter")
    await pilot.pause(wait)


def _user_rows_on_screen(app: OmegaApp) -> list[str]:
    return [str(row.render()) for row in app.query(TranscriptRow) if "row-user" in row.classes]


async def test_rewind_takes_the_dropped_question_off_the_screen(tmp_path: Path) -> None:
    """**Same bug as `/resume`, found while fixing it.** `/rewind` dropped the
    last question from the harness and left it on screen, so the conversation
    you could read was not the one the next turn would continue from."""
    app = _app_with_commands(tmp_path)
    async with app.run_test() as pilot:
        for question in ("first", "second", "third"):
            await _ask(pilot, app, question)
        assert len(_user_rows_on_screen(app)) == 3

        await app._run_command("/rewind")
        await pilot.pause()

        assert _user_rows_on_screen(app) == ["❯ first", "❯ second"]
        assert "Rewound" in app.state.rows[-1].text


async def test_commands_that_rewrite_the_conversation_wait_for_the_turn(tmp_path: Path) -> None:
    """They change the conversation the running loop is still appending to.

    Measured before the guard, for `/rewind` mid-turn: the question being
    answered was cut, and its answer was saved anyway, leaving two assistant
    messages in a row on disk. `/clear` and `/resume` would write the rest of
    the turn into the *new* session. `/compact` is not here because the same
    measurement found nothing wrong: the in-flight question and its answer
    both survived.
    """
    app = _app_with_commands(tmp_path, _Slow(FakeProvider([text_turn("slow answer")])))
    async with app.run_test() as pilot:
        await _ask(pilot, app, "a long question")
        assert app.state.running

        for command in ("/clear", "/resume anything", "/rewind"):
            await app._run_command(command)
            assert app.state.rows[-1].is_error, command
            assert command.split()[0] in app.state.rows[-1].text

        await pilot.pause(2.0)
        assert not app.state.running
        assert [m.role for m in app.harness.messages] == ["user", "assistant"]


# --------------------------------------------------------------- pasting


async def test_a_multi_line_paste_keeps_every_line(tmp_path: Path) -> None:
    """**The reported bug: three lines pasted, one line kept.**

    Textual's single-line `Input` is `event.text.splitlines()[0]`
    (`widgets/_input.py:756` in 8.2.8) — sensible for a form field, wrong for a
    prompt, and silent either way. Pasting a traceback gave omega the first line.

    Verified by measurement before the fix: `Paste("line one\\nline two\\nline
    three")` left `'line one'` in the field.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        field = app.query_one(PromptInput)
        field.focus()
        pasted = "def f():\n    return 1\n\nf()"

        field.post_message(events.Paste(pasted))
        await pilot.pause()

        assert field.expand_pastes(field.value) == pasted, "the text must survive intact"


async def test_a_big_paste_collapses_to_a_marker(tmp_path: Path) -> None:
    """The shape both references use, and the one the user already sees in their
    own editor: `[Pasted text #57 +57 lines]`.

    Tau collapses over 2,000 characters (`tui/app.py:473`); Pi over 10 lines or
    1,000 characters (`editor.ts:1198-1212`). omega collapses on *any* newline
    because its prompt is one line — a newline cannot be displayed here, so it
    has to be stood for rather than tidied away.

    The wording is Pi's, `[paste #1 …]`, asked for by name. It was `[pasted #1 …]`.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        field = app.query_one(PromptInput)
        field.focus()

        field.post_message(events.Paste("a\nb\nc\nd"))
        await pilot.pause()

        assert field.value == "[paste #1 +4 lines]"
        assert len(field.value) < 30, "the box stays readable"


async def test_a_short_single_line_paste_is_left_alone(tmp_path: Path) -> None:
    """A marker for `src/loop.py` would be worse than the path itself."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        field = app.query_one(PromptInput)
        field.focus()

        field.post_message(events.Paste("src/omega_agent/loop.py"))
        await pilot.pause()

        assert field.value == "src/omega_agent/loop.py"


async def test_a_paste_replaces_the_selected_text(tmp_path: Path) -> None:
    """**A bug the paste override introduced.** Textual's own `_on_paste` replaces
    the selection (`widgets/_input.py:759-762` in 8.2.8); omega's override called
    `insert_text_at_cursor`, so selecting a word and pasting over it put the
    paste *beside* it and kept the word.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        field = app.query_one(PromptInput)
        field.focus()
        field.value = "fix the bug in loop.py"
        field.selection = Selection(12, 22)  # "in loop.py"
        await pilot.pause()

        field.post_message(events.Paste("in harness.py"))
        await pilot.pause()

        assert field.value == "fix the bug in harness.py"
        assert field.cursor_position == len(field.value), "the cursor ends after the paste"


async def test_coming_back_to_the_prompt_does_not_select_the_draft(tmp_path: Path) -> None:
    """**`select_on_focus` defaults to True**, so every `.focus()` — after the
    interrupt bar, after a modal, after a click elsewhere — selected the whole
    draft, and the next keystroke replaced all of it. Measured before the fix:
    `Selection(start=0, end=15)` after a refocus.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        field = app.query_one(PromptInput)
        field.value = "half a thought"
        field.cursor_position = len(field.value)
        app.query_one("#transcript").focus()
        await pilot.pause()

        field.focus()
        await pilot.pause()

        assert field.selection.is_empty, "the draft must not be selected"
        await pilot.press("s")
        assert field.value == "half a thoughts"


async def test_a_paste_reaches_the_prompt_when_something_else_has_focus(
    tmp_path: Path,
) -> None:
    """**Paste went to whatever was focused, and a click moves focus.** Textual
    forwards a `Paste` only to the focused widget (`app.py:4142`), so clicking the
    transcript and then pressing Cmd+V delivered it to a scroll view that has no
    use for it. Nothing appeared and nothing said why. Tau reroutes the same way
    (`tui/app.py:3637-3656`).
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.query_one("#transcript").focus()
        await pilot.pause()

        app.post_message(events.Paste("explain src/loop.py"))
        await pilot.pause()

        field = app.query_one(PromptInput)
        assert field.value == "explain src/loop.py"
        assert field.has_focus, "typing carries on where the paste went"


async def test_the_prompt_sent_to_the_model_is_the_real_text(tmp_path: Path) -> None:
    """The marker is a display convenience. It must never reach the provider."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        field = app.query_one(PromptInput)
        field.focus()
        field.post_message(events.Paste("first\nsecond"))
        await pilot.pause()
        field.insert_text_at_cursor(" — explain this")

        await pilot.press("enter")
        await pilot.pause(0.3)

        sent = [row.text for row in app.state.rows if row.kind == "user"]
        assert sent, "the turn started"
        assert sent[0] == "first\nsecond — explain this"
        assert "paste #" not in sent[0]


async def test_pasting_the_same_text_twice_shows_it_in_full(tmp_path: Path) -> None:
    """**The ask: "when pasted twice then it expands that to whole text".**

    Neither reference does this. The rule chosen: pasting text whose marker is
    still in the box replaces that marker with the text, cursor at its end. A
    third paste collapses again, because there is no marker left to expand.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        field = app.query_one(PromptInput)
        field.focus()
        pasted = "def f():\n    return 1"

        field.post_message(events.Paste(pasted))
        await pilot.pause()
        assert field.value == "[paste #1 +2 lines]"
        field.insert_text_at_cursor(" explain")

        field.post_message(events.Paste(pasted))
        await pilot.pause()
        assert field.value == f"{pasted} explain", "the marker became the text, in place"
        assert field.cursor_position == len(pasted), "the cursor ends after what expanded"

        field.post_message(events.Paste(pasted))
        await pilot.pause()
        assert "[paste #2 +2 lines]" in field.value, "a third paste collapses again"
        assert field.expand_pastes(field.value) == f"{pasted}{pasted} explain"


async def test_an_expanded_paste_draws_its_line_breaks_as_glyphs(tmp_path: Path) -> None:
    """`Input` put a raw `\\n` in its one-row strip (measured), which a real
    terminal acts on. It must be drawn as `↵` and counted as one cell, both where
    the cursor is drawn and where a click lands, or the two drift apart by one
    cell per line break."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        field = app.query_one(PromptInput)
        field.focus()
        field.value = "ab\ncd\tef"
        field.cursor_position = 6  # just before "e"
        await pilot.pause()

        drawn = field.render_line(0).text
        assert "\n" not in drawn and "\t" not in drawn
        assert drawn.startswith("ab↵cd⇥ef")
        assert field.value == "ab\ncd\tef", "the value keeps the real characters"
        assert field.cursor_screen_offset.x - field.content_region.x == 6

        await pilot.click(field, offset=(4, 0))  # the "d"
        assert field.cursor_position == 4


async def test_backspace_takes_a_marker_whole(tmp_path: Path) -> None:
    """One backspace used to leave `[paste #1 +2 lines`, which matches nothing,
    so the paste was dropped and the fragment sent. Pi treats markers as one unit
    (`editor.ts:34-35`)."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        field = app.query_one(PromptInput)
        field.focus()
        field.value = "see "
        field.cursor_position = 4
        field.post_message(events.Paste("a\nb"))
        await pilot.pause()

        await pilot.press("backspace")
        assert field.value == "see "

        field.post_message(events.Paste("a\nb"))
        await pilot.pause()
        field.cursor_position = 4
        await pilot.press("delete")
        assert field.value == "see "


# ------------------------------------------------------------- copying


async def _drag(pilot: Any, widget: Any, start: int, end: int) -> None:
    """Press at `start`, move to `end`, release there — a mouse selection."""
    await pilot.mouse_down(widget, offset=(start, 0))
    await pilot.hover(widget, offset=(end, 0))
    await pilot.mouse_up(widget, offset=(end, 0))
    await pilot.pause()
    await pilot.pause()  # the copy runs as a worker


async def _with_answer(pilot: Any, app: OmegaApp, text: str) -> TranscriptRow:
    app.state.add("assistant", text)
    app.refresh_view()
    await pilot.pause()
    return app.query(TranscriptRow).last()


async def test_selecting_transcript_text_copies_it_and_says_how_much(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    """**The ask: "any selected text should be automatically copied", with the
    count on screen.** Before this, ctrl+c was the only copy key, and the app
    binds it at priority to *stop*, so a highlight could not be copied at all."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        row = await _with_answer(pilot, app, "hello world from omega")

        await _drag(pilot, row, 0, 11)

        assert len(fake_clipboard.writes) == 1
        copied = fake_clipboard.writes[0]
        assert copied.startswith("hello world")
        assert app.query_one(StatusBar).flashing == (
            f"copied {len(copied)} chars to clipboard · disable auto-copy in /config"
        )


async def test_a_double_click_copies_the_whole_row(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    """Textual selects a whole widget on a double-click (`widget.py:4698`) and
    posts no `TextSelected` for it, so a copy that listened only for that
    missed it."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        row = await _with_answer(pilot, app, "the whole answer")

        await pilot.click(row, offset=(2, 0), times=2)
        await pilot.pause()
        await pilot.pause()

        assert fake_clipboard.writes[-1:] == ["the whole answer"]


async def test_a_click_in_the_transcript_hands_the_keyboard_back(tmp_path: Path) -> None:
    """Clicking the transcript moved focus to it, after which typing and pasting
    both went nowhere. Tau returns focus to its prompt on click the same way
    (`tui/app.py:3663-3675`)."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        row = await _with_answer(pilot, app, "an answer")

        await pilot.click(row)
        await pilot.press("x")

        assert app.query_one(PromptInput).value == "x"


async def test_a_drag_across_rows_leaves_the_keyboard_in_the_prompt(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    """A press focused the transcript pane, and a drag ending on another row
    produces no `Click` to hand focus back (`app.py:4084-4116`). So after
    selecting across two rows, typing went nowhere."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        first = await _with_answer(pilot, app, "first answer")
        second = await _with_answer(pilot, app, "second answer")

        await pilot.mouse_down(first, offset=(0, 0))
        await pilot.hover(second, offset=(6, 0))
        await pilot.mouse_up(second, offset=(6, 0))
        await pilot.pause()
        await pilot.pause()
        await pilot.press("x")

        assert "first answer" in fake_clipboard.writes[-1], "it copied across both rows"
        assert app.query_one(PromptInput).value == "x"


async def test_a_selection_holds_the_view_while_the_turn_streams(tmp_path: Path) -> None:
    """Every event scrolled to the bottom, which pulled text out from under a
    drag. While something is selected, the view stays put."""
    app = _app(tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        for index in range(30):
            app.state.add("assistant", f"line {index}")
        app.refresh_view()
        await pilot.pause()
        pane = app.query_one("#transcript", VerticalScroll)
        pane.scroll_home(animate=False)
        await pilot.pause()
        row = app.query(TranscriptRow).first()
        await _drag(pilot, row, 0, 4)
        assert app.screen.selections, "the drag selected something"

        app.state.add("assistant", "one more")
        app.refresh_view()
        # `scroll_end` lands after the next layout pass, not at once. Measured:
        # `scroll_y` is still 0 after one pause and at the bottom after 0.2s, so a
        # single pause here passed with the guard deleted.
        await pilot.pause(0.2)

        assert pane.scroll_y == 0, "the view did not jump to the bottom"


async def test_dragging_inside_the_prompt_copies_it(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    """Screen selection never sees a drag inside an `Input`. Measured: after one,
    `get_selected_text()` is None, so the prompt reports its own."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        field = app.query_one(PromptInput)
        field.value = "draft text here"
        await pilot.pause()

        await _drag(pilot, field, 0, 5)

        assert fake_clipboard.writes == ["draft"]


async def test_auto_copy_turns_off_and_ctrl_c_still_copies(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    """**"All options should exist … but selecting should override all."** With
    auto-copy off a drag only highlights. Then ctrl+c copies instead of stopping,
    clears the highlight, and only the *next* ctrl+c means stop or exit."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await _submit(pilot, app, "/config auto-copy off")
        assert json.loads((tmp_path / "tui.json").read_text())["auto_copy"] is False

        row = await _with_answer(pilot, app, "hello world from omega")
        await _drag(pilot, row, 0, 11)
        assert fake_clipboard.writes == [], "off means a drag only highlights"

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert len(fake_clipboard.writes) == 1
        flashed = app.query_one(StatusBar).flashing
        assert flashed.startswith("copied") and "/config" not in flashed
        assert not app.screen.selections, "copied, then cleared"
        assert not any("ctrl+c again" in row.text for row in app.state.rows)

        await pilot.press("ctrl+c")
        assert any("ctrl+c again" in row.text for row in app.state.rows)


async def test_ctrl_c_copies_a_selection_in_the_prompt(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        field = app.query_one(PromptInput)
        field.focus()
        field.value = "abc def"
        field.selection = Selection(0, 3)

        await pilot.press("ctrl+c")
        await pilot.pause()

        assert fake_clipboard.writes == ["abc"]
        assert field.selection.is_empty
        assert field.value == "abc def"


async def test_a_secret_is_never_copied(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    """**`selected_text` on a password field is the real value** — measured,
    `'sk-secret'`, not the dots. So ctrl+c, cmd+c and cut in the login modal
    must all be refused in the one place every copy passes through."""
    from textual.widgets import Input

    from omega_coding.tui.login import SecretModal

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.push_screen(SecretModal("API key"))
        await pilot.pause()
        secret = app.screen.query_one(Input)
        secret.focus()
        secret.value = "sk-secret"

        for key in ("ctrl+c", "super+c", "ctrl+x"):
            secret.selection = Selection(0, len(secret.value))
            await pilot.press(key)
            await pilot.pause()

        assert fake_clipboard.writes == []
        assert "sk-secret" not in app.clipboard


async def test_ctrl_v_pastes_the_system_clipboard(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    """It pasted Textual's in-process string instead (`_input.py:1129`), which
    held nothing unless omega itself had copied."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        fake_clipboard.contents = "line one\nline two"
        field = app.query_one(PromptInput)
        field.focus()

        await pilot.press("ctrl+v")
        await pilot.pause()
        await pilot.pause()

        assert field.value == "[paste #1 +2 lines]"
        assert field.expand_pastes(field.value) == "line one\nline two"


async def test_ctrl_v_falls_back_to_what_omega_copied(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    """With no way to read the system clipboard, say over SSH, what omega last
    copied is what ctrl+v gives."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.copy_to_clipboard("from the transcript")
        await pilot.pause()
        fake_clipboard.contents = None
        field = app.query_one(PromptInput)
        field.focus()

        await pilot.press("ctrl+v")
        await pilot.pause()
        await pilot.pause()

        assert field.value == "from the transcript"


async def test_ctrl_v_in_the_login_modal_pastes_the_real_key(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    """**A regression auto-copy created, caught in review before it shipped.**
    The login modal's stock `Input` pastes Textual's in-process string on ctrl+v
    (`_input.py:1129`). That string used to be empty, and auto-copy now fills it
    on every selection. So select some transcript, copy a key in the browser,
    `/login`, ctrl+v, and the transcript text went in as the credential, hidden
    behind dots."""
    from textual.widgets import Input

    from omega_coding.tui.login import SecretModal

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        row = await _with_answer(pilot, app, "hello world from omega")
        await _drag(pilot, row, 0, 11)
        assert app.clipboard, "auto-copy filled the in-process clipboard"
        fake_clipboard.contents = "sk-real-key\n"

        app.push_screen(SecretModal("API key"))
        await pilot.pause()
        secret = app.screen.query_one(Input)
        await pilot.press("ctrl+v")
        await pilot.pause()
        await pilot.pause()

        assert secret.value == "sk-real-key"


async def test_a_secret_field_never_falls_back_to_the_in_process_copy(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    """With no system clipboard to read, the prompt may use what omega copied.
    A secret field may not: that text is never a key."""
    from textual.widgets import Input

    from omega_coding.tui.login import SecretModal

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.copy_to_clipboard("transcript text")
        await pilot.pause()
        fake_clipboard.contents = None

        app.push_screen(SecretModal("API key"))
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        await pilot.pause()

        assert app.screen.query_one(Input).value == ""


async def test_sending_a_prompt_lets_the_view_follow_again(tmp_path: Path) -> None:
    """The auto-copy highlight holds the view still. Sending a prompt has to let
    it go, or the answer streams in below the fold and omega looks frozen.

    **Textual already does this, and no omega code does.** Any cursor move in
    an `Input` calls `app.clear_selection()` (`_input.py:518`), and sending
    empties the box, which moves the cursor. An explicit clear on submit was
    written and then removed: with it deleted this test still passed, and a
    probe showed the reset alone clears the selection. So this is the guard.
    Type first, *then* select, then Enter with no keystroke between, because
    that is the order where nothing else would have cleared it.

    Writing this test found a different bug. A drag used to move focus to the
    transcript, so the Enter never reached the prompt. See `Transcript`.
    """
    app = _app(tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        for index in range(30):
            app.state.add("assistant", f"line {index}")
        app.refresh_view()
        await pilot.pause()
        pane = app.query_one("#transcript", VerticalScroll)
        pane.scroll_home(animate=False)
        await pilot.pause()
        field = app.query_one(PromptInput)
        field.value = "next question"
        await pilot.pause()
        await _drag(pilot, app.query(TranscriptRow).first(), 0, 4)
        assert app.screen.selections, "selected after typing, nothing between"

        await pilot.press("enter")
        await pilot.pause(0.5)

        assert not app.screen.selections
        assert pane.scroll_y == pane.max_scroll_y, "the view followed the new answer"


async def test_a_failed_copy_says_so_rather_than_claiming_it(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    """With no tool, OSC 52 is the only route, and nothing can confirm the
    terminal honoured it. So the notice says "sent", never "copied"."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        fake_clipboard.ok = False
        app.copy_to_clipboard("abc")
        await pilot.pause()
        await pilot.pause()

        assert app.query_one(StatusBar).flashing == "sent 3 chars to the terminal's clipboard"
        assert app.clipboard == "abc"


async def test_the_copied_notice_outlasts_the_spinner(tmp_path: Path) -> None:
    """The spinner repaints ten times a second, so a message written once would
    be gone mid-turn in a tenth of a second."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        bar = app.query_one(StatusBar)
        bar.show("thinking")
        bar.flash("copied 3 chars to clipboard")

        await pilot.pause(0.35)
        assert "copied 3 chars" in str(bar.render())

        bar.FLASH_SECONDS = 0.05
        bar.flash("gone soon")
        await pilot.pause(0.3)
        assert "thinking" in str(bar.render()), "the spinner comes back underneath"


async def test_config_lists_its_setting_and_refuses_unknown_ones(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await _submit(pilot, app, "/config")
        assert "auto-copy  on" in app.state.rows[-1].text

        await _submit(pilot, app, "/config colour on")
        assert app.state.rows[-1].is_error

        await _submit(pilot, app, "/config auto-copy")
        assert app.state.rows[-1].text.startswith("auto-copy: off"), "bare name flips it"


async def _submit(pilot: Any, app: OmegaApp, text: str) -> None:
    field = app.query_one(PromptInput)
    field.value = text
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()


# --------------------------------------------------------- the working line


async def test_the_status_says_what_is_happening_and_not_ready(tmp_path: Path) -> None:
    """Two complaints in one: no live step, and "ready" pinned to the box.

    **Driven through the adapter rather than by racing a real turn.** Two earlier
    versions of this test failed for reasons that had nothing to do with the
    status line: `FakeProvider` finishes a turn in under a millisecond, and
    `echo hi` finishes faster than a poll interval, so every `pilot.pause()`
    landed after the moment being asserted on. Slowing the provider only moved
    the race. The path under test is adapter → state → bar, and that is what this
    drives; the spinner's own animation is covered by the test below.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        bar = app.query_one(StatusBar)
        assert "ready" not in str(bar.render()), "idle has nothing to report"

        app.adapter.apply(AgentStartEvent())
        app.adapter.apply(
            ToolExecutionStartEvent(
                tool_call=ToolCall(id="1", name="read_file", arguments={"path": "loop.py"})
            )
        )
        app.refresh_view()
        await pilot.pause()

        seen = str(bar.render())
        assert "reading loop.py" in seen, f"the live step, not a generic label (saw {seen!r})"
        assert any(frame in seen for frame in FRAMES), "and a spinner beside it"


async def test_the_spinner_stops_when_nothing_is_running(tmp_path: Path) -> None:
    """A timer left running redraws ten times a second forever."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.query_one("Input").value = "do the thing"  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause(0.5)

        assert app.state.running is False
        assert not any(frame in str(app.query_one(StatusBar).render()) for frame in FRAMES)


# ----------------------------------------------------------------- layout


async def test_nothing_below_the_transcript_overlaps(tmp_path: Path) -> None:
    """**Found by measuring, and invisible in a screenshot.**

    Three `dock: bottom` siblings do not stack — they all anchor to the same
    edge. PromptBox landed at y=31 height 3 while StatusBar sat at y=32 and the
    Footer at y=33, so the box's bottom rule was painted over by the footer and
    its text row by the status. That is what "the query box is half cut" was.
    """
    app = _app(tmp_path)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        boxes = [
            (type(w).__name__, w.region)
            for w in app.screen.children
            if w.display and w.region.height
        ]

        for (name_a, a), (name_b, b) in zip(boxes, boxes[1:], strict=False):
            assert a.y + a.height <= b.y, f"{name_a} overlaps {name_b}: {a} vs {b}"


async def test_the_prompt_box_is_closed_on_both_edges(tmp_path: Path) -> None:
    """Half cut was literal: `Input` draws its own `tall` border inside ours."""
    app = _app(tmp_path)
    async with app.run_test():
        box = app.query_one(PromptBox)

        assert box.styles.border_top[0] == "solid"
        assert box.styles.border_bottom[0] == "solid"
        inner = app.query_one(PromptInput)
        assert inner.styles.border_top[0] in ("", "none"), "no second frame inside"


# ------------------------------------------------------- the banner and facts


def test_the_letterform_is_the_one_oh_my_logo_emits() -> None:
    """The banner is ANSI Shadow, taken from `npx oh-my-logo "OMEGA" --filled`
    by running it and stripping the escapes.

    Asserted on the glyphs that make it that font rather than on the whole
    string: `╔═╗` and `║` are what give the letters their segmented look, and
    their absence would mean someone had swapped in a plain block font.
    """
    assert all(glyph in BANNER for glyph in "█╗╔═║╚╝"), "not the ANSI Shadow glyph set"
    assert len(BANNER.split("\n")) == 6, "ANSI Shadow is six rows"


def test_the_counters_stay_open() -> None:
    """The hole in the `O` is background, not ink.

    The one assertion that has survived every letterform this banner has had —
    four of them now — because "the word is still readable" is the property none
    of the redesigns were allowed to break.
    """
    first_glyph = [line[:11] for line in BANNER.split("\n")]
    ink = re.compile(r"[█╗╔═║╚╝] {2,}[█╗╔═║╚╝]")

    holed = [line for line in first_glyph if ink.search(line)]
    assert len(holed) >= 2, f"the O closed up: only {len(holed)} rows kept a counter"


async def test_the_banner_follows_the_theme_rather_than_a_baked_palette(
    tmp_path: Path,
) -> None:
    """**The reason the glyphs are pasted and the colour is not.**

    `oh-my-logo` bakes a truecolor gradient into its output as escape sequences.
    Keeping those would have pinned the banner to one palette, so it would stay
    the same colour on a grey theme and on an oxblood one — and it would have
    meant a Node dependency at runtime in a Python project.
    """
    app = _app(tmp_path)
    async with app.run_test():
        splash = app.query_one(Splash)
        grey = splash._wordmark()

        splash._theme = themes.get("oxblood-dark")
        oxblood = splash._wordmark()

    assert grey != oxblood, "the banner ignored the theme"


def test_the_ramp_starts_and_ends_on_the_colours_it_was_given() -> None:
    """Checked on `gradient` directly rather than on the banner.

    The banner's first column is a space, so its first *painted* cell is already
    a step along the ramp — asserting the exact start colour appears in the
    markup fails for a reason that has nothing to do with the gradient. The
    endpoints are a property of the function; test them there.
    """
    painted = banner.gradient("ab", "#000000", "#ffffff")

    assert painted.startswith("[#000000]a[/]")
    assert painted.endswith("[#ffffff]b[/]")


def test_the_gradient_runs_across_the_width_not_per_line() -> None:
    """Per column, so the ramp is straight down the banner. Per character would
    step wherever a line happens to be shorter, which is a stripe."""
    painted = banner.gradient("ab\nab", "#000000", "#ffffff").split("\n")

    assert painted[0] == painted[1], "the same column got a different colour"


async def test_the_facts_are_side_by_side_not_stacked(tmp_path: Path) -> None:
    """Six stacked rows is a list you read top to bottom, and nothing here needs
    reading in order — you are looking for one of them. Two columns halve the
    height, and every row the splash takes is a row the first answer does not."""
    app = _app(tmp_path)
    async with app.run_test():
        columns = app.query_one(Splash)._columns()

        lines = [line for line in columns.split("\n") if line.strip()]
        facts = app.query_one(Splash)._facts

        # Asserted against the number of facts rather than a literal, because
        # the list grows: `signed in` was added with the auth gate, and a test
        # hard-coded to six rows fails for a reason unrelated to layout.
        expected = -(-len(facts) // Splash.COLUMNS)
        assert len(lines) == expected, f"{len(facts)} facts want {expected} rows, got {len(lines)}"
        assert all(line.count("[dim]") <= Splash.COLUMNS for line in lines)
        assert sum(line.count("[dim]") for line in lines) == len(facts), "every fact is drawn"


async def test_the_default_theme_is_grey_rather_than_oxblood(tmp_path: Path) -> None:
    """The website is burgundy on bone and the first themes followed it, which
    was the wrong inference: a page is looked at, a terminal is worked in. Greys
    leave the two colours that carry meaning — a failed tool, a warning — as the
    only saturated things on screen."""
    assert themes.DEFAULT_THEME == "slate"
    assert "oxblood-dark" in themes.available(), "still one /theme away"

    app = _app(tmp_path)
    async with app.run_test():
        assert app._theme.name == "slate"


# ------------------------------------------------------------------ escape


async def test_escape_closes_the_palette_first(tmp_path: Path) -> None:
    """Most-specific first. A list is open and nothing else has happened."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.state.remember("something earlier")
        await _type(pilot, app, "/c")
        assert app.query_one(CommandPalette).has_class("visible")

        await pilot.press("escape")

        assert not app.query_one(CommandPalette).has_class("visible")
        assert app.query_one(PromptInput).value == "/c", "history did not fire as well"


async def test_escape_on_an_idle_prompt_brings_back_what_you_sent(
    tmp_path: Path,
) -> None:
    """**Escape used to do nothing here**, which is a key that teaches you not
    to press it. Now it puts the last thing you sent back in the box, ready to
    edit."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.state.remember("the thing I asked before")

        await pilot.press("escape")

        assert app.query_one(PromptInput).value == "the thing I asked before"


async def test_escape_does_not_overwrite_something_half_typed(tmp_path: Path) -> None:
    """The same rule the up-arrow follows. A key that discards work in progress
    is one you learn to avoid, which defeats the point of adding it."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.state.remember("an old prompt")
        app.query_one(PromptInput).value = "half written"

        await pilot.press("escape")

        assert app.query_one(PromptInput).value == "half written"


async def test_escape_mid_turn_stops_it_and_asks_what_to_do(tmp_path: Path) -> None:
    """The work is partly done and partly paid for, so discarding it silently is
    the one option that should not be automatic."""
    harness = Harness(
        provider=_Slow(
            FakeProvider([tool_turn("run_shell", {"command": "echo hi"}), text_turn("done")])
        ),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )
    app = OmegaApp(harness)

    async with app.run_test() as pilot:
        app.query_one(PromptInput).value = "do the thing"
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert app.state.running, "the turn is under way"

        await pilot.press("escape")
        await pilot.pause(0.1)

        assert app.query_one(InterruptBar).waiting, "it stopped without asking anything"


async def test_stopping_leaves_the_transcript_alone(tmp_path: Path) -> None:
    """`s` ends it, and says nothing extra.

    The turn ending already writes "cancelled" to the transcript. A second line
    from the bar saying the same thing is how a transcript fills up with omega
    talking about itself — caught by this test asserting on the wrong string
    first, which is how the duplicate was noticed at all.
    """
    harness = Harness(
        provider=_Slow(FakeProvider([text_turn("a partial answer")])),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )
    app = OmegaApp(harness)

    async with app.run_test() as pilot:
        app.query_one(PromptInput).value = "go"
        await pilot.press("enter")
        await pilot.pause(0.15)
        await pilot.press("escape")
        await pilot.pause(0.1)

        await pilot.press("s")
        await pilot.pause(0.2)

        assert not app.query_one(InterruptBar).waiting, "the bar is gone"
        notices = [row.text for row in app.state.rows if row.kind == "notice"]
        assert notices == ["cancelled"], f"one word about it, not two: {notices}"
        assert app.focused is app.query_one(PromptInput), "focus came back"


async def test_r_and_s_are_ordinary_letters_when_nothing_is_waiting(
    tmp_path: Path,
) -> None:
    """**The trap in a single-key answer.** A prompt you cannot type the letter
    `r` into is worse than no shortcut at all, so the binding only exists while
    the bar is up."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.query_one(PromptInput).focus()

        await pilot.press("r", "s", "r")

        assert app.query_one(PromptInput).value == "rsr"
