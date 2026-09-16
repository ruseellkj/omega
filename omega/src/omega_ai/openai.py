"""The OpenAI Chat Completions adapter — and the exam Tier 1 set itself.

`provider.py` has claimed since Tier 1 that the *core* owns the interface and
adapters conform to it. That was an argument, not evidence. Evidence requires a
second provider speaking a genuinely different wire format, added after
everything else exists — and then a diff showing nothing outside `providers/`
had to move.

The differences are not cosmetic. Each of these is a place where a leaked vendor
assumption would have surfaced as a change somewhere above:

| | Anthropic | OpenAI Chat Completions |
|---|---|---|
| system prompt | a top-level parameter | the first *message* |
| tool results | **merged into one user message** | **one `role: "tool"` message each** |
| tool arguments | `input_json_delta.partial_json` | `delta.tool_calls[i].function.arguments` |
| which call a fragment is for | the content block index | its own `index`, unrelated to content |
| block boundaries | explicit `content_block_stop` | none — inferred when the stream ends |
| reasoning | `thinking` blocks | absent from this format |
| stop | `stop_reason` | `finish_reason` |

The tool-result row is the sharpest. Anthropic **rejects** results split across
messages; OpenAI **requires** it. Two adapters, opposite rules, and the neutral
`ToolResultMessage` above them never had to pick a side — which is the whole
argument for the layer, stated as a diff rather than a claim.

One adapter, many endpoints: Groq, Together, Ollama and vLLM all speak this
format, so `base_url` is a parameter. `--provider openai --base-url
http://localhost:11434/v1` is a free local model.

The thinking events in `events.py` simply never fire here. A vocabulary that
allows more than any single provider produces is correct; one shaped to a
single vendor's features is how vendor detail leaks upward.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from openai import APIStatusError, AsyncOpenAI, AuthenticationError

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

DEFAULT_MODEL = "gpt-5"
DEFAULT_MAX_TOKENS = 8192

#: Same shape as the Anthropic adapter's. Both resolve immediately before a
#: request so a token can refresh mid-session.
AuthResolver = Callable[[], Awaitable[str]]


# ------------------------------------------------------------------ outbound

#: The current OpenAI name for "stop after N tokens". `max_tokens` is the older
#: spelling: **measured against the live API, gpt-5 rejects it outright while
#: gpt-4o-mini accepts either.** So the newer name is the default, and the older
#: one is what a server falls back to if it has never heard of the new one —
#: which matters because this adapter also speaks to Groq, Together, Ollama and
#: vLLM through `--base-url`.
TOKEN_LIMIT_KEY = "max_completion_tokens"
_LEGACY_TOKEN_LIMIT_KEY = "max_tokens"


def _other_token_limit_key(current: str, exc: APIStatusError) -> str | None:
    """The other spelling, if that is what this failure is about. Else None.

    Narrow on purpose. A 400 is normally fatal and stays fatal — this is the one
    case where the request is fine and only the field name is wrong, so retrying
    with the other name is the difference between working and not. Anything else
    is re-raised untouched.
    """
    detail = str(exc).lower()
    if getattr(exc, "status_code", None) != 400:
        return None
    if current.lower() not in detail:
        return None
    if "unsupported" not in detail and "not supported" not in detail:
        return None
    return (
        _LEGACY_TOKEN_LIMIT_KEY if current == TOKEN_LIMIT_KEY else TOKEN_LIMIT_KEY
    )


def to_openai_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def to_openai_messages(system: str, messages: list[AgentMessage]) -> list[dict[str, Any]]:
    """Neutral transcript to OpenAI's message list.

    Two vendor rules live here and nowhere else:

    * **the system prompt is a message**, not a parameter
    * **each tool result is its own `role: "tool"` message.** The Anthropic
      adapter merges consecutive results into one user message because Anthropic
      rejects them split. OpenAI requires the opposite. Neither rule escaped its
      adapter.
    """
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})

    for message in messages:
        if isinstance(message, UserMessage):
            out.append({"role": "user", "content": message.content})

        elif isinstance(message, AssistantMessage):
            text = message.text
            calls = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ]
            if not text and not calls:
                # Same rule as the other adapter, for the same reason: providers
                # reject a content-less assistant turn. A failed turn produces
                # exactly one of these.
                continue
            entry: dict[str, Any] = {"role": "assistant"}
            if text:
                entry["content"] = text
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)

        elif isinstance(message, ToolResultMessage):
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    # Never empty: some endpoints reject a blank tool message,
                    # and "(no output)" is also more useful to the model.
                    "content": message.text or "(no output)",
                }
            )

    return out


def normalise_finish_reason(raw: str | None, *, has_tool_calls: bool) -> DoneReason:
    """`finish_reason` to the same three values the other adapter produces.

    Content first, exactly as in the Anthropic adapter: if the response carries
    tool calls then it is a tool-use turn, whatever the vendor labelled it.
    """
    if has_tool_calls or raw == "tool_calls":
        return "toolUse"
    if raw == "length":
        return "length"
    return "stop"


# ------------------------------------------------------------------- adapter

class OpenAIProvider:
    """Implements `omega_agent.provider.ModelProvider`. Inherits from nothing.

    `base_url` is what makes this more than one vendor: Groq, Together, Ollama
    and vLLM all serve this wire format.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        auth: AuthResolver | None = None,
        base_url: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: AsyncOpenAI | None = None,
        retry: RetryPolicy = DEFAULT_RETRY,
    ) -> None:
        self._max_tokens = max_tokens
        #: Swapped once, permanently, if a server rejects the name. See
        #: `_other_token_limit_key`.
        self._token_limit_key = TOKEN_LIMIT_KEY
        self._retry = retry
        self._auth = auth
        #: Only a client we built is ours to close. See `aclose`.
        self._owns_client = client is None
        self._client = client or AsyncOpenAI(
            # Some local servers want no key at all; a placeholder keeps the SDK
            # from refusing to construct.
            api_key=api_key or os.environ.get("OPENAI_API_KEY") or "not-needed",
            base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
        )


    async def aclose(self) -> None:
        """Close the HTTP client, but only one this adapter created.

        The *stream* is already closed deterministically below; the **client**
        outlives it and owns a connection pool. Left to the garbage collector,
        that pool is torn down at interpreter shutdown — after the event loop is
        gone — and throws `GeneratorExit` into generators that can no longer
        handle it, which surfaces as a `generator didn't stop after athrow()`
        traceback printed after the session summary.

        A caller-supplied client is not ours to close: a test or an embedder may
        still be using it. Tau draws the same line (`tau_ai/anthropic.py:85-89`).
        """
        if self._owns_client:
            await self._client.close()

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
        """Retry while that is safe, and guarantee exactly one ending.

        Same shape as the Anthropic adapter's, and for the same reasons — which
        is itself a finding: this wrapper is provider-layer machinery rather than
        vendor translation, and `providers/streaming.py` is the obvious next
        refactor. Worth noting that the duplication is *inside* `providers/`,
        which is where the boundary said vendor concerns belong.
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
        """One try. Yields the twelve events; raises on failure."""
        if self._auth is not None:
            self._client.api_key = await self._auth()

        request: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(system, messages),
            "stream": True,
            self._token_limit_key: self._max_tokens,
            # Without this, a streamed response reports no usage at all and the
            # cost tracker silently counts zero.
            "stream_options": {"include_usage": True},
        }
        if tools:
            request["tools"] = to_openai_tools(tools)

        try:
            stream = await self._client.chat.completions.create(**cast("Any", request))
        except APIStatusError as exc:
            # One wire format, two spellings of the same field. Measured against
            # the live API: gpt-5 rejects `max_tokens`, gpt-4o-mini accepts both.
            alternate = _other_token_limit_key(self._token_limit_key, exc)
            if alternate is None:
                raise
            request.pop(self._token_limit_key)
            request[alternate] = self._max_tokens
            # Remembered, so a server costs at most one rejected request per
            # session rather than one per turn.
            self._token_limit_key = alternate
            stream = await self._client.chat.completions.create(**cast("Any", request))

        # **Closed deterministically, not by the garbage collector.** The
        # loop below `return`s early on cancellation, and the consumer stops
        # iterating as soon as a terminal event arrives - both abandon the
        # stream mid-flight. Left to GC, httpcore is closed at an arbitrary
        # moment and throws GeneratorExit into a suspended generator, which
        # surfaces as a `generator didn't stop after athrow()` traceback
        # printed over the model's answer. `anthropic.py:292` already used
        # `async with`; this brings the two adapters back into line.
        async with stream:
            # After the request is established, so a connection failure leaves nothing
            # emitted and the retry in `_stream` is still allowed to fire.
            yield AssistantStartEvent(partial=partial.model_copy(deep=True))

            text_index: int | None = None
            # OpenAI's tool_call index is its own sequence, unrelated to position in
            # our content list, so the two have to be mapped rather than assumed equal.
            our_index_of: dict[int, int] = {}
            # Keyed by *our* content index, so reassembling the JSON at the end needs
            # no reverse lookup through the map above.
            tool_json: dict[int, str] = {}
            finish: str | None = None

            async for chunk in stream:
                if signal is not None and signal.is_cancelled():
                    yield AssistantErrorEvent(
                        reason="aborted",
                        error=self._error_message(partial, "Cancelled", reason="aborted"),
                    )
                    return

                if chunk.usage is not None:
                    # **Nothing is sent to earn this.** OpenAI caches long
                    # prefixes server-side with no marker and no opt-in, which is
                    # the whole difference from Anthropic: there the saving has to
                    # be asked for, here it only has to be *reported*.
                    #
                    # Read through two optional levels, because `--base-url`
                    # points this same adapter at Ollama, vLLM and Groq and none
                    # of them promise the field. Missing reads as zero, which
                    # understates a saving rather than inventing one.
                    details = getattr(chunk.usage, "prompt_tokens_details", None)
                    partial.usage = Usage(
                        input=chunk.usage.prompt_tokens,
                        output=chunk.usage.completion_tokens,
                        cache_read=getattr(details, "cached_tokens", 0) or 0,
                    )

                if not chunk.choices:
                    # A usage-only final chunk. Legal, and easy to crash on.
                    continue

                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish = choice.finish_reason

                delta = choice.delta
                if delta is None:
                    continue

                if delta.content:
                    if text_index is None:
                        text_index = len(partial.content)
                        partial.content.append(TextContent(text=""))
                        yield TextStartEvent(
                            content_index=text_index, partial=partial.model_copy(deep=True)
                        )
                    block = partial.content[text_index]
                    if isinstance(block, TextContent):
                        block.text += delta.content
                    yield TextDeltaEvent(
                        content_index=text_index,
                        delta=delta.content,
                        partial=partial.model_copy(deep=True),
                    )

                for fragment in delta.tool_calls or []:
                    stream_index = fragment.index
                    if stream_index not in our_index_of:
                        our_index_of[stream_index] = len(partial.content)
                        tool_json[our_index_of[stream_index]] = ""
                        partial.content.append(
                            ToolCall(id=fragment.id or "", name="", arguments={})
                        )
                        yield ToolCallStartEvent(
                            content_index=our_index_of[stream_index],
                            partial=partial.model_copy(deep=True),
                        )

                    index = our_index_of[stream_index]
                    call = partial.content[index]
                    if not isinstance(call, ToolCall):
                        continue

                    # The id and the name arrive once, usually in the first fragment,
                    # and the arguments arrive as a string cut at arbitrary points.
                    if fragment.id:
                        call.id = fragment.id
                    if fragment.function is not None:
                        if fragment.function.name:
                            call.name = fragment.function.name
                        if fragment.function.arguments:
                            tool_json[index] += fragment.function.arguments
                            yield ToolCallDeltaEvent(
                                content_index=index,
                                delta=fragment.function.arguments,
                                partial=partial.model_copy(deep=True),
                            )

            # This format has no per-block stop event, so the `*_end` events are
            # emitted here, in content order, once the stream is complete.
            for index, block in enumerate(partial.content):
                if isinstance(block, TextContent):
                    yield TextEndEvent(
                        content_index=index,
                        content=block.text,
                        partial=partial.model_copy(deep=True),
                    )
                elif isinstance(block, ToolCall):
                    accumulated = tool_json.get(index, "").strip()
                    try:
                        block.arguments = json.loads(accumulated) if accumulated else {}
                    except json.JSONDecodeError:
                        # Malformed arguments are the model's problem to fix; surface
                        # them rather than crashing here.
                        block.arguments = {}
                    yield ToolCallEndEvent(
                        content_index=index,
                        tool_call=block.model_copy(deep=True),
                        partial=partial.model_copy(deep=True),
                    )

            final = partial.model_copy(deep=True)
            reason = normalise_finish_reason(finish, has_tool_calls=bool(final.tool_calls))
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
    """Turn an API failure into something a human can act on."""
    status = getattr(exc, "status_code", None)
    detail = str(exc)
    if isinstance(exc, AuthenticationError) or status == 401:
        return (
            "Authentication failed. Check OPENAI_API_KEY, or set OPENAI_BASE_URL to a "
            f"local server that does not need one. [{detail}]"
        )
    if status == 429:
        # 429 covers both "too fast" and "cannot pay". Reporting the second as
        # the first sends the user to the wrong settings page, and they wait for
        # a limit that will never reset.
        if is_permanent_quota_failure(exc):
            return (
                "Out of credits: this key's project has no balance, so retrying will "
                "not help. Add credits at platform.openai.com/settings/organization/"
                "billing. Note that an sk-proj- key is scoped to ONE project, so a key "
                f"that works elsewhere belongs to a different one. [{detail}]"
            )
        return f"Rate limited by the provider, and retries were exhausted. [{detail}]"
    if status == 404:
        return (
            "Model or endpoint not found. Check --model, and OPENAI_BASE_URL if you are "
            f"pointing at a local server. [{detail}]"
        )
    return f"Provider error (HTTP {status}). [{detail}]"
