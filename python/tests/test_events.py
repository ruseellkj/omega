"""The neutral vocabulary itself."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from omega.events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    TextDeltaEvent,
)
from omega.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage


def test_assistant_message_reports_its_tool_calls() -> None:
    message = AssistantMessage(
        content=[
            TextContent(text="one moment"),
            ToolCall(id="a", name="read_file", arguments={"path": "x"}),
        ]
    )
    assert [c.name for c in message.tool_calls] == ["read_file"]
    assert message.text == "one moment", "text must exclude tool calls"


def test_message_with_no_tool_calls_is_the_stop_signal() -> None:
    assert AssistantMessage(content=[TextContent(text="done")]).tool_calls == []


def test_streaming_events_carry_the_whole_message_so_far() -> None:
    """`partial` is what lets a consumer render without tracking state itself."""
    partial = AssistantMessage(content=[TextContent(text="hel")])
    event = TextDeltaEvent(content_index=0, delta="l", partial=partial)
    assert event.partial.text == "hel"


def test_unexpected_fields_are_rejected_at_the_boundary() -> None:
    """A provider inventing a field should fail loudly, not leak vendor data up."""
    with pytest.raises(ValidationError):
        AssistantStartEvent(partial=AssistantMessage(), vendor_extra="nope")  # type: ignore[call-arg]


def test_done_reason_is_restricted_to_the_three_canonical_values() -> None:
    AssistantDoneEvent(reason="toolUse", message=AssistantMessage())
    with pytest.raises(ValidationError):
        AssistantDoneEvent(reason="end_turn", message=AssistantMessage())  # type: ignore[arg-type]


def test_tool_result_accepts_a_bare_string_and_stores_the_list_shape() -> None:
    result = ToolResultMessage(tool_call_id="a", tool_name="t", content="hello")
    assert result.text == "hello"
    assert isinstance(result.content[0], TextContent)


def test_error_events_still_carry_a_message() -> None:
    """Failures are data: the partial work survives alongside the failure."""
    partial = AssistantMessage(content=[TextContent(text="got this far")], stop_reason="error")
    event = AssistantErrorEvent(reason="error", error=partial)
    assert event.error.text == "got this far"
