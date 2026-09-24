"""Asking for a key on screen, without ever showing it.

The same shape as `tui/approval.py`, for the same reason: a command needs an
answer from a human, and `push_screen_wait` suspends the calling worker until it
has one. Nothing about `/login` knows it is talking to a modal — it holds an
`ask_secret` callable and could as easily be talking to `getpass`.

**`password=True` is the whole security surface of this file.** Textual's `Input`
renders masked characters when it is set, so the key is never on screen, never in
a screenshot, and never in the terminal's scrollback.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static

from omega_coding.tui.widgets import ClipboardPaste


class SecretInput(Input):
    """The key field: a stock masked `Input` whose ctrl+v reads the real clipboard.

    The stock `ctrl+v` pastes Textual's in-process string, which auto-copy fills
    with whatever was last selected. So it is the one place transcript text
    could have gone in as a credential. The app pastes the system clipboard here
    instead, and never falls back to that string for a password field.
    """

    def action_paste(self) -> None:
        self.post_message(ClipboardPaste(self))


class SecretModal(ModalScreen[str | None]):
    """One masked field. Enter accepts, Escape cancels."""

    CSS = """
    SecretModal { align: center middle; }
    #dialog {
        width: 68;
        height: auto;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    #asking { color: $text-muted; }
    #note   { color: $text-muted; padding-top: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.prompt, id="asking")
            yield SecretInput(password=True, placeholder="paste here — it stays hidden")
            yield Static(
                "Stored in ~/.omega/auth.json, readable only by you. "
                "Escape cancels and stores nothing.",
                id="note",
            )

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, message: Input.Submitted) -> None:
        """Take the value, and **stop the event going anywhere else.**

        `Input.Submitted` bubbles. Without `stop()` it reaches
        `OmegaApp.on_input_submitted`, which treats any submission as a prompt —
        so pressing Enter here sent the API key to the model as a question, and
        wrote it into the transcript on the way.

        Found by a test asserting the key was absent from *every* row rather
        than from the rows it was expected in. The manual check before it looked
        only at notices and reported success.
        """
        message.stop()
        # Stripped here rather than at the call site: a pasted key routinely
        # carries a trailing newline, and a credential that fails because of
        # whitespace is a miserable thing to debug.
        self.dismiss(message.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


def tui_secret_asker(app: App[None]) -> Callable[[str], Awaitable[str | None]]:
    """An `ask_secret` that asks on screen.

    Mirrors `approval.tui_asker` exactly, including the reason it works: the
    command runs inside the turn worker, and `push_screen_wait` is an ordinary
    `await` that happens to take as long as a person does.
    """

    async def ask(prompt: str) -> str | None:
        answer = await app.push_screen_wait(SecretModal(prompt))
        return answer if isinstance(answer, str) and answer else None

    return ask


class ChoiceModal(ModalScreen[str | None]):
    """Pick one of a short list. Used by `/login` with no argument.

    An `OptionList` rather than buttons, because the list is data — the providers
    omega knows about — and buttons would have to be written out per provider.
    The command palette makes the same choice for the same reason.
    """

    CSS = """
    ChoiceModal { align: center middle; }
    #dialog {
        width: 54;
        height: auto;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    #asking { color: $text-muted; padding-bottom: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, options: list[str]) -> None:
        super().__init__()
        self.prompt = prompt
        self.options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.prompt, id="asking")
            yield OptionList(*self.options)

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, message: OptionList.OptionSelected) -> None:
        message.stop()
        self.dismiss(str(message.option.prompt))

    def action_cancel(self) -> None:
        self.dismiss(None)


def tui_choice_asker(app: App[None]) -> Callable[[str, list[str]], Awaitable[str | None]]:
    """An `ask_choice` that asks on screen."""

    async def ask(prompt: str, options: list[str]) -> str | None:
        answer = await app.push_screen_wait(ChoiceModal(prompt, options))
        return answer if isinstance(answer, str) and answer else None

    return ask
