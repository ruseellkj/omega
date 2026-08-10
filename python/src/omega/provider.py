"""The provider contract.

**This file is the whole point of Tier 1.**

Note where it lives: in the core package, next to the loop — not inside
`providers/`. The consumer defines the interface and adapters conform to it.
Reverse that, and Anthropic's shape becomes the shape of the system; adding a
second provider would then mean editing the loop, which is exactly the failure
this layer exists to prevent.

One method. In: which model, the standing instructions, the conversation, the
available tools, and a way to stop. Out: a stream of neutral events.

Everything a provider must guarantee is listed in `events.py`. The interface
itself stays this small on purpose — if a fake implementation of it is hard to
write, the interface is too big. `providers/fake.py` is that check.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from omega.events import AssistantMessageEvent
from omega.tools import Tool
from omega.types import AgentMessage, CancellationToken

__all__ = ["CancellationToken", "ModelProvider"]


class ModelProvider(Protocol):
    """Provider-neutral model stream.

    A `Protocol`, so an adapter satisfies this by having the right method —
    no base class, no import of this module required. `FakeProvider` inherits
    from nothing and still type-checks as a `ModelProvider`.

    Deliberately *not* `async def`: the method returns an async iterator rather
    than awaiting one, so callers write

        async for event in provider.stream_response(...)

    without an intervening await.
    """

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[Tool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        """Stream one model response as neutral events."""
        ...
