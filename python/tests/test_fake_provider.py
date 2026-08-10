"""The fake provider - the thing that makes everything else testable offline."""

from __future__ import annotations

from omega.provider import ModelProvider
from omega.providers.fake import FakeProvider, text_turn, tool_turn
from omega.types import UserMessage


def test_fake_satisfies_the_protocol_without_inheriting_anything() -> None:
    """Structural typing: an adapter needs no base class to be an adapter."""
    provider: ModelProvider = FakeProvider()
    assert provider is not None
    assert FakeProvider.__mro__[1:] == (object,), "FakeProvider should inherit from nothing"


async def test_replays_one_scripted_stream_per_call() -> None:
    provider = FakeProvider([text_turn("first"), text_turn("second")])

    async def collect() -> list[str]:
        return [
            e.type
            async for e in provider.stream_response(model="m", system="s", messages=[], tools=[])
        ]

    assert await collect() == ["start", "text_start", "text_delta", "text_end", "done"]
    assert await collect() == ["start", "text_start", "text_delta", "text_end", "done"]
    assert await collect() == [], "running out of script yields an empty stream"


async def test_records_what_it_was_called_with() -> None:
    provider = FakeProvider([text_turn("x")])
    messages = [UserMessage(content="hello")]

    async for _ in provider.stream_response(
        model="the-model", system="the-system", messages=messages, tools=[]
    ):
        pass

    assert len(provider.calls) == 1
    assert provider.calls[0].model == "the-model"
    assert provider.calls[0].system == "the-system"
    assert len(provider.calls[0].messages) == 1


async def test_honours_a_cancellation_signal() -> None:
    class Cancelled:
        def is_cancelled(self) -> bool:
            return True

    provider = FakeProvider([text_turn("never seen")])
    events = [
        e
        async for e in provider.stream_response(
            model="m", system="s", messages=[], tools=[], signal=Cancelled()
        )
    ]
    assert events == []


def test_scripted_streams_obey_the_event_contract() -> None:
    """Exactly one start, exactly one terminal - the invariants from events.py."""
    for stream in (text_turn("hi"), tool_turn("t", {"a": 1})):
        types = [e.type for e in stream]
        assert types.count("start") == 1
        assert sum(t in ("done", "error") for t in types) == 1
        assert types[0] == "start"
        assert types[-1] in ("done", "error")


def test_tool_turn_can_script_the_awkward_stop_reason_case() -> None:
    final = tool_turn("t", {}, stop_reason="stop")[-1].message
    assert final.stop_reason == "stop"
    assert len(final.tool_calls) == 1, "content and stop_reason deliberately disagree"
