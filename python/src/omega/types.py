"""The neutral message model.

Every provider translates its own wire format into these types on the way in and
out. Nothing above the provider layer ever sees a vendor-shaped object — that is
the entire point of the layer, and the reason adding a second provider later does
not touch the loop.

Shapes follow Tau's `tau_agent/messages.py`, which in turn follows Pi's
`packages/ai/src/types.ts`. Where two independent implementations agree, that
agreement is treated as the architecture rather than a coincidence.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


@runtime_checkable
class CancellationToken(Protocol):
    """A way to ask "should I stop?" without knowing who decides.

    Lives here rather than in `provider.py` because both the provider layer and
    the tool layer need it, and putting it in either would create an import
    cycle with the other.

    Tier 1 threads this parameter through every layer but never sets it — the
    seam exists so Tier 2 can wire Ctrl-C to it without changing a signature.
    """

    def is_cancelled(self) -> bool:
        """Return whether the current operation should stop."""
        ...


class WireModel(BaseModel):
    """Base for anything that crosses a layer boundary.

    `extra="forbid"` so a provider that invents a field fails loudly here rather
    than silently carrying vendor data upward.
    """

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------- content blocks

class TextContent(WireModel):
    """Visible prose from the model."""

    type: Literal["text"] = "text"
    text: str = ""


class ThinkingContent(WireModel):
    """Reasoning the model emitted separately from its answer.

    `signature` is an opaque provider token. It carries no meaning to us, but it
    must be handed back verbatim on the next turn or multi-turn reasoning breaks
    — so it is preserved rather than dropped.
    """

    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    signature: str | None = None


class ToolCall(WireModel):
    """The model asking for a tool to be run. It cannot run anything itself."""

    type: Literal["toolCall"] = "toolCall"
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


ContentBlock = Annotated[
    TextContent | ThinkingContent | ToolCall,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------- usage

class Usage(WireModel):
    """Token accounting. Populated when the provider reports it, zero otherwise."""

    input: int = 0
    output: int = 0


# ------------------------------------------------------------------ stop reason

# Normalised across providers. Vendors spell these at least six different ways;
# translating them is the adapter's job so that nothing above has to care.
StopReason = Literal["pending", "stop", "length", "toolUse", "error", "aborted"]


# -------------------------------------------------------------------- messages

class UserMessage(WireModel):
    role: Literal["user"] = "user"
    content: str


class AssistantMessage(WireModel):
    """One model response. Content is an ordered list of blocks, not a string —
    a single reply can interleave prose, reasoning, and tool calls."""

    role: Literal["assistant"] = "assistant"
    content: list[ContentBlock] = Field(default_factory=list)
    model: str = ""
    stop_reason: StopReason = "pending"
    error_message: str | None = None
    usage: Usage = Field(default_factory=Usage)

    @property
    def tool_calls(self) -> list[ToolCall]:
        """The tool calls in this message.

        The loop's stop condition reads this, *not* `stop_reason`. Content is
        ground truth; a stop reason is provider-reported metadata that the
        adapter already had to normalise.
        """
        return [block for block in self.content if isinstance(block, ToolCall)]

    @property
    def text(self) -> str:
        """All visible prose, concatenated. Excludes thinking and tool calls."""
        return "\n".join(
            block.text for block in self.content if isinstance(block, TextContent)
        )


class ToolResultMessage(WireModel):
    """The outcome of one tool call, reported back to the model.

    Every `ToolCall` must be answered by exactly one of these. A conversation
    containing an unanswered tool call is rejected outright by providers.
    """

    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str
    tool_name: str
    content: list[TextContent] = Field(default_factory=list)
    is_error: bool = False

    @model_validator(mode="before")
    @classmethod
    def _wrap_bare_string(cls, value: Any) -> Any:
        """Accept `content="text"` and store it as `[TextContent(text="text")]`.

        Keeps call sites readable while the stored shape stays a list, so adding
        image results later is an addition rather than a change to every caller.
        """
        if isinstance(value, dict) and isinstance(value.get("content"), str):
            value = {**value, "content": [{"type": "text", "text": value["content"]}]}
        return value

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.content)


AgentMessage = Annotated[
    UserMessage | AssistantMessage | ToolResultMessage,
    Field(discriminator="role"),
]
