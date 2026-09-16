"""A stand-in for the OpenAI SDK client.

Same job as `stub_anthropic.py`, same deliberate hand-rolling: writing out the
shape documents exactly which parts of the SDK surface the adapter depends on.

The interesting difference from the Anthropic stub is what it has to model, and
that difference *is* the exam. Tool call arguments arrive as string fragments
keyed by their own `index`, unrelated to position in the content list; there are
no per-block stop events; and the final chunk carries usage with no choices at
all, which is the shape most likely to crash a careless adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any


def _chunk(
    *,
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    finish_reason: str | None = None,
    usage: Any = None,
) -> Any:
    return SimpleNamespace(
        usage=usage,
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                delta=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ],
    )


def _usage_only_chunk() -> Any:
    """A final chunk with usage and **no choices**. Legal, and easy to crash on."""
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=2), choices=[]
    )


def _fragment(index: int, *, call_id: str | None = None, name: str | None = None,
              arguments: str | None = None) -> Any:
    return SimpleNamespace(
        index=index,
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _text_path() -> list[Any]:
    return [
        _chunk(content="h"),
        _chunk(content="i"),
        _chunk(finish_reason="stop"),
        _usage_only_chunk(),
    ]


def _tool_path() -> list[Any]:
    """Arguments split mid-JSON across fragments, as they really arrive."""
    return [
        _chunk(tool_calls=[_fragment(0, call_id="call_1", name="ok", arguments='{"path": ')]),
        _chunk(tool_calls=[_fragment(0, arguments='"a.txt"}')]),
        _chunk(finish_reason="tool_calls"),
        _usage_only_chunk(),
    ]


class _Stream:
    """Stands in for `openai.AsyncStream`.

    **It is an async context manager because the real one is.** A stub that
    models less than the thing it replaces lets bugs through silently: the
    adapter consumed this stream without ever closing it, the real SDK left that
    to the garbage collector, and the result was an httpcore traceback printed
    over the model's answer — invisible to a suite whose stub did not care
    whether it was closed.
    """

    def __init__(self, events: list[Any], fail_after: int | None, error: Callable[[], Exception]):
        self._events = events
        self._fail_after = fail_after
        self._error = error
        #: Asserted on, so "the adapter closes what it opens" becomes a tested
        #: claim rather than an intention.
        self.closed = False

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    async def close(self) -> None:
        self.closed = True

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


class _Completions:
    def __init__(self, client: StubClient) -> None:
        self._client = client

    async def create(self, **kwargs: Any) -> _Stream:
        client = self._client
        client.attempts += 1
        client.calls.append(kwargs)

        if client.attempts <= client.fail_times:
            # Fails on the request itself, before anything is emitted, so a retry
            # is still safe.
            raise client.error()

        events = _tool_path() if client.script == "tool" else _text_path()
        stream = _Stream(events, fail_after=client.fail_midstream_after, error=client.error)
        # Kept so a test can assert the adapter closed what it was handed.
        client.last_stream = stream
        return stream


class StubClient:
    """Mirrors stub_anthropic.StubClient's knobs so one contract suite drives both."""

    #: The most recent `_Stream` handed to the adapter, for close assertions.
    last_stream: _Stream | None = None

    def __init__(
        self,
        *,
        fail_times: int = 0,
        fail_midstream_after: int | None = None,
        error: Callable[[], Exception] = lambda: ConnectionError("stub"),
        script: str = "text",
    ) -> None:
        self.script = script
        self.fail_times = fail_times
        self.fail_midstream_after = fail_midstream_after
        self.error = error

        self.attempts = 0
        self.calls: list[dict[str, Any]] = []
        self.api_key: str | None = None
        self.chat = SimpleNamespace(completions=_Completions(self))
