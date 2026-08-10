"""What a tool is.

A tool is the only way the model can affect anything. It writes a request; this
layer decides whether and how to honour it.

Two decisions here are worth more than they look:

**Tools raise on failure.** They do not return an error flag. Pi states this as a
rule (`packages/agent/src/types.ts`: "Throw on failure instead of encoding errors
in `content`") and it is the better convention because there is exactly one way
to fail — a tool author cannot get it subtly wrong. The loop catches and converts
the exception into a normal tool result the model can read.

**`content` and `details` are separate.** `content` goes to the model and costs
tokens; `details` goes to a UI and costs nothing. Nothing reads `details` at
Tier 1 — it exists so that when a diff renderer arrives it has somewhere to put
a full colourised diff while the model still receives "3 lines changed".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import Field, model_validator

from omega.types import CancellationToken, TextContent, WireModel


class ToolResult(WireModel):
    """What a tool produces when it succeeds."""

    content: list[TextContent] = Field(default_factory=list)
    details: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _wrap_bare_string(cls, value: Any) -> Any:
        """Accept `ToolResult(content="done")` and store the list shape."""
        if isinstance(value, dict) and isinstance(value.get("content"), str):
            value = {**value, "content": [{"type": "text", "text": value["content"]}]}
        return value

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.content)


#: A tool's implementation. Receives validated arguments and a cancellation
#: token it is expected to honour for anything long-running.
ToolExecutor = Callable[[dict[str, Any], CancellationToken | None], Awaitable[ToolResult]]


@dataclass(frozen=True, slots=True)
class Tool:
    """A capability offered to the model.

    `parameters` is a JSON Schema object — the part the model actually reads to
    work out how to call the tool. It is also a real cost: tool schemas are
    re-sent on every single request.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    execute: ToolExecutor


class ToolError(Exception):
    """Raised by a tool when it cannot do what was asked.

    The message becomes the tool result the model reads, so it should explain
    what went wrong in terms the model can act on — and, where there was output
    before the failure, carry that output along with it.
    """
