"""A provider that replays scripted events instead of calling anything.

Written *before* the real adapter, for two reasons:

1. **It proves the interface is small enough.** A contract you cannot fake in a
   page of code is too large. This is a design check disguised as a test double.
2. **It makes everything above it testable with no network, no API key, and no
   credits** — which is the only reason Tier 1 is verifiable end to end right now.

It is also a runnable mode, not just a fixture: `omega --fake` drives the whole
REPL through it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from omega.events import (
    AssistantDoneEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from omega.tools import Tool
from omega.types import (
    AgentMessage,
    AssistantMessage,
    CancellationToken,
    StopReason,
    TextContent,
    ToolCall,
)


@dataclass(frozen=True, slots=True)
class FakeCall:
    """One recorded invocation, for assertions in tests."""

    model: str
    system: str
    messages: list[AgentMessage]
    tools: list[Tool]


class FakeProvider:
    """Replays one scripted event list per call, in order.

    Satisfies `ModelProvider` structurally — note it inherits from nothing.
    """

    def __init__(self, streams: Iterable[Sequence[AssistantMessageEvent]] = ()) -> None:
        self.streams: list[list[AssistantMessageEvent]] = [list(s) for s in streams]
        self.calls: list[FakeCall] = []

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[Tool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        self.calls.append(
            FakeCall(model=model, system=system, messages=list(messages), tools=list(tools))
        )
        stream = self.streams.pop(0) if self.streams else []

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            for event in stream:
                if signal is not None and signal.is_cancelled():
                    return
                yield event

        return iterator()


# --------------------------------------------------------------------------
# Script builders. Hand-writing twelve-event sequences in every test is noise;
# these produce well-formed streams that obey the invariants in events.py.
# --------------------------------------------------------------------------


def text_turn(text: str, *, model: str = "fake-model") -> list[AssistantMessageEvent]:
    """A turn that says something and asks for nothing. The loop should stop."""
    partial = AssistantMessage(model=model)
    events: list[AssistantMessageEvent] = [
        AssistantStartEvent(partial=partial.model_copy(deep=True))
    ]

    partial.content.append(TextContent(text=""))
    events.append(TextStartEvent(content_index=0, partial=partial.model_copy(deep=True)))

    block = partial.content[0]
    assert isinstance(block, TextContent)
    block.text = text
    events.append(
        TextDeltaEvent(content_index=0, delta=text, partial=partial.model_copy(deep=True))
    )
    events.append(
        TextEndEvent(content_index=0, content=text, partial=partial.model_copy(deep=True))
    )

    final = partial.model_copy(deep=True)
    final.stop_reason = "stop"
    events.append(AssistantDoneEvent(reason="stop", message=final))
    return events


def tool_turn(
    name: str,
    arguments: dict[str, Any],
    *,
    call_id: str = "call_1",
    text: str = "",
    model: str = "fake-model",
    stop_reason: str = "toolUse",
) -> list[AssistantMessageEvent]:
    """A turn that requests a tool. The loop should run it and continue.

    `stop_reason` is settable so a test can script the awkward case: a response
    whose stop reason says "stop" while its content still carries a tool call.
    The loop must follow the content.
    """
    partial = AssistantMessage(model=model)
    events: list[AssistantMessageEvent] = [
        AssistantStartEvent(partial=partial.model_copy(deep=True))
    ]
    index = 0

    if text:
        partial.content.append(TextContent(text=text))
        events.append(TextStartEvent(content_index=index, partial=partial.model_copy(deep=True)))
        events.append(
            TextDeltaEvent(content_index=index, delta=text, partial=partial.model_copy(deep=True))
        )
        events.append(
            TextEndEvent(content_index=index, content=text, partial=partial.model_copy(deep=True))
        )
        index += 1

    call = ToolCall(id=call_id, name=name, arguments=arguments)
    partial.content.append(call)
    events.append(ToolCallStartEvent(content_index=index, partial=partial.model_copy(deep=True)))
    events.append(
        ToolCallEndEvent(
            content_index=index, tool_call=call, partial=partial.model_copy(deep=True)
        )
    )

    final = partial.model_copy(deep=True)
    final.stop_reason = cast(StopReason, stop_reason)
    events.append(AssistantDoneEvent(reason="toolUse", message=final))
    return events
