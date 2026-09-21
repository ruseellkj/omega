"""The ChatGPT subscription adapter, driven from recorded SSE frames.

**The point of this file is that the adapter is testable at all.** It talks to a
host no test may reach, over a format no SDK models, using a credential that only
exists after a browser sign-in. If none of that could be faked, the adapter would
be ~600 lines nobody could check, which is a worse outcome than the "not offered"
it replaced.

Two seams make it possible, and both were designed for it:

* `sse_objects` takes an iterator of **lines**, not a response — so the whole
  event vocabulary can be driven from a list of strings.
* `CodexProvider` accepts a `client`, so `httpx.MockTransport` can serve a
  recorded stream through the real request path, real headers and all.

The frames below are shaped from the event names Tau handles
(`research/tau/src/tau_ai/openai_codex.py`), not invented.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from omega_agent.tools import Tool
from omega_agent.types import (
    AssistantMessage,
    ResultBlock,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from omega_ai import openai_codex
from omega_ai.openai_codex import (
    CodexProvider,
    build_headers,
    build_payload,
    to_codex_input,
    to_codex_tools,
)


async def _never(_args: dict[str, Any], _signal: Any) -> Any:  # pragma: no cover
    raise AssertionError("tests never run a tool")


def _tool(name: str, schema: dict[str, Any]) -> Tool:
    return Tool(name=name, description="read a file", parameters=schema, execute=_never)


def _result(call_id: str, name: str, text: str) -> ToolResultMessage:
    content: list[ResultBlock] = [TextContent(text=text)] if text else []
    return ToolResultMessage(tool_call_id=call_id, tool_name=name, content=content)


def _frames(*events: dict[str, Any]) -> bytes:
    return b"".join(f"data: {json.dumps(event)}\n\n".encode() for event in events)


def _client(body: bytes, *, status: int = 200, seen: list[httpx.Request] | None = None):
    async def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _token() -> str:
    return "sk-codex-token"


def _provider(client: httpx.AsyncClient) -> CodexProvider:
    return CodexProvider(auth=_token, account_id="acct-1", client=client)


async def _collect(provider: CodexProvider, **kwargs: Any) -> list[Any]:
    defaults: dict[str, Any] = {"model": "m", "system": "s", "messages": [], "tools": []}
    defaults.update(kwargs)
    return [event async for event in provider.stream_response(**defaults)]


# ------------------------------------------------------------- the translators


def test_the_two_tool_call_ids_survive_a_round_trip() -> None:
    """**The detail that has no equivalent in Chat Completions.**

    A Responses function call carries a `call_id` (which the result is addressed
    to) *and* an item `id`. omega's `ToolCall` has one id field, so they are
    packed together and split apart on the way back. Lose the item id and the
    next request describes a call the conversation does not contain.
    """
    packed = openai_codex._join_ids("call_abc", "fc_xyz")
    assert openai_codex._split_ids(packed) == ("call_abc", "fc_xyz")

    # A call with no item id is ordinary, not an error.
    assert openai_codex._split_ids(openai_codex._join_ids("call_only", None)) == (
        "call_only",
        None,
    )

    message = AssistantMessage(model="m", content=[ToolCall(id=packed, name="read", arguments={})])
    items = to_codex_input([message])

    assert items[0]["call_id"] == "call_abc"
    assert items[0]["id"] == "fc_xyz"


def test_reasoning_is_replayed_verbatim() -> None:
    """Multi-turn reasoning degrades if the item is not handed back unchanged.

    `ThinkingContent.signature` already existed for exactly this — "an opaque
    provider token… handed back verbatim on the next turn" — so the neutral
    model needed no new field. This asserts the round trip actually happens,
    because omitting it looks fine on turn one and degrades on turn three.
    """
    item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "OPAQUE"}
    message = AssistantMessage(
        model="m", content=[ThinkingContent(thinking="hmm", signature=json.dumps(item))]
    )

    assert to_codex_input([message])[0] == item


def test_a_signature_that_is_not_an_item_is_skipped_rather_than_sent() -> None:
    """A provider that returns something else must not corrupt the next request."""
    message = AssistantMessage(
        model="m", content=[ThinkingContent(thinking="hmm", signature="not-json")]
    )
    assert to_codex_input([message]) == []


def test_tools_are_flat_not_nested_under_function() -> None:
    """Chat Completions nests `{"type": "function", "function": {...}}`.

    The Responses API puts `name` and `parameters` at the top level, and accepts
    the nested form **silently** — the model simply never sees the tools. A
    rejection would be easier to debug than that.
    """
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    encoded = to_codex_tools([_tool("read", schema)])

    assert encoded == [
        {
            "type": "function",
            "name": "read",
            "description": "read a file",
            "parameters": schema,
            "strict": None,
        }
    ]
    assert "function" not in encoded[0] or not isinstance(encoded[0].get("function"), dict)


def test_one_assistant_message_becomes_several_items() -> None:
    """The core difference from the Chat Completions translator next door.

    There, an assistant message stays one object with a `tool_calls` array. Here
    its reasoning, its text and each call are separate entries in `input`.
    """
    message = AssistantMessage(
        model="m",
        content=[
            ThinkingContent(thinking="t", signature=json.dumps({"type": "reasoning", "id": "r"})),
            TextContent(text="let me look"),
            ToolCall(id="call_1|fc_1", name="read", arguments={"path": "a.py"}),
        ],
    )
    items = to_codex_input(
        [
            UserMessage(content="hi"),
            message,
            _result("call_1|fc_1", "read", "file contents"),
        ]
    )

    assert [item.get("type") or item.get("role") for item in items] == [
        "user",
        "reasoning",
        "message",
        "function_call",
        "function_call_output",
    ]
    assert items[-1]["call_id"] == "call_1", "the result must address the call id, not the item id"


def test_an_empty_tool_result_still_carries_text() -> None:
    """The endpoint rejects a null output, and "it produced nothing" is a real
    result the model should see rather than a request that fails."""
    items = to_codex_input([_result("call_1", "read", "")])
    assert items[0]["output"] == "(no output)"


def test_the_account_header_is_sent_beside_the_bearer() -> None:
    """**The header whose absence reads as a bad token.**

    ChatGPT rejects a request carrying the bearer alone, with a 401 that looks
    like an expired credential rather than a missing field.
    """
    headers = build_headers(access_token="TOK", account_id="acct-9")

    assert headers["Authorization"] == "Bearer TOK"
    assert headers["chatgpt-account-id"] == "acct-9"
    assert headers["OpenAI-Beta"] == "responses=experimental"
    assert headers["originator"] == "codex_cli_rs"


def test_the_system_prompt_is_instructions_not_a_message() -> None:
    payload = build_payload(model="m", system="BE HELPFUL", messages=[], tools=[])

    assert payload["instructions"] == "BE HELPFUL"
    assert payload["store"] is False, "the session file is omega's, not theirs"
    assert "reasoning.encrypted_content" in payload["include"]
    assert all(item.get("role") != "system" for item in payload["input"])


# ------------------------------------------------------------------ the stream


async def test_a_plain_answer_streams_text_and_ends_once() -> None:
    body = _frames(
        {"type": "response.output_text.delta", "delta": "Hel"},
        {"type": "response.output_text.delta", "delta": "lo"},
        {"type": "response.output_item.done", "item": {"type": "message"}},
        {
            "type": "response.completed",
            "response": {"usage": {"input_tokens": 11, "output_tokens": 3}},
        },
    )
    async with _client(body) as client:
        events = await _collect(_provider(client))

    kinds = [event.type for event in events]
    assert kinds == ["start", "text_start", "text_delta", "text_delta", "text_end", "done"]
    assert events[-1].reason == "stop"
    assert events[-1].message.content[0].text == "Hello"
    assert events[-1].message.usage.input == 11
    assert kinds.count("done") == 1, "exactly one ending"


async def test_a_tool_call_is_reassembled_from_argument_deltas() -> None:
    body = _frames(
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "read"},
        },
        {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"pa'},
        {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": 'th":"a"}'},
        {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1"},
        },
        {"type": "response.completed", "response": {}},
    )
    async with _client(body) as client:
        events = await _collect(_provider(client))

    ends = [event for event in events if event.type == "toolcall_end"]
    assert len(ends) == 1
    assert ends[0].tool_call.name == "read"
    assert ends[0].tool_call.arguments == {"path": "a"}
    assert ends[0].tool_call.id == "call_1|fc_1", "both ids kept"
    assert events[-1].reason == "toolUse"


async def test_reasoning_arrives_as_thinking_and_keeps_its_item() -> None:
    item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "OPAQUE"}
    body = _frames(
        {"type": "response.output_item.added", "item": item},
        {"type": "response.reasoning_summary_text.delta", "delta": "weigh"},
        {"type": "response.output_item.done", "item": {"type": "reasoning"}},
        {"type": "response.completed", "response": {}},
    )
    async with _client(body) as client:
        events = await _collect(_provider(client))

    assert [event.type for event in events][:3] == ["start", "thinking_start", "thinking_delta"]
    thinking = [b for b in events[-1].message.content if isinstance(b, ThinkingContent)][0]
    assert json.loads(thinking.signature) == item, "the item must survive for the next turn"


async def test_a_reported_stream_error_ends_as_one_error_event() -> None:
    body = _frames(
        {"type": "response.output_text.delta", "delta": "partial"},
        {"type": "response.failed", "response": {"error": {"message": "quota exhausted"}}},
    )
    async with _client(body) as client:
        events = await _collect(_provider(client))

    assert events[-1].type == "error"
    assert "quota exhausted" in events[-1].error.error_message
    assert "partial" in events[-1].error.content[0].text, "output before the failure is kept"


async def test_a_stream_that_just_stops_still_produces_an_ending() -> None:
    """Every stream ends exactly once, even one the provider truncated."""
    async with _client(_frames({"type": "response.output_text.delta", "delta": "x"})) as client:
        events = await _collect(_provider(client))

    assert events[-1].type == "error"
    assert "without a terminal event" in events[-1].error.error_message


async def test_a_400_is_not_retried_and_a_429_is() -> None:
    """The rule the other two adapters already enforce, restated for httpx.

    A hand-rolled request gets neither retry nor its limits for free, so both
    directions are pinned: a client error is the same answer every time, and a
    rate limit is the provider asking for a moment.
    """
    attempts: list[httpx.Request] = []
    async with _client(b"nope", status=400, seen=attempts) as client:
        await _collect(_provider(client))
    assert len(attempts) == 1, "a 400 must not be retried"

    attempts.clear()
    async with _client(b"slow down", status=429, seen=attempts) as client:
        provider = CodexProvider(
            auth=_token,
            account_id="a",
            client=client,
            retry=openai_codex.RetryPolicy(attempts=3, base_delay=0.0, max_delay=0.0),
        )
        await _collect(provider)
    assert len(attempts) == 3, "a 429 must be retried to the policy limit"


async def test_a_failure_after_output_is_never_retried() -> None:
    """Restarting a stream that already emitted would replay text the screen has
    shown and the transcript has recorded.

    **The stream has to fail mid-flight, not merely stop.** A truncated stream
    never reaches the retry branch at all, so a test built on one passes against
    code with no guard — which is exactly what happened here on the first
    attempt, and why this one raises from inside the body generator.
    """
    attempts: list[httpx.Request] = []

    async def body() -> AsyncIterator[bytes]:
        yield _frames({"type": "response.output_text.delta", "delta": "shown"})
        raise httpx.ReadError("connection lost after output")

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(200, content=body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CodexProvider(
            auth=_token,
            account_id="a",
            client=client,
            retry=openai_codex.RetryPolicy(attempts=3, base_delay=0.0, max_delay=0.0),
        )
        events = await _collect(provider)

    assert len(attempts) == 1, "a stream that had already emitted was restarted"
    assert events[-1].type == "error"
    assert "shown" in events[-1].error.content[0].text, "the emitted text is kept"


async def test_a_connection_failure_before_the_response_is_retried() -> None:
    """The other half — otherwise "never retry" passes as "never retry at all".

    **Where the line falls is worth stating**, because it is not "before any
    text". `AssistantStartEvent` is emitted once the response is established, and
    it counts toward `emitted` like any other event — so a body that dies
    mid-read is *not* retried even if no text arrived. Only a failure before the
    response exists is. Both other adapters behave identically
    (`openai.py:417-419`); this was measured here rather than assumed, after a
    first version of this test asserted the opposite and failed.
    """
    attempts: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ConnectError("no route to host")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CodexProvider(
            auth=_token,
            account_id="a",
            client=client,
            retry=openai_codex.RetryPolicy(attempts=3, base_delay=0.0, max_delay=0.0),
        )
        events = await _collect(provider)

    assert len(attempts) == 3, "a connection failure must be retried to the limit"
    assert events[-1].type == "error"


async def test_cancellation_ends_the_stream_as_aborted() -> None:
    class Cancelled:
        def is_cancelled(self) -> bool:
            return True

    body = _frames({"type": "response.output_text.delta", "delta": "x"})
    async with _client(body) as client:
        events = await _collect(_provider(client), signal=Cancelled())

    assert events[-1].type == "error"
    assert events[-1].reason == "aborted"


async def test_the_request_actually_carries_the_credential() -> None:
    """Driven through the real request path, so the headers are the real ones."""
    seen: list[httpx.Request] = []
    async with _client(_frames({"type": "response.completed", "response": {}}), seen=seen) as c:
        await _collect(_provider(c), tools=[_tool("read", {})])

    request = seen[0]
    assert str(request.url).endswith("/codex/responses")
    assert request.headers["authorization"] == "Bearer sk-codex-token"
    assert request.headers["chatgpt-account-id"] == "acct-1"
    body = json.loads(request.content)
    assert body["tools"][0]["name"] == "read"


# ------------------------------------------------------------------ SSE itself


async def test_the_sse_reader_skips_everything_that_is_not_an_event() -> None:
    async def lines() -> AsyncIterator[str]:
        for line in [
            ": keep-alive comment",
            "",
            'data: {"type": "a"}',
            "data: [DONE]",
            "data: not json at all",
            "event: ignored",
            'data: {"type": "b"}',
        ]:
            yield line

    assert [e["type"] async for e in openai_codex.sse_objects(lines())] == ["a", "b"]


def test_starting_omega_does_not_import_this_adapter() -> None:
    """**The crash this adapter caused, as an invariant.**

    Reported from a real install:

        File "omega_ai/openai_codex.py", line 69, in <module>
            import httpx
        ModuleNotFoundError: No module named 'httpx'

    The vendor SDKs moved from `httpx` to `httpx2`, so a resolved environment
    ended up without `httpx` at all — and because `cli.py` imported this module
    at startup and this module imported `httpx` at module scope, **omega stopped
    opening entirely**, over a library nothing but Codex needed.

    Simulating the missing library is not the test: in the development
    environment the `anthropic` SDK itself still requires `httpx`, so blocking it
    breaks the SDK rather than omega and proves nothing. The invariant that
    actually holds is narrower and stronger — *starting omega must not load this
    file at all*. Whatever it imports then cannot cost anyone a startup.
    """
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        """
        import sys
        import omega_coding.cli
        loaded = "omega_ai.openai_codex" in sys.modules
        print("LOADED" if loaded else "NOT-LOADED")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "NOT-LOADED", (
        "cli.py imports the Codex adapter at startup, so anything that adapter "
        "imports can stop omega from opening"
    )


def test_the_adapter_still_reports_clearly_when_httpx_is_absent() -> None:
    """Deferring must not turn a missing library into a silent no-op.

    The feature is unavailable and the reason has to reach the user — an
    `ImportError` naming `httpx` is a better outcome than a provider that
    quietly does nothing.
    """
    import inspect

    source = inspect.getsource(openai_codex)
    module_level = [
        line
        for line in source.splitlines()
        if line.strip() == "import httpx" and not line.startswith((" ", "\t"))
    ]
    assert not module_level, "httpx is imported at module scope again"
    assert "import httpx" in source, "the runtime import was removed entirely"
