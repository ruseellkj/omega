"""How full is the context window — anatomy.md #22.

This **measures** beginner failure #1; it does not fix it. Compaction is Tier 3.
What this buys now is the ability to watch the wall approach instead of hitting
it, which is also what makes the Tier 3 work possible to tune.

**A cheap estimate, not a token count.** `anatomy.md:282` is explicit that exact
counts are not the goal: "You don't need exact counts, just a cheap estimate
that never wildly under-reports." Characters divided by four is that estimate.
Calling a real tokenizer would mean a dependency, per-model vocabularies, and
milliseconds on every turn, to improve a number that only ever drives a
threshold.

The part people forget is **tool schemas**. They are re-sent on every single
request and they are not small — four tools with descriptions and JSON Schema is
easily a thousand tokens of every turn. An estimator that measures only messages
will under-report by a constant, which is the one direction that matters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from omega_agent.tools import Tool
from omega_agent.types import AgentMessage, AssistantMessage, ToolResultMessage, UserMessage
from omega_coding import models

#: The classic rule of thumb for English and code. Wrong in both directions for
#: any given string, close enough over a whole conversation.
CHARS_PER_TOKEN = 4

#: Re-exported so callers that only care about accounting keep importing it from
#: here. The table itself moved to `models.py` when `/model` needed to answer
#: "what else could I switch to?" — the same facts, asked a second way, and two
#: copies of a window figure is how a compactor ends up budgeting against a
#: number the provider disagrees with.
DEFAULT_CONTEXT_WINDOW = models.DEFAULT_CONTEXT_WINDOW


def window_for(model: str) -> int:
    """The context window for a model, or a conservative default."""
    return models.window_for(model)


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def _message_text(message: AgentMessage) -> str:
    """Everything in a message that costs tokens.

    Tool call *arguments* are included: a call carrying a whole file's contents
    as a `content` argument is expensive, and it is exactly the kind of message
    that fills a window.
    """
    if isinstance(message, UserMessage):
        return message.content
    if isinstance(message, ToolResultMessage):
        return message.text
    if isinstance(message, AssistantMessage):
        parts = [message.text]
        parts.extend(json.dumps(call.arguments) for call in message.tool_calls)
        return "\n".join(parts)
    return ""


def estimate_parts(
    *, system: str, messages: list[AgentMessage], tools: list[Tool]
) -> tuple[int, int, int]:
    """`(system, messages, tools)` — the three things a request is made of.

    **The split was always computed and then thrown away.** The old version of
    this summed the same three quantities into one `total` and returned it, so
    "how full am I" could be answered but "full *of what*" could not — which is
    the question that tells you what to do about it. A large `tools` slice means
    trim schemas; a large `messages` slice means compact.

    Returned as a tuple rather than three calls so the three numbers cannot be
    measured against different inputs, which is how a breakdown ends up not
    adding to its own total.
    """
    system_tokens = estimate_tokens(system)
    message_tokens = sum(estimate_tokens(_message_text(message)) for message in messages)
    tool_tokens = 0
    for tool in tools:
        tool_tokens += estimate_tokens(tool.name)
        tool_tokens += estimate_tokens(tool.description)
        tool_tokens += estimate_tokens(json.dumps(tool.parameters))
    return system_tokens, message_tokens, tool_tokens


def estimate_request_tokens(
    *, system: str, messages: list[AgentMessage], tools: list[Tool]
) -> int:
    """What the next request will roughly cost, schemas included."""
    return sum(estimate_parts(system=system, messages=messages, tools=tools))


@dataclass(frozen=True, slots=True)
class ContextUsage:
    """A reading, ready to render.

    **`estimated_tokens` is a property, not a field, and that is the point.**
    Holding the total alongside its three parts means holding the same fact
    twice, and two copies of a number is how a breakdown comes to disagree with
    the total printed directly above it. Here the total is *defined* as the sum,
    so they cannot drift.
    """

    system_tokens: int
    message_tokens: int
    tool_tokens: int
    window: int
    #: Whether `window` came from a catalog entry or from the fallback. Carried
    #: because the two render identically otherwise - see `parts()`.
    window_is_known: bool = True

    @property
    def estimated_tokens(self) -> int:
        return self.system_tokens + self.message_tokens + self.tool_tokens

    @property
    def fraction(self) -> float:
        if self.window <= 0:
            return 0.0
        return self.estimated_tokens / self.window

    @property
    def percent(self) -> int:
        return int(self.fraction * 100)

    def parts(self) -> list[str]:
        """The breakdown, one line each, widest slice first.

        Ordered by size rather than by name because the reason to read this is
        to find out what to trim, and the answer is whatever is at the top.

        **A zero slice is still printed.** `tools: 0` is information - it says
        the estimate is not quietly missing the schemas, which is the one
        direction this estimator must never under-report in
        (see the module docstring).
        """
        rows = [
            ("messages", self.message_tokens),
            ("tools", self.tool_tokens),
            ("system", self.system_tokens),
        ]
        rows.sort(key=lambda row: row[1], reverse=True)
        width = max(len(name) for name, _ in rows)
        lines = [f"{name:<{width}}  {count:>9,}" for name, count in rows]
        free = self.window - self.estimated_tokens
        lines.append(f"{'free':<{width}}  {free:>9,}")
        return lines

    def window_note(self) -> str | None:
        """Why the window is what it is, when that needs saying.

        `None` for a window omega has an entry for - there is nothing to
        explain. A sentence when it is the fallback, because `~0/200,000` reads
        exactly the same whether that figure is the provider's or omega's
        stand-in, and the reader has no way to tell which they are looking at.
        """
        if self.window_is_known:
            return None
        return (
            "The window is a fallback, not this model's real figure - omega has"
            " no entry for it. Put the number in ~/.omega/models.json to fix it."
        )

    def __str__(self) -> str:
        return f"~{self.estimated_tokens:,}/{self.window:,} tokens ({self.percent}%)"


def measure(
    *, model: str, system: str, messages: list[AgentMessage], tools: list[Tool]
) -> ContextUsage:
    system_tokens, message_tokens, tool_tokens = estimate_parts(
        system=system, messages=messages, tools=tools
    )
    return ContextUsage(
        system_tokens=system_tokens,
        message_tokens=message_tokens,
        tool_tokens=tool_tokens,
        window=window_for(model),
        window_is_known=not models.window_is_guess(model),
    )
