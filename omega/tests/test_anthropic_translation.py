"""Translation into Anthropic's wire format.

Pure functions, plus a stub standing in for the SDK client where the thing under
test is how a stream *ends* - no network either way. The vendor rules encoded
here are the kind you discover by having a request rejected, so they are worth
pinning.
"""

from __future__ import annotations

import typing
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import StopReason

import omega_ai.anthropic as adapter
import stub_anthropic
from omega_agent.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from omega_ai.anthropic import (
    AnthropicProvider,
    normalise_stop_reason,
    to_anthropic_messages,
    to_anthropic_tools,
)
from omega_ai.retry import RetryPolicy
from omega_coding.builtin_tools import build_tools


def test_tools_become_input_schema() -> None:
    read_file = next(t for t in build_tools(Path.cwd()) if t.name == 'read_file')
    [tool] = to_anthropic_tools([read_file])
    assert tool["name"] == "read_file"
    assert tool["input_schema"] == read_file.parameters


def test_user_message_round_trips() -> None:
    out = to_anthropic_messages([UserMessage(content="hi")])
    assert out == [{"role": "user", "content": "hi"}]


def test_tool_results_travel_as_a_user_message() -> None:
    """Anthropic has no toolResult role - results ride inside a user turn."""
    out = to_anthropic_messages(
        [ToolResultMessage(tool_call_id="a", tool_name="t", content="done")]
    )
    assert out[0]["role"] == "user"
    assert out[0]["content"][0]["type"] == "tool_result"
    assert out[0]["content"][0]["tool_use_id"] == "a"


def test_consecutive_tool_results_are_merged_into_one_message() -> None:
    """A turn with parallel tool calls is rejected if the results are split."""
    out = to_anthropic_messages(
        [
            ToolResultMessage(tool_call_id="a", tool_name="t", content="1"),
            ToolResultMessage(tool_call_id="b", tool_name="t", content="2"),
        ]
    )
    assert len(out) == 1
    assert len(out[0]["content"]) == 2


def test_assistant_tool_calls_become_tool_use_blocks() -> None:
    message = AssistantMessage(
        content=[
            TextContent(text="thinking out loud"),
            ToolCall(id="x", name="t", arguments={"a": 1}),
        ]
    )
    [out] = to_anthropic_messages([message])

    assert out["role"] == "assistant"
    assert [b["type"] for b in out["content"]] == ["text", "tool_use"]
    assert out["content"][1]["input"] == {"a": 1}


def test_empty_assistant_turns_are_dropped() -> None:
    """Providers reject an assistant message with no content outright."""
    out = to_anthropic_messages(
        [UserMessage(content="hi"), AssistantMessage(stop_reason="error", error_message="boom")]
    )
    assert len(out) == 1
    assert out[0]["role"] == "user"


def test_error_message_is_not_smuggled_into_content() -> None:
    out = to_anthropic_messages([AssistantMessage(stop_reason="error", error_message="secret")])
    assert out == []


def test_stop_reasons_normalise_to_three_values() -> None:
    assert normalise_stop_reason("tool_use", has_tool_calls=False) == "toolUse"
    assert normalise_stop_reason("max_tokens", has_tool_calls=False) == "length"
    assert normalise_stop_reason("end_turn", has_tool_calls=False) == "stop"
    assert normalise_stop_reason(None, has_tool_calls=False) == "stop"


def test_content_wins_over_a_disagreeing_stop_reason() -> None:
    """Same rule as the loop: if there are tool calls, it is a tool turn."""
    assert normalise_stop_reason("end_turn", has_tool_calls=True) == "toolUse"


# ----------------------------------------------- endings that are not a finish


def test_every_stop_reason_the_sdk_declares_is_mapped_on_purpose() -> None:
    """**The upgrade tripwire.** The installed SDK lists the values it can send,
    and each one needs a decision here rather than a fallback. Anything outside
    the table is reported as unknown, so a future SDK that adds a value fails
    this test instead of leaving a new kind of ending mislabelled."""
    declared = set(typing.get_args(StopReason))

    assert declared, "StopReason is a Literal; reading it found nothing"
    assert declared <= set(adapter.STOP_REASONS), declared - set(adapter.STOP_REASONS)


async def _ending(stop_reason: str | None) -> Any:
    client = stub_anthropic.StubClient(stop_reason=stop_reason)
    provider = AnthropicProvider(client=client, retry=RetryPolicy(attempts=1))  # type: ignore[arg-type]
    events = [
        event
        async for event in provider.stream_response(
            model="m", system="s", messages=[UserMessage(content="hi")], tools=[]
        )
    ]
    assert [e.type for e in events].count("start") == 1
    assert len([e for e in events if e.type in {"done", "error"}]) == 1, "one ending"
    return events[-1]


@pytest.mark.parametrize(
    ("raw", "said"),
    [
        ("refusal", "declined"),
        ("pause_turn", "paused"),
        ("a_reason_from_next_year", "a_reason_from_next_year"),
    ],
)
async def test_an_abnormal_stop_is_an_error_that_keeps_the_reply(raw: str, said: str) -> None:
    """**Measured before the fix:** each of these came back as `done` with
    `stop`, a clean finish. A refusal, a paused turn and a value nobody has seen
    were all indistinguishable from an answer, and nobody was told. Now the
    stream ends the way every other failure does, with an `error` that still
    holds the text that arrived, and a message saying what happened."""
    ending = await _ending(raw)

    assert ending.type == "error"
    assert ending.error.stop_reason == "error"
    assert said in (ending.error.error_message or "")
    assert ending.error.text == "hi", "the reply so far is kept"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        (None, "stop"),
        ("max_tokens", "length"),
        ("model_context_window_exceeded", "length"),
    ],
)
async def test_a_normal_stop_is_still_a_finish(raw: str | None, reason: str) -> None:
    """The overflow is the one that moved: it reported `stop`, as if the model
    had finished, when the reply was cut off by the context window."""
    ending = await _ending(raw)

    assert ending.type == "done"
    assert ending.reason == reason
    assert ending.message.stop_reason == reason


def test_content_still_wins_over_an_abnormal_reason() -> None:
    """Tool calls in the content make it a tool turn, whatever the label. An
    `error` ending would stop the loop with those calls unanswered, so the
    content-first rule stays ahead of the table."""
    assert normalise_stop_reason("refusal", has_tool_calls=True) == "toolUse"
