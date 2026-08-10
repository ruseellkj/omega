"""The twelve stream events — Layer 1's vocabulary.

This file is the contract between the provider layer and everything above it.
Pi and Tau, written in different languages by different people, converged on
exactly these twelve names and these exact `type` strings. That convergence is
the strongest evidence in either codebase that the vocabulary *is* the
architecture, so the names are copied verbatim rather than improved.

Deliberately a separate module from `types.py`. Tier 2 introduces a second,
coarser vocabulary — the ten *agent* events — which will live in its own file
beside this one. Keeping them apart means you can always tell at a glance which
layer an event belongs to.

Shape of a well-formed stream:

    start
      text_start → text_delta* → text_end
      thinking_start → thinking_delta* → thinking_end
      toolcall_start → toolcall_delta* → toolcall_end
    done | error

Two invariants the provider layer owes upward, enforced in the adapter:

    * exactly one `start`
    * exactly one terminal event — `done` or `error` — even if the vendor
      stream dies silently

Buying those here, once, means the loop never needs a timeout, a sentinel, or a
"what if the stream just stops" branch.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from omega.types import AssistantMessage, ToolCall, WireModel

# Every streaming event carries `partial`: the whole message as it exists so far.
# It costs a copy per event and buys stateless consumers — a renderer can ignore
# the delta entirely and just redraw `partial`.


class AssistantStartEvent(WireModel):
    type: Literal["start"] = "start"
    partial: AssistantMessage


# ------------------------------------------------------------------------ text

class TextStartEvent(WireModel):
    type: Literal["text_start"] = "text_start"
    content_index: int
    partial: AssistantMessage


class TextDeltaEvent(WireModel):
    type: Literal["text_delta"] = "text_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class TextEndEvent(WireModel):
    type: Literal["text_end"] = "text_end"
    content_index: int
    content: str
    partial: AssistantMessage


# -------------------------------------------------------------------- thinking

class ThinkingStartEvent(WireModel):
    type: Literal["thinking_start"] = "thinking_start"
    content_index: int
    partial: AssistantMessage


class ThinkingDeltaEvent(WireModel):
    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class ThinkingEndEvent(WireModel):
    type: Literal["thinking_end"] = "thinking_end"
    content_index: int
    content: str
    partial: AssistantMessage


# ------------------------------------------------------------------- tool calls

class ToolCallStartEvent(WireModel):
    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int
    partial: AssistantMessage


class ToolCallDeltaEvent(WireModel):
    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class ToolCallEndEvent(WireModel):
    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int
    tool_call: ToolCall
    partial: AssistantMessage


# -------------------------------------------------------------------- terminal

DoneReason = Literal["stop", "length", "toolUse"]
ErrorReason = Literal["aborted", "error"]


class AssistantDoneEvent(WireModel):
    type: Literal["done"] = "done"
    reason: DoneReason
    message: AssistantMessage


class AssistantErrorEvent(WireModel):
    """A failure, delivered as data rather than raised.

    A stream that emitted 500 tokens and *then* failed has two things to report.
    An exception carries only the failure and discards the tokens; this carries
    both, because `error` holds the message accumulated so far.
    """

    type: Literal["error"] = "error"
    reason: ErrorReason
    error: AssistantMessage


AssistantMessageEvent = Annotated[
    AssistantStartEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ThinkingStartEvent
    | ThinkingDeltaEvent
    | ThinkingEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | AssistantDoneEvent
    | AssistantErrorEvent,
    Field(discriminator="type"),
]

TERMINAL_EVENT_TYPES = frozenset({"done", "error"})
