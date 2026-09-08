"""Running one tool. Extracted from `loop.py` because the tripwire fired.

`04-boundaries-and-layout.md` Rule 4 says the loop gets its own file and should
not grow, with a number attached: past roughly 250 lines, something in it is not
the loop's own mechanism. Adding the between-turns queues in Step 6 took
`loop.py` to 249, and this is what came out.

It is the right thing to have moved. The loop's mechanism is *ask the model, run
what it asked for, repeat*. **How** one tool call becomes one tool result —
looking up the tool, consulting the gate, catching the crash, letting
cancellation through — is a separate mechanism that happens to be called from
there. Splitting it makes both readable, and the loop is back to the shape the
rule describes.

Nothing here is a *decision*. Every policy is still somebody else's function,
reached through `hooks`.
"""

from __future__ import annotations

import asyncio

from omega_agent.hooks import AgentHooks
from omega_agent.tools import Tool, ToolResult
from omega_agent.types import CancellationToken, ToolCall, ToolResultMessage


async def execute_tool_call(
    tool_request: ToolCall,
    tool_by_name: dict[str, Tool],
    hooks: AgentHooks,
    signal: CancellationToken | None,
) -> ToolResultMessage:
    """Run one tool. Never raises.

    Every outcome — success, unknown tool, cancellation, refusal, crash — becomes
    a normal tool result. The model reads failures as observations and adapts,
    which is the entire point of an agent loop. An exception here would end the
    run instead. A *refusal* is in that list for a sharper reason: an unanswered
    tool call is rejected by providers forever, so "no" must still be an answer.
    """
    tool = tool_by_name.get(tool_request.name)
    if tool is None:
        return await _error_result(tool_request, f"Tool {tool_request.name} not found", hooks)

    if signal is not None and signal.is_cancelled():
        return await _error_result(tool_request, "Operation aborted", hooks)

    if hooks.before_tool_call is not None:
        decision = await hooks.before_tool_call(tool_request)
        if not decision.allowed:
            return await _error_result(
                tool_request, decision.reason or "Tool call denied", hooks
            )

    try:
        result = await tool.execute(tool_request.arguments, signal)
    except asyncio.CancelledError:
        # Cancellation must propagate. Swallowing it in the broad except below
        # would make Ctrl-C unreliable — and this is Python, where cancellation
        # arrives as an exception.
        raise
    except Exception as exc:  # noqa: BLE001 - tools are an isolation boundary
        # A tool is third-party code touching the filesystem and the network. If
        # a crashing tool crashed the agent, one bad tool would end every session.
        return await _error_result(tool_request, str(exc) or exc.__class__.__name__, hooks)

    if hooks.after_tool_call is not None:
        result = await hooks.after_tool_call(tool_request, result)

    return ToolResultMessage(
        tool_call_id=tool_request.id,
        tool_name=tool_request.name,
        content=result.content,
        is_error=False,
    )


async def _error_result(
    tool_request: ToolCall, message: str, hooks: AgentHooks
) -> ToolResultMessage:
    """A failure, through the same `after_tool_call` that a success goes through.

    **This closes a real leak, and the reason is worth stating.** An error *is* a
    result: it goes into the transcript, into the next request to the provider,
    and onto disk, exactly as a success does. But `after_tool_call` used to run
    only on the path below, so anything leaving through here skipped it — and
    `run_shell` raises on *any* non-zero exit, carrying the command's whole
    output in the message. The only thing separating a masked result from a
    leaked one was the exit code, which is the wrong thing to depend on: failing
    commands are precisely the ones whose output gets pasted into a bug report.

    So every exit from `execute_tool_call` now passes through the hook, not just
    the one that happens to return a value.

    A hook reached from here cannot be told this was a failure — `ToolResult`
    carries no error flag — which is fine for redaction, a pure text transform,
    and is the limit of what a patch can do. Attaching redaction to "before
    anything is recorded" rather than "after a tool returns" is the real fix, and
    it needs a seam that does not exist yet. See `TIER-2.md`.
    """
    if hooks.after_tool_call is not None:
        # `ToolResult` accepts a bare string and stores the list shape - see its
        # `_wrap_bare_string` validator. The ignore matches every other call site.
        transformed = await hooks.after_tool_call(
            tool_request,
            ToolResult(content=message),  # type: ignore[arg-type]
        )
        message = transformed.text

    return ToolResultMessage(
        tool_call_id=tool_request.id,
        tool_name=tool_request.name,
        content=message,  # type: ignore[arg-type]
        is_error=True,
    )
