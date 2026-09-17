"""The screen — Tier 3's "and has a face".

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

## What is deliberately not here

Named so the gap is a decision. Tau's `tui/` is 10,828 lines; this is a fraction
of one, and the difference is almost entirely these:

* **an approval modal** — `_ask_in_terminal` calls `input()` on a thread, which
  cannot work under Textual. So `--tui` currently requires `--yes`, and says so
  rather than hanging on a prompt nobody can see. The modal is the first thing to
  add next.
* themes, autocomplete, file drop, desktop notifications, a session picker,
  mouse support, markdown rendering of assistant text
* a diff view for `edit_file` — the most obviously missing one
"""

from __future__ import annotations

import contextlib
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, Input, Static

from omega_agent.harness import Harness
from omega_coding.tui.adapter import TuiEventAdapter
from omega_coding.tui.state import Row, TuiState


def _style(row: Row) -> str:
    """Rich markup for one row. The only place a row kind becomes a colour."""
    if row.kind == "user":
        return f"[bold]{row.text}[/bold]"
    if row.kind == "tool":
        return f"[red]{row.text}[/red]" if row.is_error else f"[dim]{row.text}[/dim]"
    if row.kind == "notice":
        return f"[red]{row.text}[/red]" if row.is_error else f"[yellow]{row.text}[/yellow]"
    return row.text


class OmegaApp(App[None]):
    """One screen: a scrolling transcript, a status line, and an input box."""

    CSS = """
    Screen { layout: vertical; }
    #transcript { height: 1fr; padding: 0 1; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    Input { dock: bottom; }
    """

    BINDINGS = [
        # Cancels the *turn*, not the app — the harness ends it properly and the
        # transcript stays valid, which is the whole point of `CancelSignal`.
        Binding("ctrl+c", "cancel", "Stop the turn"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, harness: Harness, *, title: str = "omega") -> None:
        super().__init__()
        self.harness = harness
        self.state = TuiState()
        self.adapter = TuiEventAdapter(self.state)
        self._title = title

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="transcript")
        yield Static(self.state.status, id="status")
        yield Input(placeholder="Ask omega… (type while it works to steer it)")
        yield Footer()

    def on_mount(self) -> None:
        self.title = self._title
        self.query_one(Input).focus()

    # ------------------------------------------------------------------ drawing

    def refresh_view(self) -> None:
        """Redraw from state. **State is the source; widgets are the copy.**

        Rebuilding the whole pane rather than diffing it: the transcript is tens
        of rows, not thousands, and a redraw that cannot drift out of step with
        the state is worth more here than the cycles a diff would save.
        """
        try:
            pane = self.query_one("#transcript", VerticalScroll)
        except NoMatches:
            # The screen is gone but the worker has not stopped yet — quitting
            # mid-turn produces exactly this. **Found by a test, not by reading:**
            # the worker's next redraw raised `NoMatches` and took the turn down
            # with it. There is nothing to draw on, so there is nothing to do.
            return

        pane.remove_children()
        pane.mount_all([Static(_style(row)) for row in self.state.rows])
        pane.scroll_end(animate=False)
        self.query_one("#status", Static).update(self.state.status)

    # ------------------------------------------------------------------- input

    async def on_input_submitted(self, message: Input.Submitted) -> None:
        text = message.value.strip()
        message.input.value = ""
        if not text:
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

    def action_cancel(self) -> None:
        if self.state.running:
            self.harness.cancel()

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


async def run_tui(harness: Harness, **kwargs: Any) -> None:
    """Start the screen. Returns when the user quits."""
    await OmegaApp(harness, **kwargs).run_async()
