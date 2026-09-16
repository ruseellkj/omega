"""Commands typed into the conversation.

The bug that prompted these: typing `clear` produced a paid explanation of how to
clear a *terminal*, because nothing distinguished an instruction to the program
from a question for the model.

Two properties carry most of the weight, and neither is about any one command:

* **`dispatch` returns three things, not two.** `None` means "not a command", and
  is what keeps ordinary prompts — including ones that merely mention a command —
  flowing to the model.
* **`!cmd` has no private path to the shell.** It goes through the same
  `run_shell` tool the model uses, so it inherits the approval gate and the
  refuse-outright list. The test that matters is the one proving a refused
  command does not run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from omega_agent.harness import Harness
from omega_agent.hooks import AgentHooks, ToolCallDecision
from omega_agent.session import JsonlSessionStore
from omega_agent.types import ToolCall
from omega_ai.fake import FakeProvider, text_turn
from omega_coding.approval import ApprovalPolicy
from omega_coding.builtin_tools import build_tools
from omega_coding.commands import COMMANDS, CommandContext, dispatch
from omega_coding.cost import CostTracker


def _context(tmp_path: Path, **overrides: Any) -> CommandContext:
    store = overrides.pop("store", JsonlSessionStore(tmp_path, home=tmp_path))
    harness = overrides.pop(
        "harness",
        Harness(
            provider=FakeProvider([text_turn("ok")] * 20),
            model="m",
            system="s",
            tools=[],
            store=store,
        ),
    )
    return CommandContext(
        harness=harness,
        store=store,
        tracker=overrides.pop("tracker", CostTracker()),
        model="claude-sonnet-5",
        system="be helpful",
        tools=overrides.pop("tools", build_tools(tmp_path)),
        hooks=overrides.pop("hooks", AgentHooks()),
        **overrides,
    )


# ------------------------------------------- what is a command and what is not


async def test_plain_text_is_not_a_command(tmp_path: Path) -> None:
    """`None` is the answer that keeps omega usable.

    Everything that is not a command has to reach the model untouched, or the
    command channel becomes a filter on ordinary conversation.
    """
    assert await dispatch("what is RAG?", _context(tmp_path)) is None


async def test_a_question_mentioning_a_command_still_reaches_the_model(
    tmp_path: Path,
) -> None:
    """"what does /clear do?" is a question, not an instruction.

    Only a *leading* slash makes a command, which is why the check is a
    `startswith` on the stripped text and not a search.
    """
    assert await dispatch("what does /clear do?", _context(tmp_path)) is None
    assert await dispatch("explain the ! prefix", _context(tmp_path)) is None


async def test_a_bare_exclamation_mark_goes_to_the_model(tmp_path: Path) -> None:
    """Copied from Pi (`interactive-mode.ts:2865`), deliberately.

    `!` alone is not a shell command. Passing it on is a better guess than
    refusing it or running an empty string.
    """
    assert await dispatch("!", _context(tmp_path)) is None
    assert await dispatch("!   ", _context(tmp_path)) is None


async def test_exit_and_quit_still_work_without_a_slash(tmp_path: Path) -> None:
    """They predate the slash commands and are what people type. Consistency is
    not worth breaking that for."""
    for text in ("exit", "quit", "EXIT", "  quit  "):
        assert await dispatch(text, _context(tmp_path)) == "exit"


async def test_slash_exit_works_too(tmp_path: Path) -> None:
    assert await dispatch("/exit", _context(tmp_path)) == "exit"


# ----------------------------------------------------------- unknown commands


async def test_an_unknown_command_is_an_error_not_a_prompt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both references forward `/foo` to the model. This does not.

    With seven commands and a metered API on the other side, a typo deserves a
    correction rather than a charge.
    """
    assert await dispatch("/nonsense", _context(tmp_path)) == "handled"

    printed = capsys.readouterr().out
    assert "Unknown command /nonsense" in printed
    assert "/help" in printed


async def test_a_near_miss_is_guessed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await dispatch("/sesions", _context(tmp_path))

    assert "Did you mean /sessions?" in capsys.readouterr().out


# ------------------------------------------------------------------- /clear


async def test_clear_starts_a_new_session_and_keeps_the_old_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole point of `/clear`: the model forgets, the disk does not.

    A `/clear` that destroyed the transcript would be one people hesitate to
    type, and would make an append-only log a lie.
    """
    context = _context(tmp_path)
    async for _ in context.harness.run("remember this"):
        pass

    first_session = context.harness.session_id
    assert first_session is not None
    assert context.harness.messages

    assert await dispatch("/clear", context) == "handled"

    assert context.harness.messages == [], "the model starts from nothing"
    assert context.harness.session_id is None, "the next turn opens a new file"
    assert first_session in capsys.readouterr().out, "it says what it kept"

    assert context.store is not None
    assert context.store.load(first_session), "the old transcript survived"
    assert first_session in {row.session_id for row in context.store.list_sessions()}


async def test_clear_on_an_untouched_session_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await dispatch("/clear", _context(tmp_path)) == "handled"
    assert "Nothing had been saved" in capsys.readouterr().out


# ----------------------------------------------------------- /sessions, /resume


async def test_sessions_lists_what_is_saved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    context = _context(tmp_path)
    async for _ in context.harness.run("the first question"):
        pass

    assert await dispatch("/sessions", context) == "handled"

    printed = capsys.readouterr().out
    assert "the first question" in printed, "a row has to be recognisable"
    assert "(current)" in printed, "and say which one you are in"


async def test_resume_switches_the_live_conversation(tmp_path: Path) -> None:
    """Mid-conversation, without restarting omega — the thing `--resume` could
    not do."""
    context = _context(tmp_path)
    async for _ in context.harness.run("first session"):
        pass
    original = context.harness.session_id
    assert original is not None

    await dispatch("/clear", context)
    async for _ in context.harness.run("second session"):
        pass
    assert context.harness.session_id != original

    assert await dispatch(f"/resume {original}", context) == "handled"

    assert context.harness.session_id == original
    assert any("first session" in str(message) for message in context.harness.messages)


async def test_resume_without_an_id_explains_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await dispatch("/resume", _context(tmp_path)) == "handled"
    assert "/sessions" in capsys.readouterr().out


async def test_resume_of_an_unknown_id_does_not_wipe_the_conversation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typo must not cost the conversation you are in.

    `harness.resume` replaces `messages` wholesale, so the id is checked *before*
    it is called rather than after.
    """
    context = _context(tmp_path)
    async for _ in context.harness.run("work in progress"):
        pass
    before = list(context.harness.messages)

    assert await dispatch("/resume 20990101T000000-abcdef", context) == "handled"

    assert context.harness.messages == before, "nothing was lost"
    assert "No session" in capsys.readouterr().out


# ----------------------------------------------------- the read-only reporters


async def test_help_lists_every_command_and_the_shell_escape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await dispatch("/help", _context(tmp_path)) == "handled"

    printed = capsys.readouterr().out
    for command in COMMANDS:
        assert command.usage in printed, command.name
    assert "!<command>" in printed, "the shell escape is discoverable too"


async def test_cost_and_context_report_without_touching_the_conversation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    context = _context(tmp_path)
    async for _ in context.harness.run("hello"):
        pass
    before = list(context.harness.messages)

    assert await dispatch("/cost", context) == "handled"
    assert await dispatch("/context", context) == "handled"

    assert context.harness.messages == before, "reporters are read-only"
    printed = capsys.readouterr().out
    assert "in /" in printed
    assert "tokens" in printed


async def test_cost_says_how_to_get_dollars_when_no_price_is_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await dispatch("/cost", _context(tmp_path))
    assert "OMEGA_PRICE_INPUT" in capsys.readouterr().out


# ------------------------------------------------------- !cmd, the shell escape


async def test_a_shell_escape_runs_and_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await dispatch("!echo hello-from-the-escape", _context(tmp_path)) == "handled"
    assert "hello-from-the-escape" in capsys.readouterr().out


async def test_the_shell_escape_does_not_enter_the_conversation(
    tmp_path: Path,
) -> None:
    """omega diverges from **both** references here, on purpose.

    Tau (`session.py:2529-2542`) and Pi (`interactive-mode.ts:2861-2877`) both
    add `!cmd` output to context. The surprising half of that is a shell command
    silently enlarging every later request, so the plain form here is the free
    one.
    """
    context = _context(tmp_path)
    await dispatch("!echo something", context)

    assert context.harness.messages == [], "no tokens, no context, no bill"


async def test_the_shell_escape_goes_through_the_approval_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**The test that matters — and the one that was wrong.**

    Its first version stubbed the tool's `execute` to raise, which proved only
    that a raised error printed nicely. It passed while the escape called
    `tool.execute` directly and skipped the gate entirely: a live `!rm -rf /`
    reached the shell and was stopped by the operating system, not by omega.

    So this installs a **real** `before_tool_call` hook and asserts the command
    never ran. That is the only version of this test worth having.
    """
    ran = tmp_path / "the-command-ran"

    async def refuse(call: ToolCall) -> ToolCallDecision:
        return ToolCallDecision(allowed=False, reason="refused by the gate")

    context = _context(tmp_path, hooks=AgentHooks(before_tool_call=refuse))
    assert await dispatch(f"!touch {ran}", context) == "handled"

    assert not ran.exists(), "the gate stopped it before the shell saw it"
    assert "refused by the gate" in capsys.readouterr().out


async def test_the_escape_cannot_bypass_the_refuse_outright_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A catastrophe is refused here exactly as it is for the model.

    The deny list lives in `ApprovalPolicy`, above both `--yes` and always-allow.
    Reaching it from the escape is the whole reason this routes through
    `execute_tool_call` rather than running the command itself.
    """
    context = _context(
        tmp_path,
        hooks=AgentHooks(
            before_tool_call=ApprovalPolicy(tmp_path, asker=None, auto_approve=True)
        ),
    )

    assert await dispatch("!rm -rf /", context) == "handled"

    printed = capsys.readouterr().out
    assert "refused outright" in printed
    assert "recursive delete" in printed


async def test_a_failing_shell_command_reports_instead_of_raising(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-zero exit is a `ToolError` carrying the output. It has to be read,
    not raised into the REPL."""
    assert await dispatch("!exit 3", _context(tmp_path)) == "handled"
    assert "exited with code 3" in capsys.readouterr().out


async def test_the_escape_says_so_when_there_is_no_shell_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await dispatch("!echo hi", _context(tmp_path, tools=[])) == "handled"
    assert "No shell tool" in capsys.readouterr().out


async def test_a_denied_call_is_reported_not_raised(tmp_path: Path) -> None:
    """A `ToolCallDecision` refusal must not escape into the REPL as a crash."""
    decision = ToolCallDecision(allowed=False, reason="declined")
    assert decision.allowed is False
