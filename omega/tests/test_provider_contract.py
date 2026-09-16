"""The exam: one suite, both adapters, identical assertions.

Tier 1 claimed the core owns the provider interface and adapters conform to it.
This is where that stops being a claim. Every test below is parametrized over
**both** adapters and asserts the same thing, because the promises in
`events.py` and `04-boundaries-and-layout.md` §3 are promises the *interface*
makes — not ones a particular vendor happens to keep.

If a promise held for Anthropic and not for OpenAI, the interface was describing
Anthropic. The point of running them together is that there is nowhere to hide.

The four promises, from boundaries-and-layout.md:97-104:

1. exactly one `start`, exactly one ending
2. errors arrive as `error` **events**, never as raised exceptions
3. retries happen below this line and are invisible above it
4. vendor stop reasons are normalised to `stop` / `length` / `toolUse`
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import stub_anthropic
import stub_openai
from omega_agent.provider import ModelProvider
from omega_agent.tools import Tool
from omega_agent.types import (
    AgentMessage,
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from omega_ai.anthropic import AnthropicProvider
from omega_ai.openai import OpenAIProvider
from omega_ai.retry import RetryPolicy

_FAST = RetryPolicy(attempts=3, base_delay=0.0)


def _anthropic(**kwargs: Any) -> tuple[ModelProvider, Any]:
    client = stub_anthropic.StubClient(**kwargs)
    return AnthropicProvider(client=client, retry=_FAST), client  # type: ignore[arg-type]


def _openai(**kwargs: Any) -> tuple[ModelProvider, Any]:
    client = stub_openai.StubClient(**kwargs)
    return OpenAIProvider(client=client, retry=_FAST), client  # type: ignore[arg-type]


Adapter = Callable[..., tuple[ModelProvider, Any]]

ADAPTERS = [pytest.param(_anthropic, id="anthropic"), pytest.param(_openai, id="openai")]

#: A transcript exercising every message kind, including the one the two vendors
#: disagree about most: a tool result.
TRANSCRIPT: list[AgentMessage] = [
    UserMessage(content="read a.txt"),
    AssistantMessage(
        model="m",
        stop_reason="toolUse",
        content=[ToolCall(id="call_1", name="ok", arguments={"path": "a.txt"})],
    ),
    ToolResultMessage(tool_call_id="call_1", tool_name="ok", content="file contents"),
]

TOOLS = [Tool(name="ok", description="does a thing", parameters={"type": "object"}, execute=None)]  # type: ignore[arg-type]


async def _drain(provider: ModelProvider, **kwargs: Any) -> list[Any]:
    return [
        event
        async for event in provider.stream_response(
            model="m", system="be helpful", messages=list(TRANSCRIPT), tools=TOOLS, **kwargs
        )
    ]


# ------------------------------------------- promise 1: one start, one ending


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_exactly_one_start_and_one_ending(adapter: Adapter) -> None:
    provider, _client = adapter()
    events = await _drain(provider)

    assert [e.type for e in events].count("start") == 1
    assert events[0].type == "start"
    assert len([e for e in events if e.type in {"done", "error"}]) == 1
    assert events[-1].type in {"done", "error"}


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_text_streams_as_start_deltas_end(adapter: Adapter) -> None:
    provider, _client = adapter()
    events = await _drain(provider)

    kinds = [e.type for e in events]
    assert kinds.count("text_start") == 1
    assert kinds.count("text_end") == 1
    assert kinds.index("text_start") < kinds.index("text_delta")
    assert kinds.index("text_delta") < kinds.index("text_end")

    end = next(e for e in events if e.type == "text_end")
    assert end.content == "hi"


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_every_streaming_event_carries_the_whole_message_so_far(
    adapter: Adapter,
) -> None:
    """`partial` is what lets a renderer be stateless."""
    provider, _client = adapter()
    events = await _drain(provider)

    for event in events:
        if event.type not in {"done", "error"}:
            assert event.partial is not None


# ------------------------------------------------ promise 4: normalised stops


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_a_plain_answer_normalises_to_stop(adapter: Adapter) -> None:
    provider, _client = adapter()
    events = await _drain(provider)

    assert events[-1].type == "done"
    assert events[-1].reason == "stop"
    assert events[-1].message.stop_reason == "stop"


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_a_tool_request_normalises_to_tooluse(adapter: Adapter) -> None:
    """Two vendors, two spellings - `tool_use` and `tool_calls` - one value here."""
    provider, _client = adapter(script="tool")
    events = await _drain(provider)

    assert events[-1].reason == "toolUse"
    assert events[-1].message.stop_reason == "toolUse"


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_tool_arguments_are_reassembled_from_fragments(adapter: Adapter) -> None:
    """Both formats cut the JSON at arbitrary points. Both must come back whole."""
    provider, _client = adapter(script="tool")
    events = await _drain(provider)

    end = next(e for e in events if e.type == "toolcall_end")
    assert end.tool_call.name == "ok"
    assert end.tool_call.arguments == {"path": "a.txt"}
    assert end.tool_call.id == "call_1"


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_a_tool_turn_emits_the_toolcall_events_in_order(adapter: Adapter) -> None:
    provider, _client = adapter(script="tool")
    kinds = [e.type for e in await _drain(provider)]

    assert kinds.count("toolcall_start") == 1
    assert kinds.count("toolcall_end") == 1
    assert kinds.index("toolcall_start") < kinds.index("toolcall_end")


# ----------------------------------------------- promise 2: errors as events


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_a_failure_is_an_event_not_an_exception(adapter: Adapter) -> None:
    class Fatal(Exception):
        status_code = 400

    provider, _client = adapter(fail_times=99, error=Fatal)
    events = await _drain(provider)

    assert [e.type for e in events] == ["error"]
    assert events[0].error.stop_reason == "error"
    assert events[0].error.error_message


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_cancellation_is_a_single_aborted_ending(adapter: Adapter) -> None:
    class Cancelled:
        def is_cancelled(self) -> bool:
            return True

    provider, _client = adapter()
    events = await _drain(provider, signal=Cancelled())

    assert events[-1].type == "error"
    assert events[-1].reason == "aborted"
    assert events[-1].error.stop_reason == "aborted"
    assert len([e for e in events if e.type in {"done", "error"}]) == 1


# ----------------------------------------- promise 3: retries stay invisible


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_a_retried_request_looks_like_one_request(adapter: Adapter) -> None:
    provider, client = adapter(fail_times=1, error=lambda: ConnectionError("reset"))
    events = await _drain(provider)

    assert client.attempts == 2
    kinds = [e.type for e in events]
    assert kinds.count("start") == 1, "a retry leaked an extra start"
    assert kinds.count("done") == 1
    assert "error" not in kinds


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_a_failure_after_output_is_not_retried(adapter: Adapter) -> None:
    """Restarting a stream that already emitted would duplicate its tokens."""
    provider, client = adapter(
        fail_midstream_after=2, error=lambda: ConnectionError("mid-flight")
    )
    events = await _drain(provider)

    assert client.attempts == 1
    assert events[-1].type == "error"
    assert [e.type for e in events].count("start") == 1


# ------------------------------------------------------------- usage & auth


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_token_usage_reaches_the_final_message(adapter: Adapter) -> None:
    provider, _client = adapter()
    events = await _drain(provider)

    assert events[-1].message.usage.input == 11
    assert events[-1].message.usage.output == 2


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_the_auth_resolver_is_called_per_request(adapter: Adapter) -> None:
    """Same seam, same shape, both adapters."""
    client_holder: list[Any] = []

    async def resolve() -> str:
        return f"key-{len(client_holder)}"

    if adapter is _anthropic:
        client = stub_anthropic.StubClient()
        provider: ModelProvider = AnthropicProvider(
            client=client, auth=resolve, retry=_FAST  # type: ignore[arg-type]
        )
    else:
        client = stub_openai.StubClient()
        provider = OpenAIProvider(client=client, auth=resolve, retry=_FAST)  # type: ignore[arg-type]

    await _drain(provider)
    assert client.api_key == "key-0"


# ------------------------------------------------- the transcript round trip


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_every_message_kind_is_accepted(adapter: Adapter) -> None:
    """The transcript includes a tool result - the thing the two vendors
    disagree about most. Neither adapter may need help from above to send it."""
    provider, client = adapter()
    await _drain(provider)

    assert client.calls, "the request was never made"


# ------------------------------------------------- the tier's done criterion


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_the_whole_application_stack_runs_on_either_provider(
    adapter: Adapter, tmp_path: Any
) -> None:
    """Stage 4's stated done criterion: *a second provider runs unchanged
    application code.*

    Not just the adapter in isolation - the headless driver, the harness, the
    real filesystem tools, the approval gate and the redaction hook, all of it,
    with only the provider swapped. None of those files was touched to add the
    second one.
    """
    from omega_coding.builtin_tools import build_tools
    from omega_coding.headless import run_headless

    provider, _client = adapter(script="tool")

    result = await run_headless(
        provider=provider,
        model="m",
        system="be helpful",
        prompt="read a.txt",
        tools=build_tools(tmp_path),
        approve=True,
        max_turns=1,
    )

    # The stub asks for a tool named `ok`, which these tools do not provide - so
    # the interesting assertion is that the *machinery* ran identically: the call
    # was dispatched, refused by name, and reported back as a tool result.
    assert result.tool_names == ["ok"]
    assert any(isinstance(m, ToolResultMessage) for m in result.messages)
    failure = next(m for m in result.messages if isinstance(m, ToolResultMessage))
    assert failure.is_error is True
    assert "not found" in failure.text


async def test_the_openai_adapter_closes_the_stream_it_opened() -> None:
    """The traceback that printed itself over a real answer.

    The adapter iterated the stream and never closed it, so httpcore was closed
    by the garbage collector at an arbitrary moment — throwing GeneratorExit into
    a suspended generator and producing a `generator didn't stop after athrow()`
    traceback in the middle of the model's reply.

    `anthropic.py` already used `async with` on its stream. This pins the same
    guarantee on the OpenAI side, on the ordinary path and on the abandoned one.
    """
    from omega_agent.cancellation import CancelSignal

    client = stub_openai.StubClient()
    provider = OpenAIProvider(client=client, api_key="k")

    async for _ in provider.stream_response(
        model="m", system="s", messages=[UserMessage(content="hi")], tools=[], signal=None
    ):
        pass

    assert client.last_stream is not None
    assert client.last_stream.closed, "a fully consumed stream must still be closed"

    # And when the consumer walks away early, which is the case that actually bit.
    signal = CancelSignal()
    signal.cancel()
    async for _ in provider.stream_response(
        model="m", system="s", messages=[UserMessage(content="hi")], tools=[], signal=signal
    ):
        pass

    assert client.last_stream.closed, "an abandoned stream must be closed too"
