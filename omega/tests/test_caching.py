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
from omega_agent.types import AssistantMessage, TextContent, UserMessage
from omega_ai.anthropic import AnthropicProvider, _cacheable_system


def _tool(name: str = "read_file") -> Tool:
    async def run(**_: object) -> str:
        return "ok"

    return Tool(
        name=name,
        description=f"The {name} tool",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        execute=run,  # type: ignore[arg-type]
    )


def _is_marked(message: dict[str, Any]) -> bool:
    """Does this message carry a cache breakpoint on its final block?"""
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return False
    return isinstance(content[-1], dict) and "cache_control" in content[-1]


async def _run(
    client: Any,
    *,
    messages: list[Any] | None = None,
    tools: list[Tool] | None = None,
) -> list[Any]:
    provider = AnthropicProvider(client=client, api_key="k")
    return [
        event
        async for event in provider.stream_response(
            model="claude-sonnet-5",
            system="be helpful",
            messages=messages if messages is not None else [UserMessage(content="hi")],
            tools=tools if tools is not None else [_tool()],
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


async def test_the_last_tool_carries_a_marker_and_the_others_do_not() -> None:
    """One marker caches every tool.

    A breakpoint hashes the whole prefix up to and including its own block, so
    marking the final tool covers all of them. Marking each would spend the
    entire budget of four on prefixes that the next marker already covers.
    """
    client = stub_anthropic.StubClient()
    await _run(client, tools=[_tool("read_file"), _tool("write_file"), _tool("run_shell")])

    tools = client.calls[0]["tools"]
    assert "cache_control" not in tools[0]
    assert "cache_control" not in tools[1]
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}


async def test_the_conversation_tail_is_marked() -> None:
    """**The breakpoint that actually saves money.**

    The system prompt and schemas are a few hundred fixed tokens. The
    conversation is everything else, it grows every turn, and it is re-sent in
    full each time. omega's first pass at caching marked only the static prefix,
    which is why it saved almost nothing.
    """
    client = stub_anthropic.StubClient()
    await _run(client)

    last = client.calls[0]["messages"][-1]
    assert last["content"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_the_previous_request_boundary_is_marked_too() -> None:
    """Two windows, because Anthropic's lookback is finite.

    The search for a reusable prefix checks at most **20 block positions** back
    from a breakpoint and then stops. A long turn can push the previous write
    out of that window, and the next request re-pays for the whole conversation.
    Marking where the last request ended opens a second window at a position
    already known to hold an entry.
    """
    client = stub_anthropic.StubClient()
    await _run(
        client,
        messages=[
            UserMessage(content="first question"),
            AssistantMessage(model="m", stop_reason="stop", content=[TextContent(text="answer")]),
            UserMessage(content="second question"),
        ],
    )

    sent = client.calls[0]["messages"]
    marked = [i for i, m in enumerate(sent) if _is_marked(m)]

    assert len(marked) == 2, "this tail and the previous one"
    assert marked[-1] == len(sent) - 1, "the newest is always one of them"


async def test_the_budget_of_four_is_never_exceeded() -> None:
    """Anthropic rejects a fifth breakpoint. The split is 1 system + 1 tools + 2
    messages, and a long transcript must not drift past it."""
    client = stub_anthropic.StubClient()
    history: list[Any] = []
    for i in range(8):
        history.append(UserMessage(content=f"question {i}"))
        history.append(
            AssistantMessage(
                model="m", stop_reason="stop", content=[TextContent(text=f"answer {i}")]
            )
        )
    await _run(client, messages=history)

    call = client.calls[0]
    total = (
        sum("cache_control" in block for block in call["system"])
        + sum("cache_control" in tool for tool in call["tools"])
        + sum(_is_marked(m) for m in call["messages"])
    )
    assert total <= 4, f"spent {total} of Anthropic's four breakpoints"


async def test_a_transcript_with_no_assistant_turn_marks_only_the_tail() -> None:
    """The first request of a session has no previous boundary to mark, so it
    emits one marker rather than inventing a second."""
    client = stub_anthropic.StubClient()
    await _run(client, messages=[UserMessage(content="the very first question")])

    marked = [m for m in client.calls[0]["messages"] if _is_marked(m)]
    assert len(marked) == 1


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
