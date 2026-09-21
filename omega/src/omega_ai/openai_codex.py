"""The ChatGPT subscription backend, which is not the OpenAI API.

## Why this file exists at all, when `openai.py` already talks to OpenAI

It talks to a **different product**. Signing in with a ChatGPT subscription
returns a token that `api.openai.com` rejects outright; it opens
`chatgpt.com/backend-api/codex/responses`, which speaks the Responses API rather
than Chat Completions. Two wire formats, so two adapters — the same reason
`anthropic.py` and `openai.py` are separate files.

Both references reached the same conclusion independently, and it is the single
clearest piece of evidence that this is not a header change:

| | lines |
|---|---|
| `research/tau/src/tau_ai/openai_compatible.py` (API key) | 1,240 |
| `research/tau/src/tau_ai/openai_codex.py` (subscription) | 1,054 |

Pi splits it identically — `providers/openai-codex.ts` plus
`api/openai-codex-responses.ts`.

## What is actually different about the format

Chat Completions sends a list of **messages** and gets back **choices** with
`delta` fragments. The Responses API sends a list of **items** and streams
**named events** about them:

| Chat Completions | here |
|---|---|
| `messages: [{role, content}]` | `input: [{type: "message"...}, {type: "function_call"...}]` |
| `system` as a message | `instructions`, a top-level string |
| `choices[0].delta.content` | `response.output_text.delta` |
| `delta.tool_calls[i].function.arguments` | `response.function_call_arguments.delta` |
| `finish_reason` on the choice | `response.completed` / `.incomplete` / `.failed` |
| one id per tool call | **two** — `call_id` and `id`, both needed on the way back |

That last row is the one that bites. A function call has a `call_id` (which the
result is addressed to) *and* an item `id` (which identifies the item in the
conversation). omega's `ToolCall` has one id field, so the two are packed into it
and split apart again on the next turn — see `_join_ids`.

## Reasoning has to be handed back verbatim

`response.output_item.added` delivers a `reasoning` item, and the next request
must contain that item **unchanged** or multi-turn reasoning degrades. omega
already has somewhere to put it: `ThinkingContent.signature` is documented as "an
opaque provider token… handed back verbatim on the next turn", which is exactly
this. The item is stored there as JSON and replayed from there. No change to the
neutral model was needed, which is a small vindication of that field's docstring.

## What it does not do

**`httpx` is imported inside the functions that use it**, never at module
scope, and that is not style. `cli.py` imports this module to build a provider,
so a module-scope import makes an absent or mismatched `httpx` a crash *before
omega can start* rather than a feature that is unavailable. It happened: the
vendor SDKs moved from `httpx` to `httpx2`, an installed copy resolved without
`httpx` at all, and `omega` stopped opening with a traceback about a library the
user had never heard of. `omega_coding/oauth.py:384` already deferred it for the
same reason; this file did not, and that was the whole bug.

**No `openai` SDK import.** The SDK speaks Chat Completions and the Responses
API against `api.openai.com`; none of that helps against a different host with a
different auth header, and importing it here would break the rule that exactly
two files touch a vendor SDK (`tests/test_layers.py`). Plain `httpx` and an SSE
reader instead — about sixty lines, and it is the same choice Tau made.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from platform import machine, release
from platform import system as os_name
from typing import TYPE_CHECKING, Any

from omega_agent.events import (
    TERMINAL_EVENT_TYPES,
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    DoneReason,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from omega_agent.tools import Tool
from omega_agent.types import (
    AgentMessage,
    AssistantMessage,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from omega_ai.retry import DEFAULT_RETRY, RetryPolicy, delay_for

if TYPE_CHECKING:
    import httpx

DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"

#: What a ChatGPT subscription serves. Not one of the `api.openai.com` model
#: names — the backend publishes its own list at `/codex/models`, and this is the
#: one both references default to.
DEFAULT_MODEL = "gpt-5-codex"

#: Sent on every request. `originator` and the `codex` user-agent shape are what
#: the backend matches on; `OpenAI-Beta` opts into the Responses surface.
#: Measured identical in both references (`tau/openai_codex.py:946-964`,
#: `pi/openai-codex-responses.ts`).
ORIGINATOR = "codex_cli_rs"

#: Statuses worth another attempt. 429 and 5xx are the provider asking for time;
#: everything else is a request that will fail again identically.
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})

AuthResolver = Callable[[], Awaitable[str]]


# ------------------------------------------------------------------ translators
# Pure functions, no client, no IO. They are the half of this file that can be
# tested without inventing a server.


def _join_ids(call_id: str, item_id: str | None) -> str:
    """Pack the two ids a Responses function call carries into omega's one.

    `call_id` addresses the result; `id` identifies the item. Both must come back
    on the next turn, and `ToolCall.id` is a single string — so they are joined
    with a separator that cannot occur in either (both are provider-generated
    `fc_…`/`call_…` tokens).
    """
    return f"{call_id}|{item_id}" if item_id else call_id


def _split_ids(value: str) -> tuple[str, str | None]:
    call_id, _, item_id = value.partition("|")
    return call_id, item_id or None


def to_codex_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    """Flat, unlike Chat Completions' `{"type": "function", "function": {...}}`.

    The Responses API puts `name` and `parameters` at the top level. Nesting them
    the Chat Completions way is accepted and then ignored, which is worse than a
    rejection — the model simply never sees the tools.
    """
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.parameters),
            "strict": None,
        }
        for tool in tools
    ]


def to_codex_input(messages: list[AgentMessage]) -> list[dict[str, Any]]:
    """Neutral messages → Responses items.

    One assistant *message* can become several *items* — its reasoning, its text
    and each of its tool calls are separate entries in the list. That flattening
    is the core difference from the Chat Completions translator next door, where
    an assistant message stays one object with a `tool_calls` array.
    """
    items: list[dict[str, Any]] = []
    text_index = 0

    for message in messages:
        if isinstance(message, UserMessage):
            items.append(
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": _text_of(message.content)}],
                }
            )

        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ThinkingContent):
                    # Replayed verbatim, or reasoning degrades across turns. The
                    # signature holds the original item as JSON; anything else is
                    # skipped rather than guessed at.
                    replayed = _loads_item(block.signature)
                    if replayed is not None:
                        items.append(replayed)
                elif isinstance(block, TextContent) and block.text:
                    items.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": block.text, "annotations": []}
                            ],
                            "status": "completed",
                            "id": f"msg_{text_index}",
                        }
                    )
                    text_index += 1
                elif isinstance(block, ToolCall):
                    call_id, item_id = _split_ids(block.id)
                    call: dict[str, Any] = {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": block.name,
                        "arguments": json.dumps(block.arguments),
                    }
                    if item_id:
                        call["id"] = item_id
                    items.append(call)

        elif isinstance(message, ToolResultMessage):
            call_id, _ = _split_ids(message.tool_call_id)
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    # Never empty: the endpoint rejects a null output, and "the
                    # tool produced nothing" is a real result the model should
                    # see rather than an error.
                    "output": _text_of(message.content) or "(no output)",
                }
            )

    return items


def build_payload(
    *, model: str, system: str, messages: list[AgentMessage], tools: list[Tool]
) -> dict[str, Any]:
    """The request body.

    `store: False` because the conversation lives in omega's session file, and
    asking the backend to retain it as well is a copy nobody asked for.
    """
    payload: dict[str, Any] = {
        "model": model,
        "store": False,
        "stream": True,
        "instructions": system or "You are a helpful assistant.",
        "input": to_codex_input(messages),
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        # Asks for the reasoning item to come back in a form that can be replayed
        # on the next turn. Without it the item arrives without its payload and
        # the round trip above has nothing to store.
        "include": ["reasoning.encrypted_content"],
    }
    if tools:
        payload["tools"] = to_codex_tools(tools)
    return payload


def build_headers(*, access_token: str, account_id: str) -> dict[str, str]:
    """What the backend needs beyond the bearer token.

    `chatgpt-account-id` is the one that is easy to miss: the token alone is not
    enough, and its absence is a 401 that reads like a bad token rather than a
    missing header. The id is a claim inside the access JWT, read at sign-in and
    stored beside it (`omega_coding/oauth.py:account_id_from_token`).
    """
    return {
        "Authorization": f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "originator": ORIGINATOR,
        "User-Agent": f"omega ({os_name()} {release()}; {machine()})",
        "OpenAI-Beta": "responses=experimental",
        "accept": "text/event-stream",
        "content-type": "application/json",
    }


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return "".join(parts)


def _loads_item(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def sse_objects(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Server-sent events → the JSON objects they carry.

    Deliberately takes *lines* rather than a response, so the whole event
    vocabulary below can be driven from a list of strings in a test. Comment
    lines, blank separators, `[DONE]` sentinels and unparseable payloads are all
    skipped — a stream that ends is not the same as a stream that broke.
    """
    async for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(":") or not stripped.startswith("data:"):
            continue
        payload = stripped[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            parsed = json.loads(payload)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            yield parsed


# --------------------------------------------------------------- the adapter


class _ToolBuilder:
    """One function call, assembled across deltas.

    Keyed three ways because the events identify it inconsistently: some carry
    `item_id`, some `call_id`, some only `output_index`. Tau keeps the same three
    lookups for the same reason (`openai_codex.py:696-756`).
    """

    def __init__(self, *, call_id: str, item_id: str | None, name: str, index: int) -> None:
        self.call_id = call_id
        self.item_id = item_id
        self.name = name
        self.index = index
        self.arguments = ""

    def finish(self) -> ToolCall:
        try:
            parsed = json.loads(self.arguments) if self.arguments.strip() else {}
        except ValueError:
            # A model that emits malformed JSON has still asked for the tool. The
            # runner reports the bad arguments far more usefully than a stream
            # that died mid-turn would.
            parsed = {}
        return ToolCall(
            id=_join_ids(self.call_id, self.item_id),
            name=self.name,
            arguments=parsed if isinstance(parsed, dict) else {},
        )


class CodexProvider:
    """Implements `omega_agent.provider.ModelProvider`. Inherits from nothing."""

    def __init__(
        self,
        *,
        auth: AuthResolver,
        account_id: str,
        base_url: str = DEFAULT_BASE_URL,
        retry: RetryPolicy = DEFAULT_RETRY,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        #: Resolved per request, like the Anthropic adapter's — a subscription
        #: token expires in hours and a long turn must survive the boundary.
        self._auth = auth
        self._account_id = account_id
        self._url = _responses_url(base_url)
        self._retry = retry
        self._owns_client = client is None
        if client is None:
            import httpx

            client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=20.0))
        self._client = client

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[Tool],
        signal: Any = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return self._stream(
            model=model, system=system, messages=messages, tools=tools, signal=signal
        )

    async def _stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[Tool],
        signal: Any,
    ) -> AsyncIterator[AssistantMessageEvent]:
        """Retry while that is safe, and guarantee exactly one ending.

        The same shape as the other two adapters, and the same rule: **a stream
        that has already emitted is never restarted.** Retrying one would replay
        text the screen has shown and the transcript has recorded.
        """
        attempt = 0
        while True:
            partial = AssistantMessage(model=model)
            emitted = 0
            terminal = False

            try:
                async for event in self._attempt(
                    model=model,
                    system=system,
                    messages=messages,
                    tools=tools,
                    signal=signal,
                    partial=partial,
                ):
                    emitted += 1
                    terminal = event.type in TERMINAL_EVENT_TYPES
                    yield event
            except Exception as exc:  # noqa: BLE001 - this boundary converts all of them
                may_retry = emitted == 0 and attempt + 1 < self._retry.attempts
                if may_retry and _is_retryable(exc):
                    await asyncio.sleep(delay_for(attempt, self._retry))
                    attempt += 1
                    continue
                yield AssistantErrorEvent(
                    reason="error",
                    error=_errored(partial, f"{type(exc).__name__}: {exc}"),
                )
                return

            if not terminal:
                yield AssistantErrorEvent(
                    reason="error",
                    error=_errored(partial, "Provider stream ended without a terminal event"),
                )
            return

    async def _attempt(  # noqa: C901 - a translator; the branching is the job
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[Tool],
        signal: Any,
        partial: AssistantMessage,
    ) -> AsyncIterator[AssistantMessageEvent]:
        token = await self._auth()
        payload = build_payload(model=model, system=system, messages=messages, tools=tools)
        headers = build_headers(access_token=token, account_id=self._account_id)

        index = 0
        text = ""
        text_open = False
        thinking = ""
        thinking_open = False
        builders: dict[str, _ToolBuilder] = {}
        by_output_index: dict[int, _ToolBuilder] = {}
        reason: DoneReason = "stop"

        async with self._client.stream(
            "POST", self._url, json=payload, headers=headers
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode(errors="replace")
                raise _HttpFailure(response.status_code, body[:400])

            # **After the request is established**, so a connection failure or a
            # 429 leaves nothing emitted and the retry in `_stream` is still
            # allowed to fire. `openai.py:417-419` carries the same note; this
            # adapter had it wrong first, and a test that asserted a 429 retries
            # is what found it.
            yield AssistantStartEvent(partial=partial)

            async for event in sse_objects(_lines_of(response)):
                if signal is not None and signal.is_cancelled():
                    yield AssistantErrorEvent(
                        reason="aborted", error=_errored(partial, "Cancelled", reason="aborted")
                    )
                    return

                kind = event.get("type")

                if kind in {"error", "response.failed"}:
                    raise _StreamFailure(_failure_text(event))

                if kind == "response.output_item.added":
                    item = event.get("item")
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "reasoning":
                        # Stored whole, and handed back untouched next turn.
                        thinking_open = True
                        thinking = ""
                        partial.content.append(
                            ThinkingContent(thinking="", signature=json.dumps(item))
                        )
                        yield ThinkingStartEvent(content_index=index, partial=partial)
                    elif item.get("type") == "function_call":
                        builder = _ToolBuilder(
                            call_id=str(item.get("call_id") or item.get("id") or ""),
                            item_id=str(item["id"]) if item.get("id") else None,
                            name=str(item.get("name") or ""),
                            index=index,
                        )
                        _track(builder, event, builders, by_output_index)
                        partial.content.append(
                            ToolCall(id=builder.call_id, name=builder.name, arguments={})
                        )
                        yield ToolCallStartEvent(content_index=index, partial=partial)

                elif kind == "response.output_text.delta":
                    delta = str(event.get("delta") or "")
                    if not text_open:
                        text_open = True
                        partial.content.append(TextContent(text=""))
                        yield TextStartEvent(content_index=index, partial=partial)
                    text += delta
                    _set_last_text(partial, text)
                    yield TextDeltaEvent(content_index=index, delta=delta, partial=partial)

                elif kind in {
                    "response.reasoning.delta",
                    "response.reasoning_text.delta",
                    "response.reasoning_summary_text.delta",
                }:
                    delta = str(event.get("delta") or "")
                    if not thinking_open:
                        thinking_open = True
                        partial.content.append(ThinkingContent(thinking=""))
                        yield ThinkingStartEvent(content_index=index, partial=partial)
                    thinking += delta
                    _set_last_thinking(partial, thinking)
                    yield ThinkingDeltaEvent(content_index=index, delta=delta, partial=partial)

                elif kind == "response.function_call_arguments.delta":
                    found = _lookup(event, builders, by_output_index)
                    if found is not None:
                        found.arguments += str(event.get("delta") or "")

                elif kind == "response.function_call_arguments.done":
                    found = _lookup(event, builders, by_output_index)
                    if found is not None and isinstance(event.get("arguments"), str):
                        found.arguments = str(event["arguments"])

                elif kind in {"response.output_item.done", "response.output_item.completed"}:
                    item = event.get("item")
                    item_type = item.get("type") if isinstance(item, dict) else None

                    if item_type == "message" and text_open:
                        yield TextEndEvent(content_index=index, content=text, partial=partial)
                        text_open, text, index = False, "", index + 1
                    elif item_type == "reasoning" and thinking_open:
                        yield ThinkingEndEvent(
                            content_index=index, content=thinking, partial=partial
                        )
                        thinking_open, thinking, index = False, "", index + 1
                    elif item_type == "function_call":
                        found = _lookup(event, builders, by_output_index)
                        if found is not None:
                            call = found.finish()
                            _replace_last_call(partial, call)
                            reason = "toolUse"
                            yield ToolCallEndEvent(
                                content_index=index, tool_call=call, partial=partial
                            )
                            index += 1

                elif kind in {"response.completed", "response.done", "response.incomplete"}:
                    partial.usage = _usage_of(event) or partial.usage
                    if kind == "response.incomplete":
                        reason = "length"
                    if text_open:
                        yield TextEndEvent(content_index=index, content=text, partial=partial)
                    partial.stop_reason = reason
                    yield AssistantDoneEvent(reason=reason, message=partial)
                    return


# ------------------------------------------------------------------- plumbing


class _HttpFailure(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status


class _StreamFailure(Exception):
    """An error the stream reported about itself, rather than a transport fault."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, _HttpFailure):
        return exc.status in RETRYABLE_STATUS
    import httpx

    return isinstance(exc, httpx.TransportError)


async def _lines_of(response: httpx.Response) -> AsyncIterator[str]:
    async for line in response.aiter_lines():
        yield line


def _responses_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/codex/responses"):
        return trimmed
    if trimmed.endswith("/codex"):
        return f"{trimmed}/responses"
    return f"{trimmed}/codex/responses"


def _track(
    builder: _ToolBuilder,
    event: Mapping[str, Any],
    builders: dict[str, _ToolBuilder],
    by_output_index: dict[int, _ToolBuilder],
) -> None:
    for key in (builder.call_id, builder.item_id):
        if key:
            builders[key] = builder
    output_index = event.get("output_index")
    if isinstance(output_index, int):
        by_output_index[output_index] = builder


def _lookup(
    event: Mapping[str, Any],
    builders: dict[str, _ToolBuilder],
    by_output_index: dict[int, _ToolBuilder],
) -> _ToolBuilder | None:
    """Three lookups because the events identify a call three different ways."""
    item = event.get("item")
    candidates = [event.get("item_id"), event.get("call_id")]
    if isinstance(item, Mapping):
        candidates += [item.get("id"), item.get("call_id")]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate in builders:
            return builders[candidate]
    output_index = event.get("output_index")
    if isinstance(output_index, int):
        return by_output_index.get(output_index)
    return None


def _set_last_text(partial: AssistantMessage, text: str) -> None:
    for block in reversed(partial.content):
        if isinstance(block, TextContent):
            block.text = text
            return


def _set_last_thinking(partial: AssistantMessage, thinking: str) -> None:
    for block in reversed(partial.content):
        if isinstance(block, ThinkingContent):
            block.thinking = thinking
            return


def _replace_last_call(partial: AssistantMessage, call: ToolCall) -> None:
    for position in range(len(partial.content) - 1, -1, -1):
        if isinstance(partial.content[position], ToolCall):
            partial.content[position] = call
            return


def _usage_of(event: Mapping[str, Any]) -> Usage | None:
    response = event.get("response")
    usage = response.get("usage") if isinstance(response, Mapping) else event.get("usage")
    if not isinstance(usage, Mapping):
        return None
    details = usage.get("input_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, Mapping) else 0
    return Usage(
        input=_int(usage.get("input_tokens")),
        output=_int(usage.get("output_tokens")),
        cache_read=_int(cached),
    )


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _failure_text(event: Mapping[str, Any]) -> str:
    response = event.get("response")
    error = response.get("error") if isinstance(response, Mapping) else event.get("error")
    source = error if isinstance(error, Mapping) else event
    message = source.get("message")
    return str(message) if message else "the ChatGPT backend reported an error"


def _errored(
    partial: AssistantMessage, detail: str, *, reason: StopReason = "error"
) -> AssistantMessage:
    """Keep whatever arrived before the failure — that is the point of the shape."""
    partial.stop_reason = reason
    partial.error_message = detail
    return partial
