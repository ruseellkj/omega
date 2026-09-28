"""Two views of history — anatomy.md #20.

What you *see* and what gets *sent* are not the same list. Tier 1 conflated them:
`messages` was both the transcript and the request payload, so anything unusable
by a provider had to be either kept and sent, or thrown away entirely.

The distinction earns its keep immediately. A turn that failed mid-stream leaves
an assistant message with no content and a `stop_reason` of `error`. It belongs
in the transcript — you asked something and it broke, and hiding that makes the
session a lie — but sending it back is at best noise and at worst a rejected
request.

**This is the simpler sibling of the seam compaction uses at Tier 3.** Same idea,
same signature: take what is kept, return what to send. `transform_context` is
where compaction goes; `convert_to_llm` is where this five-line version goes.
That both exist now, with the smaller one doing real work, is what makes the
larger one an addition later rather than a redesign.
"""

from __future__ import annotations

from omega_agent.types import AgentMessage, AssistantMessage


def _is_empty_failure(message: AgentMessage) -> bool:
    """A turn that failed before producing anything.

    The `not content` check is the important half. A turn that streamed 500
    tokens and *then* failed still carries those tokens, and they are real
    context the model should keep — dropping them would discard work that was
    actually done. Only the genuinely empty failures go.
    """
    return (
        isinstance(message, AssistantMessage)
        and message.stop_reason in {"error", "aborted"}
        and not message.content
    )


async def drop_empty_failed_turns(messages: list[AgentMessage]) -> list[AgentMessage]:
    """`convert_to_llm`: keep everything, send everything worth sending."""
    return [message for message in messages if not _is_empty_failure(message)]
