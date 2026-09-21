"""The parts of the screen you actually touch.

These need Textual's pilot rather than the state tests' bare adapter, because
every one of them is about a widget: what a keypress reaches, what survives a
redraw, what is on screen at all. `state.py` and `adapter.py` stay Textual-free
so the other tests can run headless; this file is where that stops being
possible, and that is the intended division.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path

from textual import events

from omega_agent.agent_events import AgentStartEvent, ToolExecutionStartEvent
from omega_agent.harness import Harness
from omega_agent.types import ToolCall
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_coding.builtin_tools import build_tools
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
    """Prefix match, so `/c` is always clear, compact, context in that order."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await _type(pilot, app, "/c")

        palette = app.query_one(CommandPalette)
        shown = [palette.get_option_at_index(i).id for i in range(palette.option_count)]
        assert shown == ["clear", "cost", "context", "compact"]


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
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        field = app.query_one(PromptInput)
        field.focus()

        field.post_message(events.Paste("a\nb\nc\nd"))
        await pilot.pause()

        assert field.value == "[pasted #1 +4 lines]"
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
        assert "pasted #" not in sent[0]


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
