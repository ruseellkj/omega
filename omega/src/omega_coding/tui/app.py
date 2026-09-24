"""The screen — Tier 3's "and has a face", and Tier 3.5's making it usable.

## What this makes possible that print could not

Steering. `TIER-2.md` records it as wired but unreachable:

> "Steering cannot actually be typed yet. The queues are wired and the loop
> drains them between turns, but a `print`/`input` REPL has no way to accept a
> keystroke while a turn is running."

`harness.queue_steering()` has existed and been tested since Tier 2 with no way
for a human to call it. A UI with an always-live input box is what closes that,
and it is the reason this file exists — a prettier transcript would not have been
worth the dependency.

## Why Textual, and why not Ink

Ink is Node and omega is Python, so it was never a candidate. The real choice was
Textual, `prompt_toolkit`, or hand-rolled ANSI. Pi hand-rolls: 14,184 lines, its
own layout engine, Kitty keyboard protocol negotiation. Tau uses Textual and gets
a 99-line event adapter out of it. Copying the reference that is in the same
language, and reading its `tui/` package while doing so, was the cheaper way to
learn the shape.

## The event loop belongs to Textual

`cli.py`'s print REPL calls `asyncio.run` once and owns its loop. Textual owns its
own, and the two cannot nest — so a turn runs as a **worker**, iterating
`harness.run(prompt)` and updating state from inside Textual's loop.

This is the third time loop ownership has mattered here. The first was a new loop
per prompt; the second was an HTTP client outliving the loop that made it. Both
printed the same `generator didn't stop after athrow()`. So the provider is closed
on app exit, inside the loop that used it, rather than after `run_async` returns.

## Redrawing incrementally, and why that stopped being optional

The first version rebuilt the whole pane on every event: `remove_children()` then
mount everything again. That is defensible while a row is a `Static` — the
transcript is tens of rows, and a redraw that cannot drift out of step with the
state is worth more than the cycles a diff saves.

It stops being defensible the moment a row can be **expanded**. A rebuild throws
away the widget holding that state, so opening a tool result and then letting the
agent emit one more event would snap it shut. Widgets are now updated in place and
only appended when the transcript grows, which is the one thing a rebuild cannot
be made to do.

## Copying and pasting

A Textual app turns on mouse reporting, so the terminal's own selection is gone:
a drag goes to the app, and the terminal never sees it. Textual highlights the
drag itself. Before this, the only way to copy that highlight was `ctrl+c`, which
this app binds at priority to *stop*. So nothing could be copied at all. What
replaced it:

* **Selecting copies.** Mouse-up on a selection in the transcript
  (`TextSelected`), a drag inside the prompt (`PromptInput.Selected`) and a
  double- or triple-click all copy immediately, and the status line says how
  much. `/config auto-copy off` turns it off.
* **`ctrl+c` copies when something is selected**, and stops or exits only when
  nothing is. That is the convention of terminals that give `ctrl+c` both jobs,
  and it is what lets a selection "override all" without taking the stop key
  away: the first press copies and clears, the next one stops.
* **One chokepoint.** `copy_to_clipboard` is overridden, as Tau overrides it
  (`tui/app.py:3495`). Every path, including `Input`'s own copy and cut,
  reaches the system clipboard through `clipboard.py`, and none can copy from a
  password field.
* **A paste always lands.** One no input took is rerouted to the prompt. A click
  returns focus to the prompt, as Tau's does (`tui/app.py:3663`). `ctrl+v`
  reads the system clipboard rather than Textual's in-process string.
* **A selection holds the view.** The transcript normally jumps to the bottom on
  every event, which dragged the text out from under the mouse mid-turn. While
  anything is selected, it stays put. It follows again the moment you type or
  send, because any cursor move in the prompt clears the highlight
  (`_input.py:518`). Tau's answer was the opposite: it turns
  selection off while a turn runs (`tui/app.py:3488-3493`). That makes the
  stream win over the user, and here the user asked for the reverse.

## What is deliberately not here

* file drop, desktop notifications, a session picker
* markdown rendering of assistant text, and a diff view for `edit_file` —
  the most obviously missing one
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path
from typing import Any

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.theme import Theme as TextualTheme
from textual.widgets import Footer, Input, Static
from textual.widgets.input import Selection

from omega_agent.harness import Harness
from omega_coding import clipboard as clipboards
from omega_coding.approval import ApprovalPolicy
from omega_coding.commands import CommandContext, dispatch
from omega_coding.tui import banner, config, themes
from omega_coding.tui.adapter import TuiEventAdapter
from omega_coding.tui.approval import tui_asker
from omega_coding.tui.login import tui_choice_asker, tui_secret_asker
from omega_coding.tui.state import Row, TuiState
from omega_coding.tui.themes import TuiTheme
from omega_coding.tui.widgets import (
    ClipboardPaste,
    CommandPalette,
    InterruptBar,
    PromptBox,
    PromptInput,
    Splash,
    StatusBar,
    ToolRow,
    Transcript,
    TranscriptRow,
    build_row,
)
from omega_coding.version import omega_version


def _as_textual(theme: TuiTheme) -> TextualTheme:
    """Our nine colours as Textual's sixteen.

    Textual's `Theme` drives the chrome — header, footer, focus rings, the
    palette's selection bar — and it wants slots omega has no separate opinion
    about. Those are filled from the nearest colour we do have rather than
    invented, so a theme file stays nine lines instead of sixteen.
    """
    return TextualTheme(
        name=theme.name,
        primary=theme.accent,
        secondary=theme.secondary,
        accent=theme.accent,
        warning=theme.warning,
        error=theme.error,
        success=theme.success,
        foreground=theme.foreground,
        background=theme.background,
        surface=theme.surface,
        panel=theme.surface,
        dark=theme.dark,
        variables={"text-muted": theme.muted},
    )


#: What "carry on" sends. A real prompt rather than an empty one, because the
#: loop appends whatever it is given as a `UserMessage` and an empty turn reads
#: to the model as a user who said nothing.
RESUME_PROMPT = "Continue from where you were interrupted."


class OmegaApp(App[None]):
    """One screen: a scrolling transcript, a status line, and an input box."""

    CSS = """
    Screen { layout: vertical; }
    #transcript { height: 1fr; padding: 1 2; }
    Splash { padding: 1 2; color: $primary; }
    #palette { max-height: 10; border: round $primary; display: none; }
    #palette.visible { display: block; }
    TranscriptRow { padding: 0; margin-bottom: 1; }
    /* The user's own message is the one block that keeps padding — the grey
       needs room around the text or it reads as a highlight rather than a
       message. Everything else is flush left. */
    .row-user { padding: 0 1; }
    ToolRow { margin-bottom: 0; }
    """

    BINDINGS = [
        # Cancels the *turn*, not the app — the harness ends it properly and the
        # transcript stays valid, which is the whole point of `CancelSignal`.
        # Pressed twice in a row it quits, because every other command-line tool
        # exits on Ctrl-C and a program that refuses to is one you have to look
        # up how to leave.
        # **`priority` is what puts this in the footer**, and finding that out
        # needed the *resolved* bindings rather than the declared ones. The row
        # read `^d Exit  ^o Expand tools` — no Ctrl-C, the key that stops a turn
        # and is half the exit pair. `app.active_bindings` showed why: the active
        # `ctrl+c` came from `PromptInput`, because Textual's `Input` binds
        # `ctrl+c,super+c` to `copy` with `show=False` and the prompt holds focus
        # almost always. Priority moves resolution back to the app, which also
        # makes the key mean one thing everywhere instead of "copy" in the box
        # and "stop" outside it.
        #
        # `system=False` was tried alongside it — Textual marks its own `ctrl+c`
        # `system=True`, which looked like the cause. It is **not** needed:
        # removing it left the footer identical on a real pty. Recorded because
        # a flag kept "just in case" reads as load-bearing to the next reader.
        Binding("ctrl+c", "cancel", "Stop / exit", priority=True),
        # **`priority` is load-bearing.** Textual's `Input` binds `delete,ctrl+d`
        # to `delete_right`, and the prompt has focus for almost the whole
        # session — so without this the keystroke deletes a character and the app
        # never sees it. Measured: the app stayed open and a character vanished.
        Binding("ctrl+d", "leave", "Exit", priority=True),
        # Textual ships `ctrl+q → quit` on `App` itself, so *not* binding it here
        # leaves it quitting anyway. Overridden rather than removed, because the
        # one thing worse than an undocumented exit key is one that exits without
        # the confirmation the documented pair insists on.
        Binding("ctrl+q", "redirect_quit", show=False, priority=True),
        Binding("ctrl+o", "expand_all", "Expand tools"),
    ]

    #: How long a first Ctrl-C stays "armed" for the second. Long enough to be a
    #: deliberate double-press, short enough that a Ctrl-C now and another a
    #: minute later are two separate refusals to quit rather than an exit.
    EXIT_WINDOW_SECONDS = 3.0

    def __init__(
        self,
        harness: Harness,
        *,
        title: str = "omega",
        policy: ApprovalPolicy | None = None,
        context: CommandContext | None = None,
        provider_name: str = "fake",
        auto_approve: bool = False,
        confine: bool = False,
        clipboard: clipboards.Clipboard | None = None,
    ) -> None:
        super().__init__()
        self.harness = harness
        #: Where copies go and ctrl+v reads from. Injectable so no test can
        #: touch the real one, see `tests/conftest.py`. **Not `_clipboard`**,
        #: which is Textual's own in-process string and is kept in step below.
        self._system_clipboard = clipboard if clipboard is not None else clipboards.system()
        #: Whether selecting text copies it. `/config auto-copy` changes it.
        self._auto_copy = config.load_auto_copy()
        self.state = TuiState()
        self.adapter = TuiEventAdapter(self.state)
        self._title = title
        #: The gate to hand a screen-based asker to once there is a screen. None
        #: when the caller passed `--yes`, in which case the policy approves
        #: without asking and has no use for one.
        self._policy = policy
        #: What `/` commands run against. None in tests that only drive turns;
        #: the palette still opens, and a command says it has nothing to act on
        #: rather than failing obscurely.
        #:
        #: **Not `_context`.** Textual's `App` already has a `_context()` method,
        #: and assigning over it replaces the context manager Textual uses to
        #: install the active app — found by `mypy --strict`, which reported
        #: "Cannot assign to a method" before anything ran.
        self._commands = context
        #: Widgets parallel to `state.rows`, so a redraw can update in place
        #: instead of rebuilding — see the module docstring.
        self._rows: list[TranscriptRow | ToolRow] = []
        self._theme = themes.get_or_default(config.load_theme_name(themes.DEFAULT_THEME))

        #: Whether a second Ctrl-C would exit. Armed by the first press
        #: on an idle prompt and disarmed on a timer — see `action_cancel`.
        self._exit_armed = False
        #: Shown in the startup facts. Passed in rather than inferred, because
        #: `cli.py` is the only file allowed to know which provider this is.
        self._provider_name = provider_name
        self._auto_approve = auto_approve
        self._confine = confine

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        # **No `Header()`.** Textual's is a generic one-line title bar — an icon
        # and the app name — which says nothing you did not already know from
        # typing `omega`. The facts worth a row are in the splash instead, where
        # they can be six lines wide and then get out of the way.
        with Transcript(id="transcript"):
            yield Splash(
                self._facts(),
                "/  commands    ↑  history    ctrl+o  expand    ctrl+c/ctrl+d  exit",
                self._theme,
            )
        yield CommandPalette(id="palette")
        # **Nothing below the transcript is docked.** Three `dock: bottom`
        # siblings do not stack — they all anchor to the same edge and overlap.
        # Measured: PromptBox landed at y=31 height 3 while StatusBar sat at
        # y=32 and Footer at y=33, so the box's bottom rule was painted over by
        # the footer and its text row by the status. `#transcript` taking
        # `height: 1fr` pushes everything after it down in compose order, which
        # is what was wanted in the first place.
        yield InterruptBar()
        yield StatusBar()
        yield PromptBox()
        yield Footer()

    def _facts(self) -> list[tuple[str, str]]:
        """The six startup rows. Degrades to "—" rather than failing."""
        model = self._commands.model if self._commands is not None else self.harness.model
        return banner.startup_facts(
            model=model,
            provider=self._provider_name,
            root=Path.cwd(),
            session_id=self.harness.session_id,
            auto_approve=self._auto_approve,
            confine=self._confine,
        )

    def on_mount(self) -> None:
        self.title = self._title
        for theme in themes.BUILTIN.values():
            self.register_theme(_as_textual(theme))
        self.theme = self._theme.name
        self.query_one(PromptInput).focus()
        if self._commands is not None:
            # The REPL's `getpass` asker cannot work here — Textual owns the
            # terminal — so it is replaced with a modal, exactly as the approval
            # gate's is. Late, for the same reason: the asker closes over an
            # `App` that did not exist when the context was built.
            self._commands = replace(
                self._commands,
                ask_secret=tui_secret_asker(self),
                ask_choice=tui_choice_asker(self),
            )

        if self._policy is not None:
            # Only now does an `App` exist for the asker to close over. Until
            # this line the policy refuses every gated call, which is the right
            # way round for a window where no one could have answered anyway.
            self._policy.use_asker(tui_asker(self))

    # ------------------------------------------------------------------ drawing

    def refresh_view(self) -> None:
        """Redraw from state. **State is the source; widgets are the copy.**"""
        try:
            pane = self.query_one("#transcript", VerticalScroll)
        except NoMatches:
            # The screen is gone but the worker has not stopped yet — quitting
            # mid-turn produces exactly this. **Found by a test, not by reading:**
            # the worker's next redraw raised `NoMatches` and took the turn down
            # with it. There is nothing to draw on, so there is nothing to do.
            return

        # **Guarded on the header, not on the splash.** `remove()` is deferred in
        # Textual: the splash is still in the node list on the next redraw, so a
        # loop keyed off its presence mounted a second `#header` and raised
        # `DuplicateIds` mid-turn. Asking whether the header already exists is
        # the question that has one answer.
        if self.state.rows and not pane.query("#header"):
            for splash in pane.query(Splash):
                splash.remove()
            # Mounted before the row loop below, so it lands at the top of the
            # transcript — the only place a header means anything. Appending it
            # would file it under whatever answer happened to be last.
            pane.mount(Static(self._header_markup(), id="header"))

        rows = self.state.rows
        # `close_stream` can delete an empty row, so the transcript can shrink.
        # Rebuilding the tail is correct and rare; rebuilding always is what the
        # expanded-tool-row problem was.
        while len(self._rows) > len(rows):
            self._rows.pop().remove()

        for index, row in enumerate(rows):
            if index < len(self._rows):
                widget = self._rows[index]
                # A row can change kind only by being replaced, which the shrink
                # above already handled — but a mismatch here would silently
                # render a tool call as prose, so it is checked rather than
                # assumed.
                if isinstance(widget, ToolRow) == (row.kind == "tool"):
                    widget.update_row(row, self._theme)
                    continue
                widget.remove()
                self._rows[index] = build_row(row, self._theme)
                pane.mount(self._rows[index])
                continue
            widget = build_row(row, self._theme)
            self._rows.append(widget)
            pane.mount(widget)

        # **Not while something is selected.** Jumping to the bottom on every
        # event pulled the text out from under a drag mid-turn. The selection
        # wins, and the view follows the stream again once it is cleared.
        if not pane.screen.selections:
            pane.scroll_end(animate=False)
        self._refresh_status()

    def _refresh_status(self) -> None:
        """Drive the spinner from state.

        **Nothing is shown when nothing is happening.** The old line read "ready"
        against the top edge of the input box — a word that never changes, pinned
        where the eye goes to type. What replaces it when idle is the token total,
        which does change and is worth a glance; when idle and untouched, nothing
        at all.
        """
        try:
            bar = self.query_one(StatusBar)
        except NoMatches:
            return
        if self.state.running:
            bar.show(self._working_label())
        else:
            bar.clear(self._spend())

    def _working_label(self) -> str:
        """What the spinner says: the live step, then anything queued."""
        state = self.state
        label = "thinking" if state.thinking else (state.activity or "working")
        if state.queued:
            label = f"{label}  ·  {state.queued} steering queued"
        spend = self._spend()
        return f"{label}  ·  {spend}" if spend else label

    def _spend(self) -> str:
        state = self.state
        spent = state.tokens_in + state.tokens_out
        if not spent:
            return ""
        cached = f", {state.tokens_cached:,} cached" if state.tokens_cached else ""
        return f"{spent:,} tokens{cached}"

    # ------------------------------------------------------------------- input

    def _palette(self) -> CommandPalette:
        return self.query_one("#palette", CommandPalette)

    def on_input_changed(self, message: PromptInput.Changed) -> None:
        palette = self._palette()
        palette.set_class(palette.filter_to(message.value), "visible")

    async def on_input_submitted(self, message: PromptInput.Submitted) -> None:
        # **Only this app's own prompt box.** `Input.Submitted` bubbles from any
        # `Input` on screen, including the one inside the login modal — and
        # treating that as a prompt sent an API key to the provider as a
        # question. The modal stops the event as well; this is the second lock,
        # because the cost of missing one is a leaked credential.
        if message.input.id != "prompt":
            return

        field = self.query_one(PromptInput)
        # Markers back to the text they stand for, *before* anything else sees
        # the prompt — history, steering and the model must all get the real
        # thing, not `[paste #1 +57 lines]`.
        text = field.expand_pastes(message.value).strip()
        message.input.value = ""
        self._palette().set_class(False, "visible")
        if not text:
            return

        # History remembers what was typed, not what it expanded to: recalling a
        # 600-line paste into a one-line box would be unusable, and the marker
        # still resolves on the way back out.
        self.state.remember(message.value.strip())

        if text.startswith("/") or text.startswith("!"):
            # **A worker, not a bare await.** `/login` opens a modal through
            # `push_screen_wait`, and Textual raises `NoActiveWorker` unless the
            # caller is one — `on_input_submitted` is an event handler. The
            # approval modal never hit this because it is reached from inside
            # `_drive`, which already runs as a worker.
            #
            # Its own group, so a command never cancels a turn: `run_turn` uses
            # `exclusive=True` in the default group, and sharing it would make
            # typing `/cost` mid-turn abort the turn.
            self.run_worker(self._run_command(text), group="command")
            return

        if self.state.running:
            # **The line this whole file exists for.** Typing during a turn is
            # not an error to swallow and not a second prompt to race the first:
            # it is guidance, and the loop drains it between turns.
            self.harness.queue_steering(text)
            self.state.queued += 1
            self.state.add("notice", f"steering queued: {text}")
            self.refresh_view()
            return

        self.state.add("user", text)
        self.refresh_view()
        self.run_turn(text)

    async def _run_command(self, text: str) -> None:
        """Run a `/` command or a `!` shell escape, showing what it says.

        Commands used to be REPL-only — not because they needed a terminal, but
        because every handler called `print()`, so under Textual they wrote to a
        stdout nobody could see. `CommandContext.emit` is the channel that fixed
        that; here it appends notice rows.
        """
        if text.startswith("/theme"):
            self._switch_theme(text[len("/theme") :].strip())
            return
        # Matched on the whole word, unlike `/theme` above: a prefix match would
        # route `/configure` here too.
        name, _, argument = text.partition(" ")
        if name == "/config":
            self._configure(argument.strip())
            return
        if self._commands is None:
            self.state.add("notice", "commands need a session; none was wired.", is_error=True)
            self.refresh_view()
            return

        lines: list[str] = []
        # `replace` rather than rebuilding the context by hand: it is a frozen
        # dataclass, so this is the supported way to swap one field, and it
        # cannot go stale when the context grows another.
        outcome = await dispatch(text, replace(self._commands, emit=lines.append))
        body = "\n".join(line for line in lines if line.strip())
        if body:
            self.state.add("notice", body)
        self.refresh_view()
        if outcome == "exit":
            self.exit()

    def _switch_theme(self, name: str) -> None:
        """`/theme` lives here rather than in `commands.py` because it is the one
        command that acts on the screen itself, and the REPL has no screen."""
        if not name:
            current = self._theme.name
            listing = ", ".join(f"*{n}*" if n == current else n for n in themes.available())
            self.state.add("notice", f"themes: {listing}\nusage: /theme <name>")
        elif name not in themes.available():
            self.state.add("notice", f"no theme called {name!r}.", is_error=True)
        else:
            self._theme = themes.get(name)
            self.theme = name
            config.save_theme_name(name)
            for index, row in enumerate(self.state.rows):
                self._rows[index].update_row(row, self._theme)
            self.state.add("notice", f"theme: {name}")
        self.refresh_view()

    def _configure(self, argument: str) -> None:
        """`/config` — settings for the screen, here for the reason `/theme` is.

        One setting so far, `auto-copy`. `/config` lists it, `/config auto-copy`
        flips it, and `/config auto-copy on|off` sets it. The copied-text notice
        names this command, so it has to exist wherever that notice can appear.
        """
        setting, _, value = argument.partition(" ")
        value = value.strip().lower()
        if not setting:
            state = "on" if self._auto_copy else "off"
            self.state.add(
                "notice",
                f"settings:\n  auto-copy  {state:<4} copy text as soon as you select it"
                "\nusage: /config auto-copy [on|off]",
            )
        elif setting != "auto-copy":
            self.state.add(
                "notice", f"no setting called {setting!r}. There is one: auto-copy.", is_error=True
            )
        elif value not in {"", "on", "off"}:
            self.state.add("notice", "usage: /config auto-copy [on|off]", is_error=True)
        else:
            self._auto_copy = (not self._auto_copy) if not value else value == "on"
            config.save_auto_copy(self._auto_copy)
            self.state.add(
                "notice",
                "auto-copy: on — selecting text copies it"
                if self._auto_copy
                else "auto-copy: off — select text, then ctrl+c to copy it",
            )
        self.refresh_view()

    # ------------------------------------------------------- copy and paste

    def copy_to_clipboard(self, text: str) -> None:
        """Every copy in the app arrives here. See "Copying and pasting" above.

        Textual's own version only writes OSC 52 (`textual/app.py:1770`), which
        macOS Terminal ignores. This one goes to the system clipboard first.
        """
        self._copy(text, auto=False)

    def _copy(self, text: str, *, auto: bool) -> None:
        """Copy `text`, unless it came out of a field holding a secret.

        **The password rule lives here, not at each caller**, because
        `Input.selected_text` on a `password=True` field is the real value.
        Measured: `'sk-secret'`, not the dots on screen. A caller that forgot
        would put an API key on the clipboard from the login modal.
        """
        focused = self.focused
        if isinstance(focused, Input) and focused.password:
            self._flash("not copied — that field holds a secret")
            return
        if not text:
            return
        # Textual's in-process copy, what ctrl+v falls back to when there is no
        # system clipboard to read (over SSH, or on a bare Linux box).
        self._clipboard = text
        self.run_worker(self._deliver(text, auto=auto), group="clipboard")

    async def _deliver(self, text: str, *, auto: bool) -> None:
        """Native first, then OSC 52 if that failed or landed on the wrong machine.

        The order and the cap are Pi's, and `clipboard.py` says why. The notice
        says only what is known: "copied" when a tool confirmed it, "sent" when
        the terminal was asked and may have declined.
        """
        backend = self._system_clipboard
        native = await backend.write(text)
        sent = False
        if (backend.remote or not native) and clipboards.fits_osc52(text):
            super().copy_to_clipboard(text)
            sent = True

        size = f"{len(text):,} char{'' if len(text) == 1 else 's'}"
        if sent:
            said = f"sent {size} to the terminal's clipboard"
        elif native:
            said = f"copied {size} to clipboard"
        else:
            self._flash(f"could not copy {size}: no clipboard tool, and too large for the terminal")
            return
        self._flash(f"{said} · disable auto-copy in /config" if auto else said)

    def _flash(self, message: str) -> None:
        """Say `message` on the status line, whichever screen is on top."""
        # The main screen by index: with a modal up, `query_one` looks at the
        # modal, which has no status line of its own.
        with contextlib.suppress(NoMatches):
            self.screen_stack[0].query_one(StatusBar).flash(message)

    def _auto_copy_selection(self) -> None:
        """Copy the screen's selection, if auto-copy is on and nothing is modal.

        **Not on a modal.** A modal is a question, and the login modal is the one
        screen with a secret on it. Selecting there still highlights, and ctrl+c
        still copies, through the password rule above.
        """
        if not self._auto_copy or len(self.screen_stack) > 1:
            return
        text = self.screen.get_selected_text()
        if text:
            self._copy(text, auto=True)

    def on_text_selected(self, event: events.TextSelected) -> None:
        """Mouse-up after a drag in the transcript. Also sent after every plain
        click, when there is nothing selected, which `_auto_copy_selection`
        ignores."""
        self._auto_copy_selection()

    def on_prompt_input_selected(self, message: PromptInput.Selected) -> None:
        """A drag inside the prompt. Screen selection never sees these —
        measured: after a drag in an `Input`, `get_selected_text()` is None."""
        if self._auto_copy:
            self._copy(message.text, auto=True)

    def on_click(self, event: events.Click) -> None:
        """Double- and triple-click selections, and focus back to the prompt.

        Textual makes those selections in `Widget._on_click` (`widget.py:4698`)
        and posts no `TextSelected` for them, so they are caught here. The click
        has bubbled up from the widget by now, so the selection already exists.
        """
        if len(self.screen_stack) > 1:
            return
        if event.chain >= 2:
            self._auto_copy_selection()
        # Not while the interrupt bar is asking: it holds focus so that `r` and
        # `s` reach it, and a click must not quietly make them type instead.
        if event.button == 1 and not self._interrupt().waiting:
            with contextlib.suppress(NoMatches):
                self.query_one(PromptInput).focus()

    def on_paste(self, event: events.Paste) -> None:
        """A paste no input took. It reaches here only by bubbling, because
        `Input` stops the ones it handles."""
        if len(self.screen_stack) > 1:
            return
        try:
            field = self.query_one(PromptInput)
        except NoMatches:
            return
        event.stop()
        if not self._interrupt().waiting:
            field.focus()
        field.paste(event.text)

    def on_clipboard_paste(self, message: ClipboardPaste) -> None:
        """ctrl+v in the prompt or the key field. A worker, because reading the
        clipboard runs a process."""
        self.run_worker(self._paste_from_clipboard(message.field), group="clipboard")

    async def _paste_from_clipboard(self, field: Input) -> None:
        """Read the system clipboard into the field that asked.

        **A password field never falls back** to Textual's in-process string:
        that is whatever was last selected in omega, and it is never a key.
        """
        text = await self._system_clipboard.read()
        if text is None and not field.password:
            # No way to read the system clipboard (over SSH, or with no tool).
            # What omega itself last copied is still better than nothing.
            text = self.clipboard
        if text is None:
            self._flash("no clipboard to read here — use your terminal's own paste")
            return
        if not text:
            self._flash("the clipboard is empty")
            return
        if not field.is_attached:
            return  # the modal closed while the clipboard was being read
        if isinstance(field, PromptInput):
            field.paste(text)
        else:
            # What Textual's own paste does in a one-line field: the first line,
            # over the selection (`_input.py:756-762`).
            field.replace(text.splitlines()[0], *sorted(field.selection))

    def _copy_selection(self) -> bool:
        """ctrl+c's first job: copy whatever is selected. True if anything was.

        The selection is cleared once copied, so the next ctrl+c stops or exits.
        Otherwise a highlight left over from auto-copy would keep claiming the
        key, and ctrl+c would never stop anything.
        """
        focused = self.focused
        if isinstance(focused, Input) and not focused.selection.is_empty:
            self.copy_to_clipboard(focused.selected_text)
            focused.selection = Selection.cursor(focused.selection.end)
            return True
        text = self.screen.get_selected_text()
        if text:
            self.copy_to_clipboard(text)
            self.screen.clear_selection()
            return True
        return False

    async def on_key(self, event: Any) -> None:
        """Route up/down: the palette first, then prompt history.

        Tau's precedence exactly (`tui/app.py:4851-4905`), and it is the right
        way round — while a list of commands is open, up and down obviously mean
        "move in this list", and history would be a surprise.

        **It does nothing while a modal is up**, and that guard is the whole of a
        bug that made `/login` unusable. This handler claims `up`, `down` and
        `escape` for the prompt, and `prevent_default()` on a key event
        suppresses Textual's binding dispatch — so with the provider picker open,
        `up` never reached its `OptionList` and `escape` never reached the
        modal's own Cancel binding. You could open the picker and neither move
        nor leave.

        Measured rather than reasoned: the same `ChoiceModal` pushed onto a bare
        `App` moved and dismissed correctly, and pushed onto this one did not.
        That isolation is what named the culprit — the modal was never at fault.

        One guard covers all three modals (`ChoiceModal`, `SecretModal`,
        `ApprovalModal`), because the rule is about *who owns the keyboard*
        rather than about any one screen.
        """
        if len(self.screen_stack) > 1:
            return

        palette = self._palette()
        open_palette = palette.has_class("visible")
        field = self.query_one(PromptInput)

        if event.key in {"up", "down"}:
            if open_palette:
                palette.action_cursor_up() if event.key == "up" else palette.action_cursor_down()
            elif event.key == "up" and not field.value and self._recall_queued(field):
                # **Precedence, written down because it is now three-deep:**
                # palette → a message queued during this turn → prompt history.
                # The queued message goes above history because it is the only
                # one you might still be able to change: history is a record and
                # a queued message is a draft that has not been sent yet.
                #
                # Guarded on an empty prompt so it cannot eat something you are
                # halfway through typing.
                pass
            else:
                recalled = (
                    self.state.previous_prompt(field.value)
                    if event.key == "up"
                    else self.state.next_prompt()
                )
                if recalled is None:
                    return
                field.value = recalled
                field.cursor_position = len(recalled)
            event.prevent_default()
            event.stop()
        elif event.key == "tab" and open_palette:
            chosen = palette.chosen()
            if chosen is not None:
                field.value = f"/{chosen} "
                field.cursor_position = len(field.value)
                palette.set_class(False, "visible")
            event.prevent_default()
            event.stop()
        elif event.key == "escape":
            self._on_escape(open_palette, field)
            event.prevent_default()
            event.stop()


    def _interrupt(self) -> InterruptBar:
        return self.query_one(InterruptBar)

    def _on_escape(self, open_palette: bool, field: PromptInput) -> None:
        """Escape means three different things, depending on what is happening.

        Ordered most-specific first, which is the order you would expect if you
        pressed it without thinking:

        1. **A list is open** — close it. Nothing else has happened yet.
        2. **A turn is running** — stop it, then ask what to do about it. The
           work is already partly done and partly paid for, so throwing it away
           silently is the one option that should not be automatic.
        3. **Nothing is happening** — put the last thing you sent back in the
           box, ready to edit and send again. Escape on an idle prompt did
           nothing at all before, which is a key that teaches you not to press
           it.
        """
        if open_palette:
            self._palette().set_class(False, "visible")
            return

        if self.state.running:
            self.harness.cancel()
            self._interrupt().show()
            return

        previous = self.state.history[-1] if self.state.history else ""
        if previous and not field.value.strip():
            field.value = previous
            field.cursor_position = len(previous)

    def on_interrupt_bar_answered(self, message: InterruptBar.Answered) -> None:
        """The bar answers by message, because it — not the app — owns the key.

        `Input` consumes every printable character before the app sees it, so
        `r` and `s` can only be read by a widget that currently has focus.
        """
        self._answer_interrupt(resume=message.resume)

    def _answer_interrupt(self, *, resume: bool) -> None:
        """Act on the bar, and take it down either way."""
        self._interrupt().hide()
        self.query_one(PromptInput).focus()
        if not resume:
            # **No notice.** The turn ending already wrote "cancelled" to the
            # transcript (`adapter.py`, on `agent_end` with reason `aborted`),
            # and a second line saying the same thing is how a transcript fills
            # up with omega talking about itself.
            self.refresh_view()
            return
        # A new turn over an intact transcript — see `InterruptBar`'s docstring
        # for why this is not a rewind and cannot be.
        self.refresh_view()
        self.run_turn(RESUME_PROMPT)

    def action_cancel(self) -> None:
        """Stop the turn. On an idle prompt, ask once and exit on the second.

        Three revisions, and the middle one is the interesting failure. The
        original only ever cancelled, so Ctrl-C on an idle app did nothing and
        the way out was Ctrl-Q — which you had to already know. The fix made an
        idle Ctrl-C exit **immediately**, which traded one problem for a worse
        one: the reflex keystroke for "stop that" now closed the session, and
        there is no undo for a closed session.

        So: cancelling still wins while a turn runs, and on an idle prompt the
        first press arms and says so. Pi's footer advertises the same pairing —
        `ctrl+c/ctrl+d clear/exit`. Ctrl-D exits on the first press because that
        is what it means everywhere else and nobody presses it by reflex.

        **A selection comes before all of it**: with text selected, ctrl+c
        copies it and does nothing else. See `_copy_selection`.
        """
        if self._copy_selection():
            return

        if self.state.running:
            self.harness.cancel()
            self._exit_armed = False
            return

        if self._exit_armed:
            self.exit()
            return

        self._exit_armed = True
        self.state.add("notice", "Press ctrl+c again to exit, or ctrl+d.")
        self.refresh_view()
        # Disarms itself, so a Ctrl-C now and another one much later are two
        # separate refusals to quit rather than an exit nobody asked for.
        self.set_timer(self.EXIT_WINDOW_SECONDS, self._disarm_exit)

    def _facts_text(self) -> str:
        """The startup facts as one block, for `ctrl+o` after the splash is gone.

        Built from `_facts()` rather than kept as a second copy — the splash and
        this are the same six answers, and two sources would drift the moment
        `/model` or `/login` changed one of them.
        """
        width = max(len(label) for label, _ in self._facts())
        lines = [f"{label:<{width}}  {value}" for label, value in self._facts()]
        return "\n".join(lines)

    def _recall_queued(self, field: PromptInput) -> bool:
        """Pull back the last message queued during this turn, for editing.

        Sending a correction mid-turn used to be final: the queue was write-only,
        so a typo committed to the next request and the only way out was
        cancelling the whole turn. Now it comes back into the box exactly as it
        was typed, and pressing Enter queues it again.

        False when there was nothing to recall, so the caller falls through to
        history — which is what `up` has always meant on an idle prompt.
        """
        recalled = self.harness.unqueue_steering()
        if recalled is None:
            return False
        field.value = recalled
        field.cursor_position = len(recalled)
        self.state.add("notice", "queued message brought back for editing")
        self.refresh_view()
        return True

    def _header_markup(self) -> str:
        """The compact identity block, built from live values.

        `self.harness.model` rather than the startup model: `/model` changes it
        mid-session, and a header that kept saying the old name would be a
        confident lie about what the next turn costs.
        """
        from omega_coding.tui import banner

        return banner.compact_header(
            version=f"v{omega_version()}",
            model=self.harness.model,
            provider=self._provider_name,
            path=banner.short_path(Path.cwd()),
            colour=self._theme.accent,
            muted=self._theme.muted,
        )

    def _disarm_exit(self) -> None:
        self._exit_armed = False

    def action_redirect_quit(self) -> None:
        """Say which keys exit, rather than exiting.

        Ctrl-Q was omega's documented way out until the pair below replaced it,
        and Textual provides it whether or not omega asks. Silently keeping a
        third exit — one with no confirmation — would undo the point of arming
        Ctrl-C.
        """
        self.state.add("notice", "Use ctrl+c twice, or ctrl+d, to exit.")
        self.refresh_view()

    def action_leave(self) -> None:
        """Ctrl-D: leave, first press.

        Unambiguous in a way Ctrl-C is not — in every shell it means end of
        input, and nobody reaches for it to interrupt something. A turn in flight
        is cancelled on the way out so the transcript is closed properly rather
        than abandoned mid-message.
        """
        if self.state.running:
            self.harness.cancel()
        self.exit()

    def action_expand_all(self) -> None:
        """Open every tool row at once — and show the startup facts when there
        are none.

        The expand half is Tau's `ctrl+o`, kept alongside the click-to-expand
        Textual gives for free, and it works: measured, one press opens a tool
        row and the next closes it. What it did **not** do was anything at all
        before the first tool call, which is most of a session and is when
        someone new presses it. Pi's `ctrl+o` means "more" — full startup help —
        so the two behaviours are one key with a fallback rather than a key that
        is dead half the time.
        """
        rows = [row for row in self._rows if isinstance(row, ToolRow)]
        if not rows:
            self.state.add("notice", self._facts_text())
            self.refresh_view()
            return
        collapsed = [row for row in self._rows if isinstance(row, ToolRow) and row.collapsed]
        target = bool(collapsed)
        for row in self._rows:
            if isinstance(row, ToolRow):
                row.collapsed = not target

    # ------------------------------------------------------------------- turns

    def run_turn(self, prompt: str) -> None:
        """Start a turn as a Textual worker.

        A worker rather than a bare task: Textual owns the loop, and work started
        outside its supervision does not get cancelled when the app exits.
        """
        self.run_worker(self._drive(prompt), exclusive=True)

    async def _drive(self, prompt: str) -> None:
        try:
            async for event in self.harness.run(prompt):
                self.adapter.apply(event)
                self.refresh_view()
        finally:
            # Whatever happened — finished, cancelled, or raised — the queue
            # drained with the turn, so the count has to go back to zero or the
            # status line lies for the rest of the session.
            self.state.queued = 0
            self.state.running = False
            self.refresh_view()

    # ----------------------------------------------------------------- shutdown

    async def on_unmount(self) -> None:
        """Close the provider inside the loop that used it.

        Third time this has mattered. Closing after `run_async` returns would be
        too late: the loop is gone, and the connection pool is collected with no
        loop to close it on.
        """
        closer = getattr(self.harness.provider, "aclose", None)
        if closer is not None:
            with contextlib.suppress(Exception):
                await closer()


async def run_tui(
    harness: Harness,
    *,
    policy: ApprovalPolicy | None = None,
    context: CommandContext | None = None,
    provider_name: str = "fake",
    auto_approve: bool = False,
    confine: bool = False,
    **kwargs: Any,
) -> None:
    """Start the screen. Returns when the user quits."""
    await OmegaApp(
        harness,
        policy=policy,
        context=context,
        provider_name=provider_name,
        auto_approve=auto_approve,
        confine=confine,
        **kwargs,
    ).run_async()


__all__ = ["OmegaApp", "Row", "run_tui"]
