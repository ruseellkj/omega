"""OpenAI Chat Completions translation — the vendor rules, and where they live.

The contract suite proves both adapters keep the same promises. This file proves
the *differences* are real, because an exam is only meaningful if the second
format actually diverges. Each test below is a rule that would have leaked
upward had Tier 1 got the boundary wrong.

The sharpest one is `test_the_two_adapters_disagree_about_tool_results`:
Anthropic rejects tool results split across messages, OpenAI requires it, and
the neutral `ToolResultMessage` above them never had to pick a side.
"""

from __future__ import annotations

import typing
from typing import Any

import pytest
from openai.types.chat.chat_completion_chunk import Choice

import omega_ai.openai as adapter
import stub_openai
from omega_agent.tools import Tool
from omega_agent.types import (
    AgentMessage,
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from omega_ai.anthropic import to_anthropic_messages
from omega_ai.openai import (
    OpenAIProvider,
    normalise_finish_reason,
    to_openai_messages,
    to_openai_tools,
)
from omega_ai.retry import RetryPolicy


def _tool() -> Tool:
    return Tool(
        name="read_file",
        description="reads a file",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        execute=None,  # type: ignore[arg-type]
    )


# ------------------------------------------------------- the system prompt


def test_the_system_prompt_becomes_the_first_message() -> None:
    """Anthropic takes it as a parameter. Here it is a message, and it goes first."""
    out = to_openai_messages("be helpful", [UserMessage(content="hi")])

    assert out[0] == {"role": "system", "content": "be helpful"}
    assert out[1]["role"] == "user"


def test_an_empty_system_prompt_adds_no_message() -> None:
    out = to_openai_messages("", [UserMessage(content="hi")])

    assert [m["role"] for m in out] == ["user"]


# --------------------------------------------------------- tool results


def test_each_tool_result_is_its_own_message() -> None:
    """The rule that is the exact opposite of the other adapter's."""
    messages: list[AgentMessage] = [
        AssistantMessage(
            model="m",
            stop_reason="toolUse",
            content=[
                ToolCall(id="a", name="read_file", arguments={"path": "1.txt"}),
                ToolCall(id="b", name="read_file", arguments={"path": "2.txt"}),
            ],
        ),
        ToolResultMessage(tool_call_id="a", tool_name="read_file", content="one"),
        ToolResultMessage(tool_call_id="b", tool_name="read_file", content="two"),
    ]

    out = to_openai_messages("", messages)

    assert [m["role"] for m in out] == ["assistant", "tool", "tool"]
    assert out[1]["tool_call_id"] == "a"
    assert out[2]["tool_call_id"] == "b"


def test_the_two_adapters_disagree_about_tool_results() -> None:
    """Side by side, because this is the whole argument for the layer.

    Anthropic **rejects** results split across messages. OpenAI **requires** it.
    Two adapters, opposite rules, and nothing above either of them knows.
    """
    messages: list[AgentMessage] = [
        AssistantMessage(
            model="m",
            stop_reason="toolUse",
            content=[
                ToolCall(id="a", name="read_file", arguments={}),
                ToolCall(id="b", name="read_file", arguments={}),
            ],
        ),
        ToolResultMessage(tool_call_id="a", tool_name="read_file", content="one"),
        ToolResultMessage(tool_call_id="b", tool_name="read_file", content="two"),
    ]

    anthropic_out = to_anthropic_messages(messages)
    openai_out = to_openai_messages("", messages)

    # Anthropic: both results merged into ONE user message.
    assert [m["role"] for m in anthropic_out] == ["assistant", "user"]
    assert len(anthropic_out[1]["content"]) == 2

    # OpenAI: one message per result.
    assert [m["role"] for m in openai_out] == ["assistant", "tool", "tool"]


def test_an_empty_tool_result_still_says_something() -> None:
    """Some endpoints reject a blank tool message, and "" tells the model nothing."""
    out = to_openai_messages(
        "", [ToolResultMessage(tool_call_id="a", tool_name="ok", content="")]
    )

    assert out[0]["content"] == "(no output)"


# ------------------------------------------------------ assistant messages


def test_tool_calls_are_serialised_as_json_strings() -> None:
    """Arguments go out as a string, not an object. A real difference."""
    out = to_openai_messages(
        "",
        [
            AssistantMessage(
                model="m",
                stop_reason="toolUse",
                content=[ToolCall(id="a", name="read_file", arguments={"path": "x.txt"})],
            )
        ],
    )

    call = out[0]["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["arguments"] == '{"path": "x.txt"}'


def test_text_and_tool_calls_travel_on_one_message() -> None:
    out = to_openai_messages(
        "",
        [
            AssistantMessage(
                model="m",
                stop_reason="toolUse",
                content=[
                    TextContent(text="let me look"),
                    ToolCall(id="a", name="read_file", arguments={}),
                ],
            )
        ],
    )

    assert out[0]["content"] == "let me look"
    assert len(out[0]["tool_calls"]) == 1


def test_an_empty_assistant_turn_is_dropped() -> None:
    """Same rule as the other adapter, same reason: providers reject them.

    A turn that failed before producing anything is exactly this shape.
    """
    messages: list[AgentMessage] = [
        UserMessage(content="go"),
        AssistantMessage(model="m", stop_reason="error", error_message="died"),
        UserMessage(content="again"),
    ]

    assert [m["role"] for m in to_openai_messages("", messages)] == ["user", "user"]


def test_thinking_blocks_are_dropped_without_complaint() -> None:
    """This format has no reasoning block. Absence is not an error.

    A vocabulary that allows more than any single provider produces is correct;
    one shaped to a single vendor's features is how detail leaks upward.
    """
    out = to_openai_messages(
        "",
        [
            AssistantMessage(
                model="m",
                stop_reason="stop",
                content=[
                    ThinkingContent(thinking="hmm", signature="opaque"),
                    TextContent(text="the answer"),
                ],
            )
        ],
    )

    assert out[0]["content"] == "the answer"
    assert "thinking" not in str(out)


# ------------------------------------------------------------------ tools


def test_tools_are_wrapped_in_a_function_envelope() -> None:
    [out] = to_openai_tools([_tool()])

    assert out["type"] == "function"
    assert out["function"]["name"] == "read_file"
    assert out["function"]["parameters"] == _tool().parameters


def test_no_tools_is_an_empty_list_not_a_crash() -> None:
    assert to_openai_tools([]) == []


# -------------------------------------------------------- stop reasons


def test_finish_reasons_normalise_to_the_same_three_values() -> None:
    assert normalise_finish_reason("stop", has_tool_calls=False) == "stop"
    assert normalise_finish_reason("length", has_tool_calls=False) == "length"
    assert normalise_finish_reason("tool_calls", has_tool_calls=True) == "toolUse"


def test_content_wins_over_the_reported_reason() -> None:
    """The Tier 1 lesson, applied to the second adapter.

    A response carrying tool calls is a tool-use turn whatever the vendor
    labelled it - the same rule the loop uses for its stop condition.
    """
    assert normalise_finish_reason("stop", has_tool_calls=True) == "toolUse"


def test_a_filtered_or_unknown_reason_is_not_a_clean_finish() -> None:
    """**This test used to assert the bug.** It was called
    `test_an_unknown_reason_falls_back_to_stop` and pinned `content_filter` to
    `stop`: a reply the provider's filter cut off read as a finished answer. Never
    crashing on an unknown value is still right; calling it a finish was not. A
    missing reason is still a finish, since some servers send none."""
    for raw in ("content_filter", "a_reason_from_next_year"):
        ending = normalise_finish_reason(raw, has_tool_calls=False)
        assert isinstance(ending, adapter.AbnormalStop), f"{raw!r} read as {ending!r}"
    assert normalise_finish_reason(None, has_tool_calls=False) == "stop"


def _declared(annotation: Any) -> set[str]:
    """The string values of a `Literal[...] | None`, however it is nested."""
    if typing.get_origin(annotation) is typing.Literal:
        return {value for value in typing.get_args(annotation) if isinstance(value, str)}
    return set().union(*(_declared(arg) for arg in typing.get_args(annotation)))


def test_every_finish_reason_the_sdk_declares_is_mapped_on_purpose() -> None:
    """**The upgrade tripwire**, as for Anthropic. The chunk's `finish_reason`
    is `Literal[...] | None`, so the `Literal` is unwrapped before reading it."""
    declared = _declared(Choice.model_fields["finish_reason"].annotation)

    assert declared, "finish_reason is a Literal; reading it found nothing"
    assert declared <= set(adapter.FINISH_REASONS), declared - set(adapter.FINISH_REASONS)


async def _ending(stop_reason: str | None) -> Any:
    client = stub_openai.StubClient(stop_reason=stop_reason)
    provider = OpenAIProvider(client=client, retry=RetryPolicy(attempts=1))  # type: ignore[arg-type]
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
        ("content_filter", "content filter"),
        ("a_reason_from_next_year", "a_reason_from_next_year"),
    ],
)
async def test_an_abnormal_finish_is_an_error_that_keeps_the_reply(raw: str, said: str) -> None:
    """**Measured before the fix:** both came back as `done` with `stop`. The
    stream now ends as every other failure does, with an `error` that still
    holds the text that arrived and says what happened.

    The cost is deliberate. An OpenAI-compatible server that sends a value the
    SDK does not declare now shows an error after its answer instead of passing
    silently. Visible beats silent: the answer is still there to read."""
    ending = await _ending(raw)

    assert ending.type == "error"
    assert ending.error.stop_reason == "error"
    assert said in (ending.error.error_message or "")
    assert ending.error.text == "hi", "the reply so far is kept"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [("stop", "stop"), (None, "stop"), ("length", "length"), ("function_call", "toolUse")],
)
async def test_a_normal_finish_is_still_a_finish(raw: str | None, reason: str) -> None:
    ending = await _ending(raw)

    assert ending.type == "done"
    assert ending.reason == reason
