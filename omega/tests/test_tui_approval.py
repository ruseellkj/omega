"""The approval modal — the screen that let the TUI become the default.

Every test here drives Textual's own pilot, because the thing under test is
exactly the part state assertions cannot reach: whether a keypress on a screen
reaches the gate that decides if a tool runs.

**The observable is the filesystem, not the transcript.** Asserting that a row
said "denied" would pass against a build that printed the word and wrote the file
anyway. Asserting that `tmp_path//written.txt` does not exist cannot.
"""

from __future__ import annotations

from pathlib import Path

from omega_agent.harness import Harness
from omega_agent.hooks import AgentHooks
from omega_agent.types import ToolCall
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_coding.approval import ApprovalPolicy
from omega_coding.builtin_tools import build_tools
from omega_coding.tui.app import OmegaApp

TARGET = "written.txt"


def _app(tmp_path: Path, *, auto_approve: bool = False) -> tuple[OmegaApp, ApprovalPolicy]:
    """A one-tool turn that tries to write a file, behind a real gate."""
    policy = ApprovalPolicy(tmp_path, asker=None, auto_approve=auto_approve)
    harness = Harness(
        provider=FakeProvider(
            [
                tool_turn("write_file", {"path": TARGET, "content": "hello"}),
                text_turn("Done."),
            ]
        ),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
        hooks=AgentHooks(before_tool_call=policy),
    )
    return OmegaApp(harness, policy=policy), policy


async def _drive(app: OmegaApp, key: str) -> None:
    async with app.run_test() as pilot:
        app.query_one("Input").value = "write the file"  # type: ignore[attr-defined]
        await pilot.press("enter")
        # Long enough for the turn to reach the gate and push the screen.
        await pilot.pause(0.3)
        await pilot.press(key)
        await pilot.pause(0.3)


async def test_denying_in_the_modal_actually_blocks_the_write(tmp_path: Path) -> None:
    """`n` must stop the tool, not merely label it.

    Verified by reverting: with the modal's answer ignored and `"once"` returned
    unconditionally, this fails on the `exists()` assertion.
    """
    app, _ = _app(tmp_path)

    await _drive(app, "n")

    assert not (tmp_path / TARGET).exists(), "denied, but the file was written anyway"


async def test_approving_once_lets_the_write_through(tmp_path: Path) -> None:
    """The other half. Without it, a gate that denies everything would pass the
    test above and be equally wrong."""
    app, _ = _app(tmp_path)

    await _drive(app, "y")

    assert (tmp_path / TARGET).read_text() == "hello"


async def test_escape_denies_rather_than_dismissing_into_consent(tmp_path: Path) -> None:
    """The rule `_ask_in_terminal` set: the easiest answer must be "no".

    A modal has more exits than a line of input does — Escape, the close, the app
    shutting down — and every one of them has to land on refusal.
    """
    app, _ = _app(tmp_path)

    await _drive(app, "escape")

    assert not (tmp_path / TARGET).exists()


async def test_the_gate_refuses_while_no_screen_has_claimed_it(tmp_path: Path) -> None:
    """Late binding has to fail safe.

    `use_asker` is called on mount, so there is a window where the policy has no
    approval channel. That window must deny. If `_asker is None` ever fell
    through to allow, every tool call before the first paint would be silently
    approved.
    """
    policy = ApprovalPolicy(tmp_path, asker=None)

    decision = await policy(
        ToolCall(id="1", name="write_file", arguments={"path": TARGET, "content": "x"})
    )

    assert decision.allowed is False
    assert "no approval channel" in (decision.reason or "")
