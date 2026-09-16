"""A stand-in for the Anthropic SDK client, for testing the adapter itself.

`providers/fake.py` fakes the provider *interface*, which is what almost every
test wants. This fakes the layer *below* the adapter — the vendor client — which
is the only way to test the adapter's own behaviour: its retry loop, its auth
resolution, and its promise to emit exactly one terminal event.

Deliberately hand-rolled rather than a mocking library. The shape it has to
present is small and specific, and writing it out documents exactly which parts
of the SDK surface the adapter actually depends on.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any


def _tool_path() -> list[Any]:
    """A stream that requests a tool, with the arguments split mid-JSON."""
    return [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(usage=SimpleNamespace(input_tokens=11)),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="tool_use", id="call_1", name="ok"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"path": '),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json='"a.txt"}'),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="tool_use"),
            usage=SimpleNamespace(output_tokens=7),
        ),
    ]


def _happy_path(cache_read: int = 0, cache_write: int = 0) -> list[Any]:
    """The minimum well-formed stream: a message that says "hi" and stops.

    The cache counters are attached only when non-zero, so the default stream
    keeps the shape of an API that never heard of prompt caching — which is also
    the shape Ollama, vLLM and Groq present through the OpenAI adapter. The
    adapter has to survive both.
    """
    usage: Any = SimpleNamespace(input_tokens=11)
    if cache_read or cache_write:
        usage.cache_read_input_tokens = cache_read
        usage.cache_creation_input_tokens = cache_write
    return [
        SimpleNamespace(type="message_start", message=SimpleNamespace(usage=usage)),
        SimpleNamespace(
            type="content_block_start", index=0, content_block=SimpleNamespace(type="text")
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="hi"),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=2),
        ),
    ]


class _Stream:
    def __init__(self, events: list[Any], fail_after: int | None, error: Callable[[], Exception]):
        self._events = events
        self._fail_after = fail_after
        self._error = error

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    def __aiter__(self) -> _Stream:
        self._iter: Iterator[Any] = iter(self._events)
        self._served = 0
        return self

    async def __anext__(self) -> Any:
        if self._fail_after is not None and self._served >= self._fail_after:
            raise self._error()
        try:
            event = next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None
        self._served += 1
        return event


class _Messages:
    def __init__(self, client: StubClient) -> None:
        self._client = client

    def stream(self, **kwargs: Any) -> _Stream:
        client = self._client
        client.attempts += 1
        client.calls.append(kwargs)

        if client.attempts <= client.fail_times:
            # Fails on connect, before anything is emitted - so a retry is safe.
            return _Stream([], fail_after=0, error=client.error)

        events = (
            _tool_path()
            if client.script == "tool"
            else _happy_path(client.cache_read, client.cache_write)
        )
        return _Stream(events, fail_after=client.fail_midstream_after, error=client.error)


class StubClient:
    """Counts attempts and can be told to fail on connect or mid-stream.

    The distinction matters: failing before any event has been emitted is
    retryable, and failing after is not.
    """

    def __init__(
        self,
        *,
        fail_times: int = 0,
        fail_midstream_after: int | None = None,
        error: Callable[[], Exception] = lambda: ConnectionError("stub"),
        script: str = "text",
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> None:
        self.script = script
        self.cache_read = cache_read
        self.cache_write = cache_write
        self.fail_times = fail_times
        self.fail_midstream_after = fail_midstream_after
        self.error = error

        self.attempts = 0
        self.calls: list[dict[str, Any]] = []
        #: The adapter assigns this when an auth resolver is configured.
        self.api_key: str | None = None
        self.messages = _Messages(self)
