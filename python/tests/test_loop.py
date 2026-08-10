"""The tests that prove Tier 1.

Every one runs offline: the provider is faked at the interface boundary, so
there is no network, no API key, and no cost. That is the payoff for writing
`providers/fake.py` before the real adapter.
"""

from __future__ import annotations

from typing import Any

import pytest

from omega.loop import run_agent_loop
from omega.providers.fake import FakeProvider, text_turn, tool_turn
from omega.tools import Tool, ToolResult
from omega.types import (
    AgentMessage,
    AssistantMessage,
    CancellationToken,
    ToolResultMessage,
    UserMessage,
)


async def _ok(arguments: dict[str, Any], signal: CancellationToken | None) -> ToolResult:
    return ToolResult(content=f"ran with {arguments}")


async def _boom(arguments: dict[str, Any], signal: CancellationToken | None) -> ToolResult:
    raise RuntimeError("tool exploded")


OK_TOOL = Tool(name="ok", description="works", parameters={"type": "object"}, execute=_ok)
BOOM_TOOL = Tool(name="boom", description="fails", parameters={"type": "object"}, execute=_boom)


async def _run(
    streams: list[list[Any]],
    *,
    tools: list[Tool] | None = None,
    max_turns: int = 25,
) -> tuple[list[AgentMessage], list[Any], FakeProvider]:
    messages: list[AgentMessage] = [UserMessage(content="go")]
    provider = FakeProvider(streams)
    events = [
        event
        async for event in run_agent_loop(
            provider=provider,
            model="test-model",
            system="be helpful",
            messages=messages,
            tools=tools if tools is not None else [OK_TOOL, BOOM_TOOL],
            max_turns=max_turns,
        )
    ]
    return messages, events, provider


async def test_stops_when_no_tool_calls() -> None:
    messages, _events, provider = await _run([text_turn("all done")])

    assert len(provider.calls) == 1, "one turn should mean one provider call"
    assert [m.role for m in messages] == ["user", "assistant"]
    assistant = messages[-1]
    assert isinstance(assistant, AssistantMessage)
    assert assistant.text == "all done"


async def test_stop_condition_follows_content_not_stop_reason() -> None:
    """The case that separates a correct loop from a plausible one.

    The provider reports `stop_reason="stop"` while the message still carries a
    tool call. Content is ground truth; the loop must keep going.
    """
    messages, _events, provider = await _run(
        [
            tool_turn("ok", {"x": 1}, stop_reason="stop"),
            text_turn("finished"),
        ]
    )

    assert len(provider.calls) == 2, "loop stopped early - it trusted stop_reason over content"
    assert [m.role for m in messages] == ["user", "assistant", "toolResult", "assistant"]


async def test_tool_exception_becomes_error_result_and_run_continues() -> None:
    messages, _events, provider = await _run([tool_turn("boom", {}), text_turn("recovered")])

    result = next(m for m in messages if isinstance(m, ToolResultMessage))
    assert result.is_error is True
    assert "tool exploded" in result.text
    assert len(provider.calls) == 2, "a crashing tool must not end the run"


async def test_unknown_tool_becomes_error_result() -> None:
    messages, _events, _provider = await _run([tool_turn("does_not_exist", {}), text_turn("ok")])

    result = next(m for m in messages if isinstance(m, ToolResultMessage))
    assert result.is_error is True
    assert "not found" in result.text


async def test_max_turns_halts_a_provider_that_never_stops() -> None:
    _messages, events, provider = await _run([tool_turn("ok", {}) for _ in range(10)], max_turns=3)

    assert len(provider.calls) == 3
    assert events[-1].type == "error"
    assert "max_turns=3" in events[-1].error.error_message


async def test_terminal_error_ends_the_run_without_raising() -> None:
    from omega.events import AssistantErrorEvent

    failed = AssistantMessage(model="test-model", stop_reason="error", error_message="upstream 500")
    messages, events, provider = await _run(
        [[AssistantErrorEvent(reason="error", error=failed)], text_turn("never reached")]
    )

    assert len(provider.calls) == 1, "the loop must not continue past a terminal error"
    assert events[-1].type == "error"
    last = messages[-1]
    assert isinstance(last, AssistantMessage)
    assert last.stop_reason == "error"


async def test_tool_results_are_paired_to_their_calls() -> None:
    """Every tool call must be answered, keyed by id - providers reject otherwise."""
    messages, _events, _provider = await _run([tool_turn("ok", {}, call_id="abc"), text_turn("x")])

    call_ids = [
        block.id for m in messages if isinstance(m, AssistantMessage) for block in m.tool_calls
    ]
    result_ids = [m.tool_call_id for m in messages if isinstance(m, ToolResultMessage)]
    assert call_ids == result_ids == ["abc"]


async def test_provider_receives_the_growing_transcript() -> None:
    _messages, _events, provider = await _run([tool_turn("ok", {}), text_turn("done")])

    first, second = provider.calls
    assert len(first.messages) == 1, "first call sees just the user prompt"
    assert len(second.messages) == 3, "second call sees user + assistant + tool result"
    assert second.system == "be helpful"


async def test_events_are_passed_through_in_order() -> None:
    _messages, events, _provider = await _run([text_turn("hi")])

    assert [e.type for e in events] == ["start", "text_start", "text_delta", "text_end", "done"]


@pytest.mark.parametrize("turns", [1, 5])
async def test_exactly_one_provider_call_per_turn(turns: int) -> None:
    streams = [tool_turn("ok", {}) for _ in range(turns - 1)] + [text_turn("end")]
    _messages, _events, provider = await _run(streams)
    assert len(provider.calls) == turns
