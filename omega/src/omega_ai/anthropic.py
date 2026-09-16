"""The Anthropic adapter.

**The only file in omega that knows a vendor exists.** Everything above it sees
the twelve neutral events and nothing else, which is what makes adding a second
provider an addition rather than a rewrite.

It owes four promises upward, all enforced here so no caller has to think about
them:

1. exactly one `start`
2. exactly one terminal event — synthesised if the SDK stream ends without one
3. stop reasons normalised to `stop` / `length` / `toolUse`
4. **failures emitted as `error` events, never raised**

Promise 4 is the counter-intuitive one. A stream that produced 500 tokens and
then failed has two things to report; an exception carries only the failure and
discards the tokens. The `error` event carries both.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import Any, cast

from anthropic import APIStatusError, AsyncAnthropic, AuthenticationError
from anthropic.types import MessageParam, ToolParam

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
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from omega_agent.tools import Tool
from omega_agent.types import (
    AgentMessage,
    AssistantMessage,
    CancellationToken,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from omega_ai.retry import (
    DEFAULT_RETRY,
    RetryPolicy,
    delay_for,
    is_permanent_quota_failure,
    is_retryable,
    retry_after_of,
)

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 4096


# --------------------------------------------------------------- translation in

def to_anthropic_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    """Neutral tools → Anthropic tool definitions."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in tools
    ]


#: Anthropic allows four `cache_control` markers per request. The split below is
#: Tau's (`tau_ai/anthropic.py:61-66`) and the reasoning is the documented one:
#: sections that change at *different frequencies* deserve their own breakpoint.
MAX_CACHE_BREAKPOINTS = 4
SYSTEM_BREAKPOINTS = 1
TOOLS_BREAKPOINTS = 1
MESSAGE_BREAKPOINTS = MAX_CACHE_BREAKPOINTS - SYSTEM_BREAKPOINTS - TOOLS_BREAKPOINTS

#: The marker itself. Copied at every attach site so no two breakpoints share a
#: dict — mutating one in place would silently move the others.
CACHE_CONTROL: dict[str, Any] = {"type": "ephemeral"}

#: Blocks Anthropic will accept a breakpoint on. A marker anywhere else is
#: rejected outright, so the position is checked rather than assumed.
CACHEABLE_BLOCK_TYPES = frozenset({"text", "image", "tool_result"})


def _cacheable_system(system: str) -> list[dict[str, Any]]:
    """The system prompt as one cacheable block.

    A plain string cannot carry a marker, so the prompt becomes a one-element
    list of content blocks. The text is byte-identical either way; only the
    envelope changes, which matters because a single changed character
    invalidates every prefix hashed behind it.
    """
    return [{"type": "text", "text": system, "cache_control": dict(CACHE_CONTROL)}]


def _cacheable_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    """Tool definitions with a breakpoint on the last one.

    One marker, on the final tool, caches **all** of them: a breakpoint hashes
    the whole prefix up to and including its own block. Marking every tool would
    spend four breakpoints caching prefixes each already covered by the next.

    Separate from the system breakpoint even though system sits behind tools in
    the hierarchy, because the two change at different rates — editing `OMEGA.md`
    rewrites the system prompt while the schemas stay put, and a tools breakpoint
    survives that.
    """
    definitions = to_anthropic_tools(tools)
    if definitions:
        definitions[-1] = {**definitions[-1], "cache_control": dict(CACHE_CONTROL)}
    return definitions


def _mark_breakpoint(message: dict[str, Any]) -> None:
    """Attach a marker to a user message's final content block, if eligible.

    Only user messages: they are where a request *ends*, so they are the only
    positions whose prefix is worth hashing. An ineligible block is skipped
    rather than forced — a rejected request costs more than a missed cache.
    """
    if message.get("role") != "user":
        return

    content = message.get("content")
    if isinstance(content, str):
        if content:
            message["content"] = [
                {"type": "text", "text": content, "cache_control": dict(CACHE_CONTROL)}
            ]
        return

    if not isinstance(content, list) or not content:
        return
    last = content[-1]
    if not isinstance(last, dict) or last.get("type") not in CACHEABLE_BLOCK_TYPES:
        return
    content[-1] = {**last, "cache_control": dict(CACHE_CONTROL)}


def _previous_request_boundary(messages: list[dict[str, Any]]) -> int | None:
    """Where the previous request's message list ended.

    The transcript is append-only and every request stops just before the
    assistant message it produced, so the last user message *before* the final
    assistant turn is exactly where the previous request's tail breakpoint was
    placed — and therefore where a cache entry already exists.
    """
    last_assistant = None
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "assistant":
            last_assistant = index
            break
    if last_assistant is None:
        return None

    for index in range(last_assistant - 1, -1, -1):
        if messages[index].get("role") == "user":
            return index
    return None


def _apply_message_breakpoints(messages: list[dict[str, Any]]) -> None:
    """Mark this request's tail and the previous one's, in place.

    **This is where prompt caching actually pays.** The system prompt and the
    schemas are a fixed few hundred tokens; the conversation is everything else
    and it is re-sent in full on every single turn. Caching only the static
    prefix saves a rounding error — which is what omega did in its first pass at
    this, and why that pass was wrong.

    **Two breakpoints, not one.** Anthropic searches at most **20 block
    positions back** from a breakpoint for a reusable prefix, then gives up. A
    long turn can push the previous write out of that window, and the request
    then re-pays for the whole conversation. Marking where the last request ended
    opens a second lookback window at a position that is known to have been
    written, so the hit survives a wide turn.

    Either position may be ineligible, in which case fewer markers are emitted —
    missing a cache is cheap, an invalid request is not.
    """
    if not messages:
        return

    positions = {len(messages) - 1}
    boundary = _previous_request_boundary(messages)
    if boundary is not None:
        positions.add(boundary)

    if len(positions) > MESSAGE_BREAKPOINTS:  # pragma: no cover - defensive
        raise AssertionError("message breakpoints exceed the Anthropic budget of four")

    for position in positions:
        _mark_breakpoint(messages[position])


def _assistant_content(message: AssistantMessage) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextContent):
            if block.text:
                blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ThinkingContent):
            thinking: dict[str, Any] = {"type": "thinking", "thinking": block.thinking}
            if block.signature is not None:
                # Opaque and meaningless to us, but it must go back verbatim or
                # multi-turn reasoning breaks.
                thinking["signature"] = block.signature
            blocks.append(thinking)
        elif isinstance(block, ToolCall):
            blocks.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": block.arguments}
            )
    return blocks


def to_anthropic_messages(messages: list[AgentMessage]) -> list[dict[str, Any]]:
    """Neutral transcript → Anthropic `messages`.

    Two vendor rules handled here rather than upstream:

    * tool results travel in a **user** message, not their own role
    * consecutive tool results must be **merged into one** user message, or a
      turn with parallel tool calls is rejected
    """
    out: list[dict[str, Any]] = []
    pending_results: list[dict[str, Any]] = []

    def flush() -> None:
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for message in messages:
        if isinstance(message, ToolResultMessage):
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.text,
                    "is_error": message.is_error,
                }
            )
            continue

        flush()
        if isinstance(message, UserMessage):
            out.append({"role": "user", "content": message.content})
        elif isinstance(message, AssistantMessage):
            blocks = _assistant_content(message)
            if blocks:  # an empty assistant turn is rejected outright
                out.append({"role": "assistant", "content": blocks})

    flush()
    return out


def normalise_stop_reason(raw: str | None, *, has_tool_calls: bool) -> DoneReason:
    """Anthropic's spellings → the three canonical reasons."""
    if has_tool_calls or raw == "tool_use":
        return "toolUse"
    if raw == "max_tokens":
        return "length"
    return "stop"


#: Supplies the API key, called immediately before each request. A callback
#: rather than a string so a token can be refreshed without rebuilding the
#: provider - Tau calls the same idea a RuntimeProviderAuthResolver.
AuthResolver = Callable[[], Awaitable[str]]


# ------------------------------------------------------------------- the adapter

class AnthropicProvider:
    """Implements `omega_agent.provider.ModelProvider`. Inherits from nothing."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        auth: AuthResolver | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: AsyncAnthropic | None = None,
        retry: RetryPolicy = DEFAULT_RETRY,
    ) -> None:
        self._max_tokens = max_tokens
        self._retry = retry

        #: Resolved before every request rather than read once at construction.
        #: That difference is the whole point: a static string cannot refresh, so
        #: a subscription token would expire mid-session and there would be no
        #: place to notice. Full OAuth is a post-Tier-3 item; this is the seam it
        #: needs, and it costs about ten lines to have now instead of later.
        self._auth = auth

        self._client = client or AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[Tool],
        signal: CancellationToken | None = None,
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
        signal: CancellationToken | None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        """Attempt the request, retrying while that is still safe, and guarantee
        exactly one ending.

        All of failure #5 lives here, and so does the single place that turns an
        exception into an event. `_attempt` is free to raise; nothing above this
        line ever sees an exception from a provider.
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
                # Retry only while nothing has gone upward. Once tokens have been
                # emitted, restarting the stream would produce them a second time,
                # so a late failure is reported with whatever arrived before it.
                may_retry = emitted == 0 and attempt + 1 < self._retry.attempts
                if may_retry and is_retryable(exc):
                    await asyncio.sleep(
                        delay_for(attempt, self._retry, retry_after=retry_after_of(exc))
                    )
                    attempt += 1
                    continue

                detail = (
                    _explain(exc)
                    if isinstance(exc, APIStatusError)
                    else f"{type(exc).__name__}: {exc}"
                )
                yield AssistantErrorEvent(
                    reason="error", error=self._error_message(partial, detail)
                )
                return

            if not terminal:
                # Promise 2: a consumer always gets an ending, even if the vendor
                # stream simply stopped.
                yield AssistantErrorEvent(
                    reason="error",
                    error=self._error_message(
                        partial, "Provider stream ended without a terminal event"
                    ),
                )
            return

    async def _attempt(  # noqa: C901 - a translator; the branching is the job
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[Tool],
        signal: CancellationToken | None,
        partial: AssistantMessage,
    ) -> AsyncIterator[AssistantMessageEvent]:
        """One try. Yields the twelve events; raises on failure.

        `partial` is supplied by the caller and mutated here, so that when this
        raises, the caller still holds everything that arrived first. That is how
        "a stream that failed after 500 tokens keeps the 500 tokens" survives the
        retry restructuring.
        """
        raw_stop: str | None = None
        tool_json: dict[int, str] = {}

        # Resolved here, not in __init__, and re-resolved on every retry - which
        # is exactly what makes an expiring token survivable.
        if self._auth is not None:
            self._client.api_key = await self._auth()

        # Built before the call so the breakpoints are visible in one place.
        # `to_anthropic_messages` stays a pure translator; marking happens here,
        # because where a request *ends* is a caching question, not a wire-format
        # one.
        payload_messages = to_anthropic_messages(messages)
        _apply_message_breakpoints(payload_messages)

        async with self._client.messages.stream(
            model=model,
            max_tokens=self._max_tokens,
            # Four breakpoints, spent deliberately: system, the last tool, and
            # the two message tails. Anthropic hashes the whole prefix up to and
            # including a marked block, so each one buys everything behind it.
            system=cast("Any", _cacheable_system(system)),
            # The translators return plain dicts on purpose: it keeps them
            # testable without constructing SDK objects. Casting here is the
            # honest place to reconcile that with the client's typed params.
            messages=cast("Iterable[MessageParam]", payload_messages),
            tools=cast("Iterable[ToolParam]", _cacheable_tools(tools)),
        ) as stream:
            async for raw in stream:
                if signal is not None and signal.is_cancelled():
                    yield AssistantErrorEvent(
                        reason="aborted",
                        error=self._error_message(partial, "Cancelled", reason="aborted"),
                    )
                    return

                # Narrow on `raw.type` inline. Assigning the discriminator to a
                # variable first defeats type narrowing — the checker can no
                # longer tell which event shape it is holding.
                if raw.type == "message_start":
                    # Both fields are optional on the wire and absent on older
                    # API versions, so they are read defensively rather than
                    # indexed. Zero means "not reported", which reads the same as
                    # "nothing cached" and is the safe conflation: it never
                    # overstates a saving.
                    reported = raw.message.usage
                    partial.usage = Usage(
                        input=reported.input_tokens,
                        output=0,
                        cache_write=getattr(reported, "cache_creation_input_tokens", 0) or 0,
                        cache_read=getattr(reported, "cache_read_input_tokens", 0) or 0,
                    )
                    yield AssistantStartEvent(partial=partial.model_copy(deep=True))

                elif raw.type == "content_block_start":
                    index = raw.index
                    block = raw.content_block
                    if block.type == "text":
                        partial.content.append(TextContent(text=""))
                        yield TextStartEvent(
                            content_index=index, partial=partial.model_copy(deep=True)
                        )
                    elif block.type == "thinking":
                        partial.content.append(ThinkingContent(thinking=""))
                        yield ThinkingStartEvent(
                            content_index=index, partial=partial.model_copy(deep=True)
                        )
                    elif block.type == "tool_use":
                        tool_json[index] = ""
                        partial.content.append(
                            ToolCall(id=block.id, name=block.name, arguments={})
                        )
                        yield ToolCallStartEvent(
                            content_index=index, partial=partial.model_copy(deep=True)
                        )

                elif raw.type == "content_block_delta":
                    index = raw.index
                    current = partial.content[index] if index < len(partial.content) else None

                    if raw.delta.type == "text_delta" and isinstance(current, TextContent):
                        current.text += raw.delta.text
                        yield TextDeltaEvent(
                            content_index=index,
                            delta=raw.delta.text,
                            partial=partial.model_copy(deep=True),
                        )
                    elif raw.delta.type == "thinking_delta" and isinstance(
                        current, ThinkingContent
                    ):
                        current.thinking += raw.delta.thinking
                        yield ThinkingDeltaEvent(
                            content_index=index,
                            delta=raw.delta.thinking,
                            partial=partial.model_copy(deep=True),
                        )
                    elif raw.delta.type == "signature_delta" and isinstance(
                        current, ThinkingContent
                    ):
                        current.signature = raw.delta.signature
                    elif raw.delta.type == "input_json_delta":
                        tool_json[index] = tool_json.get(index, "") + raw.delta.partial_json
                        yield ToolCallDeltaEvent(
                            content_index=index,
                            delta=raw.delta.partial_json,
                            partial=partial.model_copy(deep=True),
                        )

                elif raw.type == "content_block_stop":
                    index = raw.index
                    current = partial.content[index] if index < len(partial.content) else None

                    if isinstance(current, TextContent):
                        yield TextEndEvent(
                            content_index=index,
                            content=current.text,
                            partial=partial.model_copy(deep=True),
                        )
                    elif isinstance(current, ThinkingContent):
                        yield ThinkingEndEvent(
                            content_index=index,
                            content=current.thinking,
                            partial=partial.model_copy(deep=True),
                        )
                    elif isinstance(current, ToolCall):
                        accumulated = tool_json.get(index, "").strip()
                        try:
                            current.arguments = json.loads(accumulated) if accumulated else {}
                        except json.JSONDecodeError:
                            # Malformed arguments are the model's problem to
                            # fix; surface them rather than crashing here.
                            current.arguments = {}
                        yield ToolCallEndEvent(
                            content_index=index,
                            tool_call=current.model_copy(deep=True),
                            partial=partial.model_copy(deep=True),
                        )

                elif raw.type == "message_delta":
                    raw_stop = raw.delta.stop_reason
                    # `model_copy`, not `Usage(...)`. Rebuilding it here dropped
                    # every field the constructor did not mention, which silently
                    # reset the cache counts recorded at `message_start` — so the
                    # start event reported a cache hit and the final message,
                    # the one anything downstream actually reads, reported none.
                    partial.usage = partial.usage.model_copy(
                        update={"output": raw.usage.output_tokens}
                    )

        final = partial.model_copy(deep=True)
        reason = normalise_stop_reason(raw_stop, has_tool_calls=bool(final.tool_calls))
        final.stop_reason = reason
        yield AssistantDoneEvent(reason=reason, message=final)
    @staticmethod
    def _error_message(
        partial: AssistantMessage, message: str, *, reason: str = "error"
    ) -> AssistantMessage:
        error = partial.model_copy(deep=True)
        error.stop_reason = "aborted" if reason == "aborted" else "error"
        error.error_message = message
        return error


def _explain(exc: APIStatusError) -> str:
    """Turn an API failure into something a human can act on.

    Credit and auth problems are the two you actually hit, and a raw stack trace
    for either is a poor experience.
    """
    status = getattr(exc, "status_code", None)
    detail = str(exc)
    if isinstance(exc, AuthenticationError) or status == 401:
        return (
            "Authentication failed. Check ANTHROPIC_API_KEY in your .env "
            f"(see .env.sample). [{detail}]"
        )
    if status == 429 and is_permanent_quota_failure(exc):
        # A workspace whose rate limit is configured to 0 returns 429 forever.
        # It reads as throttling and is really a settings problem.
        return (
            "This workspace's rate limit is set to 0, so every request is refused and "
            "retrying will not help. Raise it in the Anthropic Console under Settings "
            f"> Workspaces, or use a key from another workspace. [{detail}]"
        )
    if status == 400 and "credit balance" in detail.lower():
        return (
            "Your Anthropic credit balance is too low for this request. "
            "Top up at console.anthropic.com, or run `omega --fake` to use "
            f"scripted responses instead. [{detail}]"
        )
    if status == 429:
        return f"Rate limited by the provider, and retries were exhausted. [{detail}]"
    return f"Provider error (HTTP {status}). [{detail}]"
