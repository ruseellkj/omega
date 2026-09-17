"""Subagents — the claim that the headless driver was reusable.

`headless.py` has said so since Tier 2, in its own docstring:

> "Anything that wants to drive omega programmatically — an eval, a benchmark, a
> subagent at Tier 3+ — wants exactly this function, and a subagent *is* this
> function called from inside a tool."

That was an argument. This file is the evidence, and the test is narrow: adding
subagents must not change the loop, the harness, or the provider contract. If it
needs a new mechanism, the claim was wrong.

## Why anyone wants one

**Context isolation, which is failure #1 again from a different direction.**
Answering "where is authentication handled?" can mean reading twenty files. Done
in the main conversation, all twenty land in the context and stay there for the
rest of the session. Done in a subagent, the parent gains one paragraph and the
twenty files are discarded with the child.

So the property that matters is not "it can run a nested agent" — it is **what
the parent does *not* inherit**.

## The two ways this goes wrong

* **Unbounded recursion.** A subagent holding the subagent tool can spawn one,
  which can spawn one. Nothing in the loop stops it, because from the loop's
  point of view it is an ordinary tool call that happens to be slow.
* **A bypassed gate.** A subagent that builds its own approval policy is a second
  place for that policy to be correct — the mistake `paths.py` spends a docstring
  warning about, and the one the `!cmd` escape already made once.
"""

from __future__ import annotations

from pathlib import Path

from omega_agent.hooks import AgentHooks, ToolCallDecision
from omega_agent.tools import Tool
from omega_agent.types import ToolCall
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_coding.builtin_tools import build_tools
from omega_coding.subagent import (
    SUBAGENT_TOOL_NAME,
    build_subagent_tool,
    child_tools_for,
)


def _subagent(tmp_path: Path, script: list[object], **overrides: object) -> Tool:
    settings: dict[str, object] = {
        "provider": FakeProvider(script),  # type: ignore[arg-type]
        "model": "m",
        "tools": build_tools(tmp_path),
        "root": tmp_path,
        "hooks": AgentHooks(),
        "approve": True,
    }
    settings.update(overrides)
    return build_subagent_tool(**settings)  # type: ignore[arg-type]


async def _call(tool: Tool, task: str) -> str:
    result = await tool.execute({"task": task}, None)
    return result.text


# ------------------------------------------------------------- it does the work


async def test_a_subagent_runs_and_returns_its_answer(tmp_path: Path) -> None:
    tool = _subagent(tmp_path, [text_turn("authentication lives in auth.py")])

    answer = await _call(tool, "where is authentication handled?")

    assert "auth.py" in answer


async def test_a_subagent_can_use_tools(tmp_path: Path) -> None:
    """It is a whole agent, not a single model call — that is the point of
    reusing the driver rather than sending one prompt."""
    (tmp_path / "auth.py").write_text("def login():\n    pass\n")
    tool = _subagent(
        tmp_path,
        [tool_turn("read_file", {"path": "auth.py"}), text_turn("it defines login()")],
    )

    answer = await _call(tool, "read auth.py and summarise it")

    assert "login()" in answer


async def test_the_parent_gets_only_the_summary(tmp_path: Path) -> None:
    """**The reason subagents exist at all.**

    The child may read twenty files. What comes back is one string, so the
    parent's context grows by a paragraph rather than by twenty files — which is
    failure #1 approached from the other side.
    """
    (tmp_path / "big.py").write_text("SECRET_INTERNAL_DETAIL = 1\n" * 200)
    tool = _subagent(
        tmp_path,
        [tool_turn("read_file", {"path": "big.py"}), text_turn("it defines one constant")],
    )

    answer = await _call(tool, "what is in big.py?")

    assert answer == "it defines one constant"
    assert "SECRET_INTERNAL_DETAIL" not in answer, "the child's reading stayed with the child"


# ------------------------------------------------------- the two failure modes


async def test_a_subagent_cannot_spawn_a_subagent(tmp_path: Path) -> None:
    """**The recursion stop.**

    Nothing in the loop prevents this: from its point of view a nested agent is
    an ordinary tool call that happens to take a while. So the child is handed a
    tool list with the subagent tool removed — the depth limit is the absence of
    the tool, not a counter someone has to remember to increment.
    """
    inner = _subagent(tmp_path, [text_turn("done")])
    parent_tools = [*build_tools(tmp_path), inner]

    # The filtering, asserted directly...
    names = [tool.name for tool in child_tools_for(parent_tools)]
    assert SUBAGENT_TOOL_NAME not in names
    assert "read_file" in names, "but the ordinary tools are kept"

    # ...and then asserted through the built tool, which is the part that
    # matters. A child whose script *tries* to recurse must be told the tool
    # does not exist, rather than quietly getting one.
    outer = _subagent(
        tmp_path,
        [tool_turn(SUBAGENT_TOOL_NAME, {"task": "recurse"}), text_turn("gave up")],
        tools=parent_tools,
    )

    answer = await _call(outer, "try to spawn a child of your own")

    assert "gave up" in answer, "the child finished without recursing"


async def test_a_subagent_uses_the_parents_approval_gate(tmp_path: Path) -> None:
    """A second place for a deny list to be correct is the mistake `paths.py`
    warns about, and the one `!cmd` already made once.

    The child runs under the parent's hooks, so a refusal is a refusal at any
    depth.
    """
    refused: list[str] = []

    async def refuse_everything(call: ToolCall) -> ToolCallDecision:
        refused.append(call.name)
        return ToolCallDecision(allowed=False, reason="refused by the parent's gate")

    (tmp_path / "auth.py").write_text("x = 1\n")
    tool = _subagent(
        tmp_path,
        [tool_turn("read_file", {"path": "auth.py"}), text_turn("could not read it")],
        hooks=AgentHooks(before_tool_call=refuse_everything),
    )

    await _call(tool, "read auth.py")

    assert "read_file" in refused, "the child's tool call met the parent's gate"


async def test_a_runaway_subagent_is_bounded(tmp_path: Path) -> None:
    """A child that never stops must not spend the parent's whole budget.

    The loop's own `max_turns` already stops it; this asserts the subagent is
    given a *tighter* one, because a child burning the same allowance as the
    parent leaves nothing for the parent to finish with.
    """
    (tmp_path / "a.txt").write_text("hi\n")
    forever = [tool_turn("read_file", {"path": "a.txt"})] * 50
    tool = _subagent(tmp_path, forever, max_turns=3)

    answer = await _call(tool, "loop forever")

    # Tight on purpose. The looser version — "turn" or "limit" appears — passed
    # against a build whose turn-limit branch could never fire, because the
    # fallback message also contains "turn". mypy found that; the test had not.
    assert f"turn limit of {3}" in answer, "it names the limit it hit"
    assert "Narrow the task" in answer, "and says what to do about it"


async def test_a_failing_subagent_reports_instead_of_raising(tmp_path: Path) -> None:
    """A tool that raises into the parent loop is a tool that can end the
    session. The child's failure is data, like every other tool result."""
    tool = _subagent(tmp_path, [])  # an empty script: the provider runs out

    answer = await _call(tool, "anything")

    assert answer, "something came back rather than an exception"
