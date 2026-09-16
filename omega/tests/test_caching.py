"""Prompt caching — beginner failure #9, "it costs more than it should".

Every turn re-sends the system prompt and the whole tool schema block, and pays
full price for both. A cache marker tells the provider to bill the repeat at a
fraction.

**What these tests can and cannot prove.** They prove the marker reaches the
wire and that a reported saving survives to the final message. They cannot prove
a cache *hit*: that needs a real provider, two turns inside the ephemeral
lifetime, and a `cache_read_input_tokens` above zero coming back. Until that is
run, #9 is "markers sent", not "closed" — recorded that way in `TIER-3.md`.
"""

from __future__ import annotations

from typing import Any

import stub_anthropic
from omega_agent.events import AssistantDoneEvent
from omega_agent.tools import Tool
from omega_agent.types import UserMessage
from omega_ai.anthropic import AnthropicProvider, _cacheable_system


def _tool() -> Tool:
    async def run(**_: object) -> str:
        return "ok"

    return Tool(
        name="read_file",
        description="Read a file",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        execute=run,  # type: ignore[arg-type]
    )


async def _run(client: Any) -> list[Any]:
    provider = AnthropicProvider(client=client, api_key="k")
    return [
        event
        async for event in provider.stream_response(
            model="claude-sonnet-5",
            system="be helpful",
            messages=[UserMessage(content="hi")],
            tools=[_tool()],
        )
    ]


# ------------------------------------------------------------- the request side


def test_the_system_prompt_carries_a_cache_marker() -> None:
    """A bare string cannot carry `cache_control`, so the prompt becomes a block."""
    blocks = _cacheable_system("be helpful")

    assert blocks == [
        {"type": "text", "text": "be helpful", "cache_control": {"type": "ephemeral"}}
    ]


def test_the_prompt_text_is_unchanged_by_the_envelope() -> None:
    """Only the wrapper moves. If the text changed, every cached prefix would
    miss — and the whole mechanism depends on it being byte-identical."""
    prompt = "line one\nline two\n"

    assert _cacheable_system(prompt)[0]["text"] == prompt


async def test_the_marker_actually_reaches_the_wire() -> None:
    """The test that would catch the marker being built and never sent."""
    client = stub_anthropic.StubClient()
    await _run(client)

    sent = client.calls[0]["system"]
    assert sent[0]["cache_control"] == {"type": "ephemeral"}


async def test_only_one_breakpoint_is_spent() -> None:
    """Anthropic caps breakpoints at four and bills every write above normal
    input. The hierarchy is tools -> system -> messages, so marking `system`
    already covers the schemas in front of it; a second marker would pay twice
    to cache the same bytes.
    """
    client = stub_anthropic.StubClient()
    await _run(client)

    call = client.calls[0]
    assert not any("cache_control" in tool for tool in call["tools"]), "tools inherit"
    assert not any("cache_control" in str(m) for m in call["messages"]), "messages change"


# -------------------------------------------------------------- the reply side


async def test_cache_usage_survives_to_the_final_message() -> None:
    """**The test that catches the real bug.**

    `message_delta` used to rebuild `Usage` from scratch to attach the output
    count, which silently reset anything the constructor did not name. The cache
    figures were recorded correctly at `message_start` and were gone by the end
    of the stream, so the start event reported a saving and the final message —
    the one the cost tracker and the transcript both read — reported none.

    Asserting on the final message rather than the start event is the entire
    point of this test.
    """
    client = stub_anthropic.StubClient(cache_read=900, cache_write=64)
    events = await _run(client)

    done = [e for e in events if isinstance(e, AssistantDoneEvent)]
    assert done, "the stream has to finish"
    usage = done[-1].message.usage

    assert usage.cache_read == 900
    assert usage.cache_write == 64
    assert usage.output == 2, "and the output count still arrives"


async def test_a_provider_that_reports_no_cache_fields_is_fine() -> None:
    """The default stub has no cache attributes at all — the shape of an older
    API, and of Ollama, vLLM and Groq through the OpenAI adapter. Missing must
    read as zero rather than raise."""
    events = await _run(stub_anthropic.StubClient())

    usage = [e for e in events if isinstance(e, AssistantDoneEvent)][-1].message.usage
    assert usage.cache_read == 0
    assert usage.cache_write == 0
    assert usage.input == 11, "ordinary accounting is untouched"
