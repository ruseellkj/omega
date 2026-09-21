"""The approval prompt, as a screen rather than a question on stdin.

## Why this file had to exist before the TUI could be the default

`cli.py`'s `_ask_in_terminal` calls `input()` on a thread. Under Textual that is
not merely ugly, it is invisible: Textual owns the terminal, so the prompt is
painted over and the keystrokes answering it are consumed by the app. The old
code said so and refused to start — `--tui` hard-required `--yes`.

That refusal was honest while the TUI was opt-in. It stops being acceptable the
moment the TUI is what `omega` does with no arguments, because `--yes` means
*approve every write and every shell command without asking*. Flipping the
default without this file would have turned a deliberate opt-in into a silent
one. So the modal is not the first nice-to-have; it is the thing that makes the
flip safe.

## Why it is small

The seam was already there. `ApprovalPolicy` takes an `asker`:

    Asker  = Callable[[ApprovalRequest], Awaitable[Answer]]
    Answer = Literal["once", "always", "deny"]

Nothing about the policy — the deny list, the outside-root rule, the
non-recursive "always" grant — knows or cares how the question reaches a human.
Swapping the terminal asker for a screen is therefore a new *implementation of an
existing type*, not a change to the gate. All the judgement stays in
`approval.py`, which is where it was tested.

## The default is refusal, in three separate places

`_ask_in_terminal` made a bare Enter mean no, on the grounds that a prompt whose
easiest answer is yes is not really asking. A modal has more ways to be
dismissed than a line of input does, so the same rule has to be applied to each
of them: Escape denies, closing the screen without a choice denies, and the
"No" button is the one focused when the screen opens. There is no path that
reaches `allowed=True` without a deliberate press.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from omega_coding.approval import Answer, ApprovalRequest

#: Long arguments are worth showing and not worth wrapping the dialog around.
SUMMARY_WIDTH = 240


def _clip(text: str, width: int = SUMMARY_WIDTH) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= width else collapsed[: width - 1] + "…"


class ApprovalModal(ModalScreen[Answer]):
    """One question, three answers, and no fourth way out that means yes."""

    CSS = """
    ApprovalModal {
        align: center middle;
    }
    #dialog {
        width: 72;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    #asking   { color: $text-muted; }
    #summary  { padding: 1 0; text-style: bold; }
    #outside  { color: $error; text-style: bold; padding-bottom: 1; }
    #scope    { color: $text-muted; padding-bottom: 1; }
    #buttons  { height: auto; align: center middle; }
    #buttons Button { margin: 0 1; }
    """

    BINDINGS = [
        # The letters the terminal prompt used, kept so the muscle memory carries
        # over. `escape` is listed last because it is the one that matters: every
        # dismissal that is not an explicit yes has to land on "deny".
        Binding("y", "answer('once')", "Yes"),
        Binding("a", "answer('always')", "Always"),
        Binding("n", "answer('deny')", "No"),
        Binding("escape", "answer('deny')", "No"),
    ]

    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"omega wants to use {self.request.tool_name}", id="asking")
            yield Static(_clip(self.request.summary), id="summary")
            if self.request.outside_root is not None:
                # Said loudly and separately. Writing inside the project and
                # writing to `~/.ssh` are not the same question and must not look
                # like the same question.
                yield Static(
                    f"⚠ {self.request.outside_root} is outside the working directory",
                    id="outside",
                )
            yield Static(self.request.scope_note, id="scope")
            with Horizontal(id="buttons"):
                yield Button("No", variant="error", id="deny")
                yield Button("Yes", variant="success", id="once")
                yield Button("Always", variant="warning", id="always")

    def on_mount(self) -> None:
        # Focus the refusal, so that Enter — the key people press to make a
        # dialog go away — declines rather than consents.
        self.query_one("#deny", Button).focus()

    def action_answer(self, answer: Answer) -> None:
        self.dismiss(answer)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        # The ids are the three `Answer` literals, so no mapping table can drift
        # out of step with the type.
        self.dismiss(event.button.id)  # type: ignore[arg-type]


def tui_asker(app: App[None]) -> Callable[[ApprovalRequest], Awaitable[Answer]]:
    """An `Asker` that asks on screen instead of on stdin.

    `push_screen_wait` suspends the calling **worker** until the screen is
    dismissed, which is exactly the shape the hook needs: the gate is an ordinary
    `await` that happens to take as long as a human does. It works here because
    the turn already runs as a worker (`OmegaApp.run_turn`), and the hook is
    called from inside that same task.
    """

    async def ask(request: ApprovalRequest) -> Answer:
        answer = await app.push_screen_wait(ApprovalModal(request))
        # A screen can be dismissed with no result — `dismiss()` with no argument,
        # or the app shutting down underneath it. None of those are consent.
        return answer if answer in {"once", "always", "deny"} else "deny"

    return ask
