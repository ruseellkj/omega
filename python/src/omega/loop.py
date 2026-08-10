"""The agent loop.

Ask the model. If it requested tools, run them and report back. Repeat until it
stops asking. That is the whole idea, and it is why this file is short.

**It should stay short.** If this file is growing, something in it is a *decision*
rather than the *mechanism* — may I run this, should I compact, is this output too
long — and decisions belong to a caller, reached through a callback. Pi's loop is
792 lines inside 109,000; Tau's is 318 inside 36,000. Neither grew as its system did.

Tier 1 note: this yields the twelve *provider* events straight through, because
that is the only vocabulary that exists yet. Tier 2 introduces the ten coarser
*agent* events (`agent_start`, `turn_end`, `tool_execution_start`, …) and this
will emit those instead — which is also what lets a UI show tool activity while
it happens rather than after the fact.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from omega.events import AssistantErrorEvent, AssistantMessageEvent
from omega.provider import ModelProvider
from omega.tools import Tool
from omega.types import (
    AgentMessage,
    AssistantMessage,
    CancellationToken,
    ToolCall,
    ToolResultMessage,
)

DEFAULT_MAX_TURNS = 25


async def run_agent_loop(
    *,
    provider: ModelProvider,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[Tool],
    max_turns: int = DEFAULT_MAX_TURNS,
    signal: CancellationToken | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Run until the model stops requesting tools.

    `messages` is mutated in place: the caller's list *is* the transcript, and it
    grows with each assistant message and tool result. Tau does the same.
    """
    tool_by_name = {tool.name: tool for tool in tools}

    for _turn in range(max_turns):
        assistant: AssistantMessage | None = None

        async for event in provider.stream_response(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            signal=signal,
        ):
            yield event
            # The provider guarantees exactly one terminal event, so exactly one
            # of these fires. Both carry the final message; an error still
            # produces a message, holding whatever arrived before the failure.
            if event.type == "done":
                assistant = event.message
            elif event.type == "error":
                assistant = event.error

        if assistant is None:
            # Defensive: the contract says this cannot happen. If a future
            # adapter breaks it, fail as data rather than crashing the run.
            yield AssistantErrorEvent(
                reason="error",
                error=AssistantMessage(
                    model=model,
                    stop_reason="error",
                    error_message="Provider produced no terminal event",
                ),
            )
            return

        messages.append(assistant)

        if assistant.stop_reason in ("error", "aborted"):
            return

        # Stop on CONTENT, not on stop_reason. A response can carry tool calls
        # while its stop reason says something else, and content is ground truth
        # where a stop reason is provider-reported metadata.
        calls = assistant.tool_calls
        if not calls:
            return

        for call in calls:
            messages.append(await _execute_tool_call(call, tool_by_name, signal))

    # Fell out of the loop: a model that never stops asking would otherwise run
    # until the budget is gone. Reported as an event rather than appended to the
    # transcript — an empty failed assistant turn is not valid model context.
    yield AssistantErrorEvent(
        reason="error",
        error=AssistantMessage(
            model=model,
            stop_reason="error",
            error_message=f"Stopped after max_turns={max_turns}",
        ),
    )


async def _execute_tool_call(
    call: ToolCall,
    tool_by_name: dict[str, Tool],
    signal: CancellationToken | None,
) -> ToolResultMessage:
    """Run one tool. Never raises.

    Every outcome — success, unknown tool, cancellation, crash — becomes a normal
    tool result. The model reads failures as observations and adapts, which is
    the entire point of an agent loop. An exception here would end the run instead.
    """
    tool = tool_by_name.get(call.name)
    if tool is None:
        return _error_result(call, f"Tool {call.name} not found")

    if signal is not None and signal.is_cancelled():
        return _error_result(call, "Operation aborted")

    try:
        result = await tool.execute(call.arguments, signal)
    except asyncio.CancelledError:
        # Cancellation must propagate. Swallowing it in the broad except below
        # would make Ctrl-C unreliable — and this is Python, where cancellation
        # arrives as an exception.
        raise
    except Exception as exc:  # noqa: BLE001 - tools are an isolation boundary
        # A tool is third-party code touching the filesystem and the network. If
        # a crashing tool crashed the agent, one bad tool would end every session.
        return _error_result(call, str(exc) or exc.__class__.__name__)

    return ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=result.content,
        is_error=False,
    )


def _error_result(call: ToolCall, message: str) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=message,  # type: ignore[arg-type]
        is_error=True,
    )
