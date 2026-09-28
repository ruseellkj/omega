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
from typing import Any

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

    Every outcome — success, unknown tool, cancellation, missing arguments,
    refusal, crash — becomes a normal tool result. The model reads failures as
    observations and adapts,
    which is the entire point of an agent loop. An exception here would end the
    run instead. A *refusal* is in that list for a sharper reason: an unanswered
    tool call is rejected by providers forever, so "no" must still be an answer.
    """
    tool = tool_by_name.get(tool_request.name)
    if tool is None:
        return await _error_result(tool_request, f"Tool {tool_request.name} not found", hooks)

    if signal is not None and signal.is_cancelled():
        return await _error_result(tool_request, "Operation aborted", hooks)

    missing = _missing_required(tool, tool_request.arguments)
    if missing:
        message = _missing_message(tool_request.name, missing)
        return await _error_result(tool_request, message, hooks)

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


def _missing_required(tool: Tool, arguments: dict[str, Any]) -> list[str]:
    """The arguments the tool's own schema calls required, and this call lacks.

    **Why this is here, in the core.** `required` is part of the `Tool` contract
    this package defines, so checking it is mechanism, not policy — nothing here
    knows what a path or a file is. Without it, a tool indexed the missing key
    and the model was handed the KeyError's text: for `write_file`, the single
    word `'path'`.

    **The case it exists for is a reply cut off mid-call.** When the output limit
    lands inside a tool call's arguments, the JSON is broken and every adapter
    parses it to `{}` — never to a half-filled dict, because truncated JSON does
    not parse at all. The loop still runs the call, because it follows content.
    So the likeliest cause of a missing argument is the model's own oversized
    reply — and the message says so, because the natural retry otherwise is the
    same call again.

    **Only `required`, not the whole schema.** Full JSON Schema validation, as Pi
    does (`agent-loop.ts:617-618`, `validateToolArguments`, also before its
    `beforeToolCall`), would need a validator, and a wrong *type* already fails
    inside the tool with a message that names it. An absent argument is the
    failure that produced no useful message at all.
    """
    required = tool.parameters.get("required")
    if not isinstance(required, list):
        return []
    return [name for name in required if isinstance(name, str) and name not in arguments]


def _missing_message(name: str, missing: list[str]) -> str:
    listed = ", ".join(repr(argument) for argument in missing)
    noun = "argument" if len(missing) == 1 else "arguments"
    return (
        f"{name} was called without its required {noun} {listed}, so it was not run. "
        "The arguments arrived empty or incomplete - most often because the reply was "
        "cut off at the output limit while writing them. Retry with a smaller call; "
        "if the content was large, split it across several calls."
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
