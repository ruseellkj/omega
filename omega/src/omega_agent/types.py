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


class ImageContent(WireModel):
    """An image, carried as base64 with its real media type.

    **Tier 3, and the test of a Tier 1 decision.** `ContentBlock` was made a
    discriminated union rather than a bare string so that this would be an
    addition instead of a change to every caller. It was.

    `media_type` is detected from the file's **magic bytes**, never from its
    extension (`builtin_tools.read_image`). An extension is a claim and a `.png`
    holding a JPEG is an ordinary mistake; a provider rejecting the request is
    not a good way to find out.

    Base64 rather than a path, because the bytes have to reach the provider and
    a path is meaningless on the other side of an HTTP request.
    """

    type: Literal["image"] = "image"
    media_type: str
    data: str


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

#: What a tool may hand back. Narrower than `ContentBlock` on purpose: a tool
#: cannot produce thinking, and it certainly cannot produce a tool call.
ResultBlock = Annotated[TextContent | ImageContent, Field(discriminator="type")]


# ---------------------------------------------------------------------- usage

class Usage(WireModel):
    """Token accounting. Populated when the provider reports it, zero otherwise.

    The two cache fields are how beginner failure #9 becomes *checkable*. Sending
    a cache marker is easy and proves nothing; `cache_read` above zero on the
    second turn of a session is the only evidence the marker did anything.

    Both default to zero, so a provider that reports nothing — and every session
    written before these existed — stays valid without a schema bump.
    """

    input: int = 0
    output: int = 0
    #: Tokens written into the cache. Billed *above* normal input on Anthropic,
    #: so a run that only ever writes costs more, not less.
    cache_write: int = 0
    #: Tokens served from the cache, at a fraction of the input price. This is
    #: the number that means the feature is working.
    cache_read: int = 0


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

#  a message in the transcript, sent to the API.
class ToolResultMessage(WireModel):
    """The outcome of one tool call, reported back to the model.

    Every `ToolCall` must be answered by exactly one of these. A conversation
    containing an unanswered tool call is rejected outright by providers.
    """

    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str
    tool_name: str
    content: list[ResultBlock] = Field(default_factory=list)
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
        """The prose only. **Images contribute nothing.**

        `.text` is read by the cost meter, the event log, redaction and both
        adapters. Letting a megabyte of base64 through here would put it in the
        log, through the redaction patterns, and into the token estimate — three
        places that all want words.
        """
        return "\n".join(
            block.text for block in self.content if isinstance(block, TextContent)
        )

    @property
    def images(self) -> list[ImageContent]:
        """The image blocks, for the adapters that have to place them."""
        return [block for block in self.content if isinstance(block, ImageContent)]


AgentMessage = Annotated[
    UserMessage | AssistantMessage | ToolResultMessage,
    Field(discriminator="role"),
]
