"""Which surface omega presents, and why.

The bug this file exists for: `omega` with no flags ran a print REPL, and the
terminal UI was reachable only through `--tui`, which additionally demanded
`--yes`. So the UI existed, was tested, and was never seen. The flip makes the
TUI the default — and a default is exactly the kind of thing that regresses
silently, because every test that passes a flag keeps passing.

`choose_mode` is a pure function of the flags and two booleans, which is the
whole reason it is a function: the alternative is asserting on a real terminal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omega_agent.harness import Harness
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_coding.builtin_tools import build_tools
from omega_coding.cli import _run_print, choose_mode, textual_is_available


def _mode(**overrides: object) -> str:
    """A real terminal with no flags, unless a test says otherwise."""
    defaults: dict[str, object] = {
        "print_prompt": None,
        "repl": False,
        "stdin_tty": True,
        "stdout_tty": True,
        "textual_available": True,
    }
    return choose_mode(**{**defaults, **overrides})  # type: ignore[arg-type]


# ---------------------------------------------------------------- the default


def test_bare_omega_on_a_terminal_opens_the_tui() -> None:
    """**The literal complaint.** `uv run omega` showed no UI."""
    assert _mode() == "tui"


def test_repl_is_still_reachable_on_a_terminal() -> None:
    """Kept rather than deleted: it is the fallback when Textual cannot run."""
    assert _mode(repl=True) == "repl"


# ------------------------------------------------------- the terminal decides


@pytest.mark.parametrize(
    ("stdin_tty", "stdout_tty"),
    [(False, True), (True, False), (False, False)],
)
def test_a_pipe_on_either_side_falls_back_to_the_repl(
    stdin_tty: bool, stdout_tty: bool
) -> None:
    """Textual takes the screen, reads raw keys and writes escape sequences.

    Pointed at a pipe it emits control codes where output was expected and reads
    EOF where a keystroke was. Both halves matter: `omega < script.txt` breaks on
    stdin, `omega | tee log` breaks on stdout, and neither should start a UI.
    """
    assert _mode(stdin_tty=stdin_tty, stdout_tty=stdout_tty) == "repl"


# ----------------------------------------------------------------- print mode


def test_print_wins_over_everything_including_a_terminal() -> None:
    """`-p` is an explicit instruction, not a hint. Sitting at a terminal does
    not make a one-shot run interactive."""
    assert _mode(print_prompt="say hi") == "print"
    assert _mode(print_prompt="say hi", repl=True) == "print"
    assert _mode(print_prompt="-", stdin_tty=False, stdout_tty=False) == "print"


async def test_print_mode_puts_the_answer_on_stdout_and_nothing_else(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**The property that makes `-p` pipeable, and the one I got wrong first.**

    The first version let the startup banner reach stdout, so
    `omega -p "..." > out.txt` captured seven lines of orientation above the
    answer. Tool activity has the same problem, so it goes to stderr — where a
    redirect leaves it alone but a human still sees it.
    """
    harness = Harness(
        provider=FakeProvider(
            [tool_turn("run_shell", {"command": "echo hi"}), text_turn("All done.")]
        ),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )

    code = await _run_print(harness, "do the thing")

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip().endswith("All done.")
    assert "run_shell" not in captured.out, "tool activity leaked into the answer"
    assert "running" in captured.err, "tool activity should still be visible on stderr"


async def test_print_mode_reports_failure_through_the_exit_code(tmp_path: Path) -> None:
    """A script in a pipeline checks `$?`, not the wording of a message."""
    harness = Harness(
        provider=FakeProvider([]),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )

    assert await _run_print(harness, "do the thing") == 1



# ------------------------------------------------- when the UI cannot be loaded


def test_a_missing_textual_falls_back_instead_of_crashing() -> None:
    """**Reported from a real terminal, and it was a traceback.**

    `textual` became a dependency at Tier 3. An `omega` installed with
    `uv tool install --editable` *before* that keeps running the current source
    against its old environment — so the code imports a module its venv has never
    had, and the user gets `ModuleNotFoundError` on a program that worked
    yesterday.

    The REPL is a complete agent. "Your UI is missing" is not a reason to refuse
    to work.
    """
    assert _mode(textual_available=False) == "repl"


def test_the_fallback_does_not_hijack_an_explicit_choice() -> None:
    """`-p` and `--repl` never wanted the UI, so nothing about them changes."""
    assert _mode(print_prompt="say hi", textual_available=False) == "print"
    assert _mode(repl=True, textual_available=False) == "repl"


def test_availability_is_decided_before_the_banner_not_at_the_import() -> None:
    """**The second bug, which the first fix caused.**

    Catching `ModuleNotFoundError` at the deferred import works, but by then the
    banner has printed "Ctrl+Q quits" — and the REPL it is about to start has no
    Ctrl+Q. A mode that can still change after the screen has been described to
    the user is not a mode, so availability is an input to `choose_mode`.
    """
    assert _mode(textual_available=False) == "repl"
    assert _mode(textual_available=True) == "tui"


def test_textual_really_is_available_in_this_environment() -> None:
    """The probe itself, against the venv running the suite.

    Worth one line: every test above passes a boolean, so a `textual_is_available`
    that always returned False would not fail any of them — and would silently
    move every developer onto the REPL.
    """
    assert textual_is_available() is True
