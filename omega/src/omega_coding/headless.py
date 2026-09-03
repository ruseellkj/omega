"""Run the agent without a keyboard — anatomy.md #35.

A prompt goes in, the finished transcript comes out. No REPL, no prompting, no
terminal.

`anatomy.md:400` makes the case for building it now rather than later: **this is
also the terminal-bench interface.** Build it once, use it twice. Anything that
wants to drive omega programmatically — an eval, a benchmark, a subagent at
Tier 3+ — wants exactly this function, and a subagent *is* this function called
from inside a tool.

The one thing worth being careful about is approvals. Headless means nobody is
watching, and `ApprovalPolicy` with no asker denies by default for exactly that
reason. So a caller has to say `approve=True` explicitly. It is a keyword with no
default because "the tools silently did nothing" is a miserable way to discover
you forgot.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from omega_agent.agent_events import AgentEndReason, AgentEvent
from omega_agent.cancellation import CancelSignal
from omega_agent.harness import Harness
from omega_agent.hooks import AgentHooks
from omega_agent.loop import DEFAULT_MAX_TURNS
from omega_agent.provider import ModelProvider
from omega_agent.session import SessionStore
from omega_agent.tools import Tool
from omega_agent.types import AgentMessage, AssistantMessage
from omega_coding.approval import ApprovalPolicy


@dataclass(frozen=True, slots=True)
class HeadlessResult:
    """Everything a caller could want, and nothing it has to reconstruct."""

    messages: list[AgentMessage] = field(default_factory=list)
    events: list[AgentEvent] = field(default_factory=list)
    reason: AgentEndReason = "stop"
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        """`stop` is the only success. The other three all mean unfinished."""
        return self.reason == "stop"

    @property
    def text(self) -> str:
        """What the model said last. The answer, for anything that wants one."""
        for message in reversed(self.messages):
            if isinstance(message, AssistantMessage) and message.text:
                return message.text
        return ""

    @property
    def tool_names(self) -> list[str]:
        """Which tools ran, in order. What an eval usually asserts on."""
        return [
            event.tool_call.name for event in self.events if event.type == "tool_execution_start"
        ]


async def run_headless(
    *,
    provider: ModelProvider,
    model: str,
    system: str,
    prompt: str,
    tools: list[Tool],
    hooks: AgentHooks | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    signal: CancelSignal | None = None,
    store: SessionStore | None = None,
    approve: bool,
    root: Path | None = None,
) -> HeadlessResult:
    """One prompt, run to completion, with no interaction.

    `approve` has no default on purpose. Nobody is watching, so the choice
    between "tools may act" and "tools are refused" has to be made out loud.
    """
    base = hooks if hooks is not None else AgentHooks()
    if base.before_tool_call is None:
        # `root` only decides which paths count as "outside" for the gate. With
        # `approve=True` nothing is asked anyway; with `approve=False` and no
        # asker, everything but an inside-root read is refused either way. It
        # still gets passed rather than defaulted inside the policy, so the one
        # place that knows the working directory is the caller.
        base = replace(
            base,
            before_tool_call=ApprovalPolicy(
                root if root is not None else Path.cwd(), auto_approve=approve
            ),
        )

    harness = Harness(
        provider=provider,
        model=model,
        system=system,
        tools=tools,
        hooks=base,
        max_turns=max_turns,
        signal=signal,
        store=store,
    )

    events: list[AgentEvent] = []
    reason: AgentEndReason = "error"
    error_message: str | None = "run produced no agent_end"

    async for event in harness.run(prompt):
        events.append(event)
        if event.type == "agent_end":
            reason = event.reason
            error_message = event.error_message

    return HeadlessResult(
        messages=list(harness.messages),
        events=events,
        reason=reason,
        error_message=error_message,
    )
