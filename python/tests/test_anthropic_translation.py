"""Translation into Anthropic's wire format.

Pure functions only - no client, no network. The vendor rules encoded here are
the kind you discover by having a request rejected, so they are worth pinning.
"""

from __future__ import annotations

from omega.builtin_tools import READ_FILE
from omega.providers.anthropic import (
    normalise_stop_reason,
    to_anthropic_messages,
    to_anthropic_tools,
)
from omega.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage


def test_tools_become_input_schema() -> None:
    [tool] = to_anthropic_tools([READ_FILE])
    assert tool["name"] == "read_file"
    assert tool["input_schema"] == READ_FILE.parameters


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
