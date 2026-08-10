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

import json
import os
from collections.abc import AsyncIterator, Iterable
from typing import Any, cast

from anthropic import APIStatusError, AsyncAnthropic, AuthenticationError
from anthropic.types import MessageParam, ToolParam

from omega.events import (
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
from omega.tools import Tool
from omega.types import (
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

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 4096


# --------------------------------------------------------------- translation in

def to_anthropic_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    """Neutral tools → Anthropic tool definitions."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in tools
    ]


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


# ------------------------------------------------------------------- the adapter

class AnthropicProvider:
    """Implements `omega.provider.ModelProvider`. Inherits from nothing."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self._max_tokens = max_tokens
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

    async def _stream(  # noqa: C901 - a translator; the branching is the job
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[Tool],
        signal: CancellationToken | None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        partial = AssistantMessage(model=model)
        terminal = False
        raw_stop: str | None = None
        tool_json: dict[int, str] = {}

        try:
            async with self._client.messages.stream(
                model=model,
                max_tokens=self._max_tokens,
                system=system,
                # The translators return plain dicts on purpose: it keeps them
                # testable without constructing SDK objects. Casting here is the
                # honest place to reconcile that with the client's typed params.
                messages=cast("Iterable[MessageParam]", to_anthropic_messages(messages)),
                tools=cast("Iterable[ToolParam]", to_anthropic_tools(tools)),
            ) as stream:
                async for raw in stream:
                    if signal is not None and signal.is_cancelled():
                        terminal = True
                        yield AssistantErrorEvent(
                            reason="aborted",
                            error=self._error_message(partial, "Cancelled", reason="aborted"),
                        )
                        return

                    # Narrow on `raw.type` inline. Assigning the discriminator to a
                    # variable first defeats type narrowing — the checker can no
                    # longer tell which event shape it is holding.
                    if raw.type == "message_start":
                        partial.usage = Usage(input=raw.message.usage.input_tokens, output=0)
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
                        partial.usage = Usage(
                            input=partial.usage.input, output=raw.usage.output_tokens
                        )

            final = partial.model_copy(deep=True)
            reason = normalise_stop_reason(raw_stop, has_tool_calls=bool(final.tool_calls))
            final.stop_reason = reason
            terminal = True
            yield AssistantDoneEvent(reason=reason, message=final)

        except (AuthenticationError, APIStatusError) as exc:
            terminal = True
            yield AssistantErrorEvent(
                reason="error", error=self._error_message(partial, _explain(exc))
            )
        except Exception as exc:  # noqa: BLE001 - the boundary converts everything to events
            terminal = True
            yield AssistantErrorEvent(
                reason="error",
                error=self._error_message(partial, f"{type(exc).__name__}: {exc}"),
            )

        if not terminal:
            # Promise 2: a consumer always gets an ending, even if the vendor
            # stream simply stopped.
            yield AssistantErrorEvent(
                reason="error",
                error=self._error_message(
                    partial, "Provider stream ended without a terminal event"
                ),
            )

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
    if status == 400 and "credit balance" in detail.lower():
        return (
            "Your Anthropic credit balance is too low for this request. "
            "Top up at console.anthropic.com, or run `omega --fake` to use "
            f"scripted responses instead. [{detail}]"
        )
    if status == 429:
        return f"Rate limited by the provider. Tier 1 has no retry yet. [{detail}]"
    return f"Provider error (HTTP {status}). [{detail}]"
