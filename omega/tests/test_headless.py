"""The headless driver and the smoke eval — anatomy.md #35.

Two things worth testing here, and they are different in kind.

The **driver** is library code: a prompt in, a finished transcript out, no
keyboard. anatomy.md:400 says build it once and use it twice, because it is also
the terminal-bench interface — and at Tier 3+ a subagent is literally this
function called from inside a tool.

The **eval** is not a test. The tests check units against fakes; the eval checks
the assembled agent against a task. It catches the class of breakage where every
unit passes and the whole thing still does nothing: a tool registered under the
wrong name, a gate refusing everything, a system prompt that stopped mentioning
the tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omega_agent.hooks import AgentHooks, ToolCallDecision
from omega_agent.tools import Tool, ToolResult
from omega_agent.types import ToolCall
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_coding.builtin_tools import build_tools
from omega_coding.headless import run_headless


async def _ok(arguments: dict[str, Any], signal: Any) -> ToolResult:
    return ToolResult(content="ran")


OK_TOOL = Tool(name="ok", description="works", parameters={"type": "object"}, execute=_ok)


async def _run(streams: list[list[Any]], **kwargs: Any) -> Any:
    return await run_headless(
        provider=FakeProvider(streams),
        model="m",
        system="s",
        prompt="do the thing",
        tools=[OK_TOOL],
        approve=True,
        **kwargs,
    )


# ------------------------------------------------------------------ the driver


async def test_a_prompt_goes_in_and_a_transcript_comes_out() -> None:
    result = await _run([text_turn("all done")])

    assert result.ok is True
    assert result.reason == "stop"
    assert result.text == "all done"
    assert [m.role for m in result.messages] == ["user", "assistant"]


async def test_it_reports_which_tools_ran() -> None:
    """What an eval usually asserts on."""
    result = await _run([tool_turn("ok", {}), text_turn("done")])

    assert result.tool_names == ["ok"]


async def test_text_is_the_last_thing_the_model_said() -> None:
    """Not the first, and not a concatenation of every turn."""
    result = await _run([tool_turn("ok", {}, text="thinking"), text_turn("the answer")])

    assert result.text == "the answer"


async def test_an_answer_cut_off_at_the_length_limit_is_not_a_success() -> None:
    """The run ends as `stop` — the loop stops on content — so `ok` said True
    for half an answer. Everything that reads `ok` (the evals, the sub-agent
    tool) then treated it as finished."""
    result = await _run([text_turn("Found two of the", stop_reason="length")])

    assert result.reason == "stop", "the loop's reason is unchanged"
    assert result.cut_off is True
    assert result.ok is False


async def test_a_failed_run_is_reported_not_raised() -> None:
    result = await _run([tool_turn("ok", {}) for _ in range(5)], max_turns=2)

    assert result.ok is False
    assert result.reason == "max_turns"
    assert "max_turns=2" in (result.error_message or "")


async def test_a_run_with_no_text_has_empty_text_not_an_error() -> None:
    result = await _run([tool_turn("ok", {}) for _ in range(3)], max_turns=1)

    assert result.text == ""


# ------------------------------------------------------------------ approvals


async def test_approve_false_means_the_tools_are_refused() -> None:
    """Headless means nobody is watching, so this must be a real choice."""
    result = await run_headless(
        provider=FakeProvider([tool_turn("ok", {}), text_turn("gave up")]),
        model="m",
        system="s",
        prompt="do it",
        tools=[OK_TOOL],
        approve=False,
    )

    from omega_agent.types import ToolResultMessage

    refusal = next(m for m in result.messages if isinstance(m, ToolResultMessage))
    assert refusal.is_error is True


async def test_a_caller_supplied_gate_wins() -> None:
    """`approve` is a convenience, not an override of a real policy."""
    seen: list[str] = []

    async def gate(call: ToolCall) -> ToolCallDecision:
        seen.append(call.name)
        return ToolCallDecision(allowed=False, reason="my rules")

    result = await run_headless(
        provider=FakeProvider([tool_turn("ok", {}), text_turn("fine")]),
        model="m",
        system="s",
        prompt="do it",
        tools=[OK_TOOL],
        hooks=AgentHooks(before_tool_call=gate),
        approve=True,
    )

    assert seen == ["ok"], "the caller's gate was bypassed"
    assert "my rules" in str(result.messages)


# ----------------------------------------------------------------- the eval


async def test_the_smoke_eval_passes_against_a_working_model() -> None:
    from omega_coding.evals import _scripted_provider, run_smoke

    checks = await run_smoke(_scripted_provider(), model="fake-model")

    assert len(checks) == 4
    assert all(check.passed for check in checks), [str(c) for c in checks]


async def test_the_smoke_eval_fails_when_the_agent_does_nothing() -> None:
    """An eval that cannot fail is not measuring anything."""
    from omega_coding.evals import run_smoke

    checks = await run_smoke(FakeProvider([text_turn("I would rather not")]), model="fake-model")

    assert not all(check.passed for check in checks)
    assert [c.name for c in checks if not c.passed] == [
        "a tool was called",
        "the file exists",
        "the file has the right contents",
    ]


async def test_the_eval_writes_nowhere_near_the_repo() -> None:
    """An eval that writes into the repo it tests eventually breaks the repo."""
    from omega_coding.evals import _scripted_provider, run_smoke

    before = set(Path.cwd().iterdir())
    await run_smoke(_scripted_provider(), model="fake-model")

    assert set(Path.cwd().iterdir()) == before


async def test_the_eval_grades_a_wrong_answer_as_wrong() -> None:
    """Right tool, wrong contents. The check that catches a plausible failure."""
    from omega_coding.evals import run_smoke

    checks = await run_smoke(
        FakeProvider(
            [
                tool_turn("write_file", {"path": "hello.txt", "content": "goodbye"}),
                text_turn("done"),
            ]
        ),
        model="fake-model",
    )

    by_name = {check.name: check for check in checks}
    assert by_name["the file exists"].passed is True
    assert by_name["the file has the right contents"].passed is False


# --------------------------------------------------------- it is the real thing


async def test_the_driver_uses_the_real_tools(tmp_path: Path) -> None:
    """Not a mock of the agent - the agent, with a fake provider in front of it."""
    result = await run_headless(
        provider=FakeProvider(
            [
                tool_turn("write_file", {"path": "out.txt", "content": "written headlessly"}),
                text_turn("done"),
            ]
        ),
        model="m",
        system="s",
        prompt="write a file",
        tools=build_tools(tmp_path),
        approve=True,
    )

    assert result.ok is True
    assert (tmp_path / "out.txt").read_text() == "written headlessly"
