"""The parts of the screen that know what a row looks like.

Split out of `app.py` for the same reason `state.py` was split out of both: the
app is about *when* things happen — a turn starts, a key is pressed, a worker
finishes — and this file is about *what they look like*. Mixing them is how a
200-line app becomes a 6,808-line one (`research/tau/src/tau_coding/tui/app.py`,
which is exactly that, and which is why Tau also has a separate `widgets.py`).

## The three ideas here

**A row's kind chooses its colours, and nothing else does.** `RoleStyle` comes
from the theme, so adding a theme cannot require touching a widget, and a widget
cannot quietly hard-code a colour the theme does not know about.

**Assistant prose has no left rule.** Every other kind gets one. That is not
decoration: the rule is what makes the user's message and the agent's steps
scannable as a column down the left, and prose that carried one would read as
just another step rather than as the answer. Tau makes the same exception
(`widgets.py:273`).

**A finished tool call is a receipt you can open.** `_render` could only show the
first line of output, because a printer has nowhere to put the other 1,999. A
`Collapsible` does, so the title says "ran npm test" and the body holds
everything — and the row stops being a lossy summary.
"""

from __future__ import annotations

import re
from typing import Any

from rich.cells import cell_len, get_character_cell_size
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Collapsible, Input, OptionList, Static
from textual.widgets.option_list import Option

from omega_coding.commands import COMMANDS
from omega_coding.status import FRAMES
from omega_coding.tui import banner
from omega_coding.tui.state import Row
from omega_coding.tui.themes import RoleStyle, TuiTheme

#: How much of a tool's output to keep in the expanded view. A `run_shell` that
#: prints a megabyte would otherwise be mounted into the widget tree in full,
#: which is slow to lay out and useless to read.
OUTPUT_LIMIT = 4000


def _apply_role(widget: Static | Collapsible, style: RoleStyle, fallback: str) -> None:
    """Paint one widget from its role.

    `body` is a Rich style string (`"#221C1A on #F5F1E9"`), which is one field
    covering both the text colour and the background behind it — the shape Tau's
    themes use, and the reason a role needs two values rather than four.
    """
    parsed = Style.parse(style.body)
    if parsed.color is not None and parsed.color.triplet is not None:
        widget.styles.color = parsed.color.triplet.hex
    if parsed.bgcolor is not None and parsed.bgcolor.triplet is not None:
        widget.styles.background = parsed.bgcolor.triplet.hex
    if style.border:
        widget.styles.border_left = ("tall", style.border)
    else:
        # Explicitly cleared, not merely unset: a widget being reused for a
        # different kind of row would otherwise keep the previous rule.
        #
        # A colour is still required even though `"none"` draws nothing —
        # Textual parses the pair eagerly and raises `StyleValueError` on `""`.
        # Found by running the tests, which failed five at once with
        # "failed to parse '' as a color".
        widget.styles.border_left = ("none", fallback)


class TranscriptRow(Static):
    """A user message, a line of assistant prose, or a notice."""

    #: Drawn before a user message, matching the prompt box below. It is what
    #: identifies the row as *yours* now that the left bar is gone — the grey
    #: background alone reads as a quote, not as something you said.
    MARKER = "❯ "

    def __init__(self, row: Row, theme: TuiTheme) -> None:
        super().__init__(row.text or " ")
        self.add_class(f"row-{row.kind}")
        self.update_row(row, theme)

    def update_row(self, row: Row, theme: TuiTheme) -> None:
        # A blank string collapses the widget to zero height, which makes a
        # streaming row that has not received its first token flicker into
        # existence. One space holds the line open.
        text = row.text or " "
        self.update(f"{self.MARKER}{text}" if row.kind == "user" else text)
        style = theme.roles.get(row.kind)
        if style is not None:
            _apply_role(self, theme.roles["notice"] if row.is_error else style, theme.background)


class ToolRow(Collapsible):
    """One tool call: a one-line receipt that opens to show what it produced."""

    DEFAULT_CSS = """
    ToolRow {
        border: none;
        padding: 0;
        margin: 0;
    }
    ToolRow > CollapsibleTitle { padding: 0; }
    ToolRow Contents { padding: 0 0 0 2; }
    """

    def __init__(self, row: Row, theme: TuiTheme) -> None:
        self._body = Static("")
        super().__init__(self._body, title=row.text, collapsed=True)
        self.update_row(row, theme)

    def update_row(self, row: Row, theme: TuiTheme) -> None:
        # The spinner-free equivalent of "still going". A marker rather than an
        # animation because the status line above already animates, and two
        # things moving for one event is noise.
        marker = "…" if row.running else ("✗" if row.is_error else "✓")
        self.title = f"{marker} {row.text}"
        body = row.output.rstrip() or ("running…" if row.running else "(no output)")
        if len(body) > OUTPUT_LIMIT:
            dropped = len(body) - OUTPUT_LIMIT
            body = f"{body[:OUTPUT_LIMIT]}\n… {dropped:,} more characters not shown"
        # **`from_ansi`, not a plain string.** Real commands emit colour — pytest,
        # git, ls, cargo, every test runner — and a `Static` given the raw text
        # prints the escape codes literally: `[33mno tests ran[0m`. Rich parses
        # them into styles instead, so the output looks the way it would in the
        # shell rather than like the shell malfunctioned.
        self._body.update(Text.from_ansi(body))
        style = theme.roles["notice"] if row.is_error else theme.roles["tool"]
        if style.border:
            self.styles.border_left = ("tall", style.border)


def build_row(row: Row, theme: TuiTheme) -> TranscriptRow | ToolRow:
    """One row of state becomes one widget. The only place that mapping lives."""
    return ToolRow(row, theme) if row.kind == "tool" else TranscriptRow(row, theme)


class Transcript(VerticalScroll):
    """The scrolling pane of rows, which a mouse press never focuses.

    **A drag used to take the keyboard away from the prompt.** A press focuses
    the nearest focusable ancestor of whatever is under it (`screen.py:1934`).
    For a row, that is this pane. Textual only turns a press and release into a
    `Click` when both land on the same widget (`app.py:4084-4116`), so a drag
    from one row to another left focus here, with nothing to hand it back. The
    next keystroke, and the Enter after it, went to a scroll view. Found by a
    test whose prompt was never sent.

    `focus_on_click` is Textual's switch for exactly this. The pane stays
    focusable, so Tab still reaches it for keyboard scrolling.
    """

    def focus_on_click(self) -> bool:
        return False


class CommandPalette(OptionList):
    """The list that appears the moment you type `/`.

    **Prefix match, not fuzzy.** Tau filters the same way
    (`tui/autocomplete.py:381` is `startswith`), and with nine commands fuzzy
    matching buys nothing while making the ordering hard to predict — `/c` should
    offer `/clear`, `/compact`, `/context` in that order every time.

    It reads `COMMANDS` directly, which is the tuple `/help` already iterates. No
    second registry to drift: Pi keeps one purely to feed autocomplete, and its
    own module docstring notes omega deliberately does not.
    """

    def filter_to(self, text: str) -> bool:
        """Show the commands matching `text`. Returns whether any are left.

        The palette closes as soon as a space is typed, because everything after
        the first space is an argument (`dispatch` partitions on it) and a
        command list is no longer what you need — `/resume ` wants a session id,
        not nine more command names.
        """
        if not text.startswith("/") or " " in text:
            return False
        prefix = text[1:].lower()
        matches = [command for command in COMMANDS if command.name.startswith(prefix)]
        if not matches:
            return False
        self.clear_options()
        self.add_options(
            [
                Option(f"{command.usage:<16} {command.summary}", id=command.name)
                for command in matches
            ]
        )
        self.highlighted = 0
        return True

    def chosen(self) -> str | None:
        """The highlighted command's name, or None if nothing is highlighted."""
        if self.highlighted is None:
            return None
        return self.get_option_at_index(self.highlighted).id


class Splash(Vertical):
    """The empty state: the wordmark, then the facts. Replaced by the transcript.

    ## Two colours, one string

    The banner is drawn with two characters — `█` for the stroke and `▒` for the
    outline traced around it. Painting both the same colour turns the outline
    into noise and the word into a smudge, so each run is wrapped in its own
    markup on the way out. That is the whole reason `banner.OUTLINE` is exported
    rather than being a detail of the art.

    ## Why the facts are in columns

    Six stacked rows is a list you read top to bottom, and nothing here needs
    reading in order — you are looking for *one* of them. Two columns halve the
    height, which matters because every row the splash occupies is a row the
    first answer does not get.
    """

    DEFAULT_CSS = """
    Splash { height: auto; }
    Splash > #wordmark { padding: 0 0 1 0; }
    Splash > #facts {
        border: round $primary 40%;
        padding: 0 2;
        width: auto;
    }
    Splash > #hint { padding: 1 0 0 1; }
    """

    COLUMNS = 2
    # 10, not 9: `signed in` is exactly nine characters, so a nine-wide
    # column rendered it as `signed innot signed in` with no gap at all.
    LABEL_WIDTH = 10
    VALUE_WIDTH = 25

    def __init__(self, facts: list[tuple[str, str]], hint: str, theme: TuiTheme) -> None:
        super().__init__()
        self._facts = facts
        self._hint = hint
        self._theme = theme

    def _wordmark(self) -> str:
        """The banner, painted along the theme's own colour ramp.

        `oh-my-logo` — the utility this letterform came from — bakes its gradient
        into the text as escape sequences. Deriving it from the theme instead
        means `/theme` recolours the banner along with everything else, and the
        project keeps no Node dependency for a decorative string.
        """
        return banner.gradient(banner.BANNER, self._theme.accent, self._theme.secondary)

    def _columns(self) -> str:
        """The facts, laid out across `COLUMNS` rather than down one."""
        per = -(-len(self._facts) // self.COLUMNS)  # ceiling
        rows: list[str] = []
        for row in range(per):
            cells = []
            for column in range(self.COLUMNS):
                index = column * per + row
                if index >= len(self._facts):
                    continue
                label, value = self._facts[index]
                # The value is clipped rather than wrapped: a wrapped cell breaks
                # the column alignment that is the only reason this is a table.
                if len(value) > self.VALUE_WIDTH:
                    value = value[: self.VALUE_WIDTH - 1] + "…"
                cells.append(
                    f"[dim]{label:<{self.LABEL_WIDTH}}[/dim]"
                    f"[bold]{value:<{self.VALUE_WIDTH}}[/bold]"
                )
            rows.append("   ".join(cells).rstrip())
        return "\n".join(rows)

    def compose(self) -> ComposeResult:
        # Three widgets rather than one string, because the middle one needs a
        # border of its own and a border is a widget property, not markup.
        yield Static(self._wordmark(), id="wordmark")
        yield Static(self._columns(), id="facts")
        yield Static(f"[dim]{self._hint}[/dim]", id="hint")


class ClipboardPaste(Message):
    """ctrl+v in `field`: the app should read the system clipboard into it.

    **One message for every field that pastes**, the prompt and the login
    modal's key alike. The stock `Input.action_paste` pastes Textual's
    in-process string (`_input.py:1129`), which auto-copy fills with whatever was
    last selected. In the key field that meant transcript text went in as the
    credential, hidden behind dots. Found in review, and it is why the field
    that asked travels with the message.
    """

    def __init__(self, field: Input) -> None:
        super().__init__()
        self.field = field


class PromptInput(Input):
    """The text field, and every Textual behaviour that had to change for it.

    **Pasting more than one line silently kept only the first.** Textual's
    `Input._on_paste` is `event.text.splitlines()[0]` (8.2.8,
    `widgets/_input.py:756`) — a reasonable default for a single-line field and a
    bad one for a prompt. Pasting a stack trace, a diff, or a block of code gave
    omega line one and dropped the rest, with nothing on screen to say so.

    ## Why a placeholder rather than joining the lines

    The obvious fix is to flatten newlines to spaces. That loses nothing but the
    line structure — which is exactly what matters when the thing pasted is code
    or a traceback.

    **Both references solve it the same way:** collapse a large paste to a short
    marker, keep the real text, and expand it again on submit.

    | | threshold | marker |
    |---|---|---|
    | Tau (`tui/app.py:473,679-694`) | 2,000 chars | `[Pasted content #N: 12,345 chars, 67 lines]` |
    | Pi (`tui/.../editor.ts:1198-1212`) | 10 lines or 1,000 chars | `[paste #1 +123 lines]` |
    | omega | **any newline**, or 500 chars | `[paste #1 +57 lines]` |

    omega's line threshold is stricter than either because its prompt is a
    single-line `Input`, where a newline has nowhere to go. Pi and Tau both have
    multi-line editors and can afford to wait for the tenth line. The marker
    wording is Pi's. It was `[pasted #1 …]` until it was asked for as
    `[paste 1…]`.

    ## Pasting the same thing twice shows it in full

    **Neither reference does this.** Pi and Tau only ever collapse. The rule:
    *paste text whose marker is still in the box, and that marker is replaced by
    the text itself*, with the cursor at its end. So the first paste keeps the
    box readable, and the second paste is "no, show me". Three consequences,
    stated because each could surprise:

    - A second paste over a *selection* replaces the selection, as any paste
      does. Expanding needs an empty selection.
    - Once expanded, there is no marker left, so a third paste of the same text
      collapses again, as `#2`.
    - An expanded paste is real text in the box, so the box draws its line
      breaks as `↵` (see below) and history remembers it expanded.

    ## Drawing a newline in a one-row box

    `Input` renders a newline as a raw `\\n` inside a one-row strip (measured:
    `'one\\ntwo\\nthree   …'`), which a real terminal would act on and tear the
    screen with. Tabs were already wrong: drawn eight cells wide while the cursor
    maths counts four (measured: `'a       b'` with the cursor at cell 6). Both
    are drawn as one-cell glyphs, `↵` and `⇥`, and the three methods that
    turn text into cells agree on that. **The value keeps the real characters**,
    so what is sent, copied or recalled is never the glyph.

    ## Markers are one unit

    Backspace at the end of a marker deletes the whole marker, as Pi's editor
    treats them as atomic (`editor.ts:34-35`, "paste markers as single units").
    Without it one backspace turns `[paste #1 +4 lines]` into `[paste #1 +4 lines`,
    which no longer matches, so the paste is silently dropped and the fragment
    is sent in its place.

    ## Where the clipboard comes in

    `ctrl+v` used to paste Textual's in-process string (`action_paste` reads
    `App.clipboard`, `_input.py:1129`), which is not the system clipboard. It now
    asks the app, which owns `clipboard.py`, and the text comes back through
    `paste` like any other. A drag inside the box posts `Selected` for the app to
    copy. The widget knows neither how to reach a clipboard nor whether
    auto-copy is on.
    """

    #: Collapse anything with a newline, or longer than this.
    COLLAPSE_CHARS = 500

    #: How the one-row box draws characters it cannot show. One cell each.
    GLYPHS = {"\n": "↵", "\t": "⇥"}
    _SHOWN = str.maketrans(GLYPHS)

    #: The marker's shape, for finding every one in a single pass.
    MARKER = re.compile(r"\[paste #\d+ [^\]]+\]")

    class Selected(Message):
        """The mouse finished selecting text inside the box."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        #: marker -> the text it stands for.
        self.pastes: dict[str, str] = {}
        self._paste_count = 0
        self._dragging = False

    # ------------------------------------------------------------------ paste

    def _on_paste(self, event: events.Paste) -> None:
        if not event.text:
            return
        event.stop()
        event.prevent_default()
        self.paste(event.text)

    def action_paste(self) -> None:
        self.post_message(ClipboardPaste(self))

    def paste(self, text: str) -> None:
        """One paste, from wherever it came: Cmd+V, ctrl+v, or a reroute.

        Line endings are normalised first. A Windows clipboard's `\\r\\n` would
        otherwise leave a `\\r` in the value, and the provider would receive it.
        """
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return
        start, end = sorted(self.selection)

        if start == end:
            marker = self._marker_holding(text)
            if marker is not None:
                at = self.value.rfind(marker)
                self.replace(text, at, at + len(marker))
                return

        if "\n" not in text and len(text) <= self.COLLAPSE_CHARS:
            # Small and flat: what Textual would have done, selection included.
            self.replace(text, start, end)
            return

        self._paste_count += 1
        lines = text.count("\n") + 1
        measure = f"+{lines} lines" if lines > 1 else f"{len(text):,} chars"
        marker = f"[paste #{self._paste_count} {measure}]"
        self.pastes[marker] = text
        self.replace(marker, start, end)

    def _marker_holding(self, text: str) -> str | None:
        """The newest marker for exactly `text` that is still in the box."""
        for marker, held in reversed(self.pastes.items()):
            if held == text and marker in self.value:
                return marker
        return None

    def expand_pastes(self, value: str) -> str:
        """Put the real text back before the prompt is sent.

        **Not `expand`.** Textual's `Widget` already has an `expand` bool, and
        shadowing it with a method is the same mistake `_context` was — caught
        the same way, by `mypy --strict` before anything ran.

        One pass with one pattern, not a `replace` per marker: pasted text that
        happens to contain a marker must come out as it went in, rather than
        being expanded a second time. `pastes` is *not* cleared — a prompt
        recalled from history with the up-arrow still carries its markers, and
        they have to resolve the second time too.
        """
        return self.MARKER.sub(lambda hit: self.pastes.get(hit.group(0), hit.group(0)), value)

    # ------------------------------------------------------ markers as a unit

    def _marker_span(
        self, *, ending: int | None = None, starting: int | None = None
    ) -> tuple[int, int] | None:
        """The marker that ends at `ending`, or starts at `starting`, if any."""
        for marker in self.pastes:
            if ending is not None and self.value[max(0, ending - len(marker)) : ending] == marker:
                return ending - len(marker), ending
            if starting is not None and self.value[starting : starting + len(marker)] == marker:
                return starting, starting + len(marker)
        return None

    def action_delete_left(self) -> None:
        span = self._marker_span(ending=self.cursor_position) if self.selection.is_empty else None
        if span is None:
            super().action_delete_left()
        else:
            self.delete(*span)

    def action_delete_right(self) -> None:
        span = self._marker_span(starting=self.cursor_position) if self.selection.is_empty else None
        if span is None:
            super().action_delete_right()
        else:
            self.delete(*span)

    # ------------------------------------------------------- mouse selection

    def on_mouse_down(self, event: events.MouseDown) -> None:
        # Runs before `Input._on_mouse_down` starts its own drag — handlers go
        # subclass first (`message_pump.py:758`). A flag of our own rather than
        # reading Input's private `_selecting`.
        self._dragging = True

    def on_mouse_up(self, event: events.MouseUp) -> None:
        dragged, self._dragging = self._dragging, False
        if dragged and not self.selection.is_empty and not self.password:
            self.post_message(self.Selected(self.selected_text))

    # ------------------------------------------------------------- drawing

    @property
    def _value(self) -> Text:
        shown = Text(self.value.translate(self._SHOWN), no_wrap=True, overflow="ignore", end="")
        for index, char in enumerate(self.value):
            if char in self.GLYPHS:
                shown.stylize("dim", index, index + 1)
        return shown

    def _position_to_cell(self, position: int) -> int:
        return cell_len(self.value[:position].translate(self._SHOWN))

    def _cell_offset_to_index(self, offset: int) -> int:
        cell = 0
        scroll_x, _ = self.scroll_offset
        offset += scroll_x
        for index, char in enumerate(self.value.translate(self._SHOWN)):
            width = get_character_cell_size(char)
            if cell <= offset < cell + width:
                return index
            cell += width
        return max(0, min(offset, len(self.value)))


class PromptBox(Horizontal):
    """The input, an arrow, and the two rules that make it look like a box.

    The field used to be a bare docked `Input`, which renders Textual's default
    `tall` border — heavy on top, thin below — and read as though the bottom of
    the box had been cut off. Two explicit rules, top and bottom, close it.
    """

    DEFAULT_CSS = """
    PromptBox {
        height: auto;
        border-top: solid $foreground;
        border-bottom: solid $foreground;
        padding: 0 1;
    }
    PromptBox > #arrow {
        width: 2;
        color: $primary;
        text-style: bold;
        padding: 0;
    }
    PromptBox > Input {
        border: none;
        padding: 0;
        height: 1;
        width: 1fr;
        background: transparent;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("❯ ", id="arrow")
        # `compact=True` is what actually removes Textual's own border. Setting
        # `border: none` in CSS above is not enough on its own: `Input` ships
        # `border: tall $border-blurred; height: 3` in its `DEFAULT_CSS`
        # (`widgets/_input.py:190-196`), and the result was a second, heavier
        # frame drawn inside this one — the "half cut" box. `compact` is the
        # supported switch for a one-row, borderless field (`_input.py:283`).
        #
        # `select_on_focus=False`: Textual's default selects the whole value on
        # every focus (`_input.py:738`), so coming back to the box from a click,
        # a modal or the interrupt bar left the draft selected, and the next key
        # replaced all of it.
        yield PromptInput(
            placeholder="Ask anything, or / for commands",
            id="prompt",
            compact=True,
            select_on_focus=False,
        )


class StatusBar(Static):
    """What is happening, with a spinner, sitting above the prompt box.

    Two things it does not do, both deliberate:

    **It does not say "ready".** An idle agent has nothing to report, and a word
    pinned to the top edge of the input box is noise that never changes. The row
    stays reserved so the layout does not jump when a turn starts.

    **The spinner only spins while something is running.** `Timer.pause()` and
    `resume()` rather than a permanently-running interval, so an idle omega is
    not redrawing ten times a second forever.

    Frames come from `status.FRAMES` — the same braille sequence the print REPL's
    status line uses, so the two surfaces animate identically.

    ## A flash outranks both

    `flash` is the one-line answer to something you just did: "copied 152 chars
    to clipboard". It holds the row for `FLASH_SECONDS`, and it has to beat the
    spinner. `_tick` repaints ten times a second, so a message written once
    would be gone in a tenth of a second mid-turn. So every paint asks about the
    flash first, and whatever the row held underneath comes back when it
    expires. **Not a transcript notice**: a row per copy is how a transcript
    fills up with omega talking about itself.
    """

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        margin: 0 0 1 0;
        padding: 0 2;
        color: $text-muted;
    }
    StatusBar.-flash { color: $primary; }
    """

    #: How long a flash holds the row.
    FLASH_SECONDS = 4.0

    def __init__(self) -> None:
        super().__init__("")
        self._frame = 0
        self._timer: Timer | None = None
        self._label = ""
        self._trailing = ""
        self._flash = ""
        self._flash_timer: Timer | None = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.1, self._tick, pause=True)

    def show(self, label: str) -> None:
        """Start or update the working line."""
        self._label = label
        if self._timer is not None:
            self._timer.resume()
        self._paint()

    def clear(self, trailing: str = "") -> None:
        """Stop spinning. `trailing` survives — the token count, typically."""
        self._label = ""
        self._trailing = trailing
        if self._timer is not None:
            self._timer.pause()
        self._paint()

    def flash(self, message: str) -> None:
        """Say `message` for a few seconds, over whatever the row holds."""
        self._flash = message
        if self._flash_timer is not None:
            self._flash_timer.stop()
        self._flash_timer = self.set_timer(self.FLASH_SECONDS, self._end_flash)
        self._paint()

    @property
    def flashing(self) -> str:
        """The message being flashed, or `""`."""
        return self._flash

    def _end_flash(self) -> None:
        self._flash = ""
        self._paint()

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(FRAMES)
        self._paint()

    def _paint(self) -> None:
        self.set_class(bool(self._flash), "-flash")
        if self._flash:
            # `Text`, not a string: a flash is never markup, and one that
            # happened to contain `[` must not be parsed as a style.
            self.update(Text(self._flash))
        elif self._label:
            self.update(f"{FRAMES[self._frame]} {self._label}")
        else:
            self.update(f"[dim]{self._trailing}[/dim]" if self._trailing else "")


class InterruptBar(Static):
    """What to do about a turn you just stopped.

    ## Why this is a bar and not a modal

    A modal takes the screen, and the thing you most want to look at while
    deciding is the half-finished answer behind it. This sits above the prompt,
    the transcript stays visible, and one key answers it.

    ## Why it takes focus

    **Found by a failing test, and the passing test next to it is the proof.**
    Handling `r`/`s` at the app level never fired: Textual gives a key to the
    focused widget first, and `Input` consumes every printable character. The
    test asserting that `r`, `s`, `r` type the word "rsr" into an idle prompt
    passed for exactly that reason — the same behaviour, seen from the other
    side.

    So the bar focuses itself while it is asking. The `Input` is not focused, so
    nothing is competing for the keystroke, and focus goes back when it is
    dismissed. It is also the honest signal that something is waiting on you.

    ## What "carry on" can and cannot mean

    **It is not a pause.** The provider stream is gone the moment the turn is
    cancelled — there is no socket left to continue reading.

    What it *can* do is ask the model to continue, and that works because the
    transcript is still valid: the partial assistant message is kept with
    `stop_reason="aborted"`, and every tool call still has its result
    (`loop.py:147` appends one even when the call was cancelled). So carrying on
    is a new turn over an intact conversation, with the model reading its own
    cut-off answer as context. The wording says "carry on" rather than "resume"
    because a resume that silently re-asks would be the worse surprise.
    """

    can_focus = True

    DEFAULT_CSS = """
    InterruptBar {
        height: auto;
        padding: 0 2;
        margin: 0 0 1 0;
        color: $warning;
        display: none;
    }
    InterruptBar.visible { display: block; }
    InterruptBar:focus { text-style: none; }
    """

    PROMPT = (
        "[b]Stopped.[/b] What should omega do?"
        "   [b]r[/b] carry on from here   [b]s[/b] stop here   [b]esc[/b] stop here"
    )

    class Answered(Message):
        """Raised when the question has been answered, either way."""

        def __init__(self, *, resume: bool) -> None:
            super().__init__()
            self.resume = resume

    def show(self) -> None:
        self.update(self.PROMPT)
        self.add_class("visible")
        self.focus()

    def hide(self) -> None:
        self.remove_class("visible")

    @property
    def waiting(self) -> bool:
        return self.has_class("visible")

    def on_key(self, event: events.Key) -> None:
        if not self.waiting:
            return
        if event.key in {"r", "s", "escape"}:
            event.stop()
            event.prevent_default()
            self.post_message(self.Answered(resume=event.key == "r"))
