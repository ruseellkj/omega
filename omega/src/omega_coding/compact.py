"""Compaction — beginner failure #1, and the first thing Tier 3 closes.

Tier 2 could only *watch* the window fill. `context.py` measured it and `/context`
printed it, and then the run died at the limit anyway. This is the thing that
moves the wall.

## Where it plugs in

`transform_context` (`hooks.py:93`), which has existed and been empty since Tier
2. `loop.py` consults it through `_context_for_request`, after `convert_to_llm`:
normalise the transcript, *then* decide how much of it to send. The loop gains
zero lines from this file, which was the test the seam was built to pass.

The signature is `list[AgentMessage] -> list[AgentMessage]` and receives neither
the model, the system prompt, nor the tools — all three of which are needed to
know what "too big" means. So this is a **class that closes over them at
construction**, the same shape `ApprovalPolicy` uses for the same reason.
Widening the hook signature was the alternative, and it would have touched
`hooks.py`, `loop.py` and `history.py` to spare one constructor.

## The two rules it cannot break

**1 · Never orphan a tool call.** An `AssistantMessage` carrying a `ToolCall` and
the `ToolResultMessage` answering it are one indivisible unit. Providers reject a
conversation containing an unanswered call — not once, but on every subsequent
request. `repair_orphans` exists to undo that when an interrupt causes it; a
compactor causing it would be worse, because the transcript would be *fine* and
the request broken every single time, so no repair would ever fire. Units are
therefore the atom this file cuts on, and `_units()` is deliberately the same
grouping `repair_orphans` walks.

**2 · Never touch the transcript.** What is *kept* and what is *sent* are two
lists (`history.py`). Every message here is either passed through by reference or
rebuilt with `model_copy`; nothing is mutated in place. That is what makes a bug
in this file a bad request rather than lost work.

## Why it does not call the model

The obvious compaction asks the model to summarise the middle of the
conversation. Pi does exactly that, in a subsystem of ~880 lines. This does not,
yet, for one reason that outweighs fidelity: **a summarising compactor cannot be
tested offline.** omega's tests take three seconds and touch no network, and that
property has caught more real bugs than a better summary would have prevented.

What is here instead is mechanical and exact: drop the oldest whole turns, then
shrink what remains. It is worth knowing where the bytes actually are — a tool
result holding a file is orders of magnitude larger than the assistant text
around it, so dropping old *results* recovers most of the window without a single
token spent. Model-based summarisation is an addition on top of this, not a
replacement for it, and it goes in the same seam.

## When it cannot succeed, and why that is the right answer

Compaction is not guaranteed to fit. Fuzzing 3,000 random transcripts against
windows from 500 to 20,000 tokens left **93 still over budget, and every one for
one of two deliberate reasons**:

* **The user's own messages exceed the budget** (74 cases). They are never cut.
  A tool result can be re-read and an explanation re-generated; the instruction
  exists nowhere else. An agent that half-remembers what it was asked is worse
  than one that admits the window is full.
* **`MIN_RESULT_TOKENS` multiplied out** (19 cases). Four results cannot shrink
  below four floors, and on a 400-token budget that arithmetic simply does not
  close. Overshoot in these was 2 tokens.

The same run found **zero** invariant violations — no orphaned calls, no
reordering, no mutation of the caller's list, and identical output on a repeat
call. That is the trade this file makes on purpose: **validity is absolute, size
is best-effort.** A slightly oversized request may be rejected by the provider;
an invalid one certainly will be, on every subsequent turn, forever.

## Determinism is a requirement, not a nicety

`transform_context` runs on **every loop iteration**, not once per turn. A
compactor that carried state, or varied its output, would produce a different
prefix on iteration 2 than iteration 1 of the same turn — which is precisely what
prompt caching (failure #9, the other half of Tier 3) cannot tolerate, since
cache markers need a byte-identical prefix. Every function here is pure in its
input.

**Pure was not enough, and that was measured.** Purity makes one turn agree with
itself. It says nothing about the *next* turn, whose input is one unit longer.
Dropping exactly the minimum moved the cut every turn, and the elision note's
count with it, so consecutive requests shared only their first message and
Anthropic re-wrote nearly the whole request to its cache each turn, at 1.25x the
price of plain input. A compacted turn's messages cost about 125% of sending
them uncached.

So the automatic pass drops history in **steps** (`STEP_FRACTION`). It rounds the
drop up to a whole number of steps and cuts at the first unit boundary past
that. Units that are already old never change, so the same rounding lands on the
same boundary turn after turn, and each request is the previous one plus the new
turn until the drop crosses the next step. Still pure: the cut is a function of
the input alone, with no memory of where the last one was.
"""

from __future__ import annotations

from omega_agent.tools import Tool
from omega_agent.types import (
    AgentMessage,
    AssistantMessage,
    ContentBlock,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from omega_coding.context import CHARS_PER_TOKEN, estimate_request_tokens, window_for

#: Compact at 80% rather than 100%. The estimate is characters/4 and is allowed
#: to be wrong; the reply still has to fit; and arriving at the limit with no room
#: to compact *in* is the failure this exists to prevent.
DEFAULT_THRESHOLD = 0.8

#: The automatic pass drops history in chunks of about this much of the window.
#:
#: **Why a step at all:** a cut that moves every turn breaks the cached prefix
#: every turn (see "Pure was not enough" above). A step holds the cut still for
#: about `step / unit size` turns. At 200,000 tokens a step is 60,000, which is
#: about 56 turns that each read a 4,200-character file.
#:
#: **Why 30%:** after a step the request falls to about 50% of the window at the
#: default 80% threshold, so it has room to grow for many turns before the next
#: step, and it still keeps more than half of what fits. A smaller step moves the
#: cut more often; a larger one forgets more at once.
#:
#: **What it costs.** Each step drops more than the minimum, so a compacted
#: request carries less history than the tightest fit would. And it helps only
#: when a step spans several units: when one turn is larger than a step, the cut
#: moves nearly every turn anyway and the extra drop buys nothing. It is a
#: fraction of the window, not of the budget, so with a `--compact-threshold` not
#: far above it one step can take most of what fits.
STEP_FRACTION = 0.3

#: A result is never shrunk below this. Small enough not to matter, large enough
#: that what survives says what happened rather than being an empty string.
MIN_RESULT_TOKENS = 64

#: Marks both kinds of elision, so the model can tell "this was cut" from "this
#: is what the tool said". Greppable on purpose.
MARK = "[compacted]"


def _elision_note(turns: int) -> str:
    """What the model is told about the gap.

    Said out loud, because a silent hole invites invention. `truncate.py` reaches
    the same conclusion for tool output — *truncation is not data loss if you say
    what you dropped* — and the reasoning is identical one level up.
    """
    plural = "turn" if turns == 1 else "turns"
    return (
        f"{MARK} {turns} earlier {plural} were removed from this request to stay "
        f"within the context window. They are still in the session transcript and "
        f"can be re-read from the session file if their detail matters."
    )


def _units(messages: list[AgentMessage]) -> list[list[AgentMessage]]:
    """Group into the smallest pieces that are still valid on their own.

    An assistant message with tool calls takes the results that follow it. This
    is the same walk `harness.repair_orphans` does, for the same reason: that
    pairing is the thing providers validate.
    """
    units: list[list[AgentMessage]] = []
    index = 0

    while index < len(messages):
        message = messages[index]
        unit: list[AgentMessage] = [message]
        index += 1

        if isinstance(message, AssistantMessage) and message.tool_calls:
            while index < len(messages) and isinstance(messages[index], ToolResultMessage):
                unit.append(messages[index])
                index += 1

        units.append(unit)

    return units


def _cut(text: str, keep: int) -> str:
    """Head *and* tail of `text`, with the gap named in the middle.

    Both ends, because they carry different things: a file starts with imports
    and a definition, a failing command ends with the error. Keeping only one end
    reliably loses whichever mattered.
    """
    note = f"\n\n{MARK} {len(text) - keep:,} characters elided\n\n"
    half = (keep - len(note)) // 2
    return note.strip() if half <= 0 else text[:half] + note + text[-half:]


def _excerpt(message: ToolResultMessage, max_tokens: int) -> ToolResultMessage:
    """A smaller copy of one tool result. **A copy** — the original is untouched.

    Tool output is the first thing to cut because it is the only thing that can
    be *recovered*: the model can run the tool again. Prose cannot be re-derived.
    """
    text = message.text
    keep = max_tokens * CHARS_PER_TOKEN
    if len(text) <= keep:
        return message

    # model_copy rather than mutation: this message is still in the caller's
    # transcript, and editing it there would quietly destroy the two-views split.
    return message.model_copy(update={"content": [TextContent(text=_cut(text, keep))]})


def _trim_prose(message: AssistantMessage, max_tokens: int) -> AssistantMessage:
    """Shrink only the *text* the model wrote. Everything else passes through.

    Two block types are untouchable and the reasons differ:

    * **`ToolCall`** — it anchors the pairing. Shrinking or dropping one orphans
      the result that answers it, which is the permanent failure this whole file
      is arranged to avoid.
    * **`ThinkingContent`** — `types.py:59-61` is explicit that `signature` "must
      be handed back verbatim on the next turn or multi-turn reasoning breaks".
      An opaque token is not something to excerpt.
    """
    keep = max_tokens * CHARS_PER_TOKEN
    blocks: list[ContentBlock] = []
    changed = False

    for block in message.content:
        if isinstance(block, TextContent) and len(block.text) > keep:
            blocks.append(TextContent(text=_cut(block.text, keep)))
            changed = True
        else:
            blocks.append(block)

    return message.model_copy(update={"content": blocks}) if changed else message


def _stepped(costs: list[int], minimum: int, step: int) -> int:
    """How many of the oldest units to drop, rounded up to whole steps.

    `minimum` is the fewest that must go for the rest to fit. The tokens those
    hold are rounded **up** to a multiple of `step`, and the cut goes at the
    first unit boundary at or past that amount. Up, so the result still fits:
    it can only drop more than the minimum, never less.

    **Why this holds the cut still.** `costs` for units already in the
    transcript are the same every turn, so for a given rounded amount the
    boundary is the same every turn too. That amount changes only when the
    minimum crosses the next multiple of `step`, and until then the request is
    the previous one plus the new turn.

    The newest unit is never dropped, whatever the rounding says, for the reason
    `_fit` keeps it: a request without what the model just did is useless.
    """
    target = -(-sum(costs[:minimum]) // step) * step  # ceiling, in whole steps
    dropped, total = 0, 0
    while dropped < len(costs) - 1 and total < target:
        total += costs[dropped]
        dropped += 1
    return max(dropped, minimum)


class Compactor:
    """A `transform_context` hook that keeps a request under the window.

    Callable, so it *is* the hook — `AgentHooks` takes any callable of the right
    shape. Holds no mutable state; `__call__` is a pure function of its argument.
    """

    def __init__(
        self,
        *,
        model: str,
        system: str,
        tools: list[Tool],
        window: int | None = None,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        #: An explicit window beats the model's, so a caller can hold a smaller
        #: context deliberately — and so tests do not need 200,000-token fixtures.
        self.window = window if window is not None else window_for(model)
        self.threshold = threshold

        #: Charged once at construction. The system prompt and the tool schemas
        #: are re-sent on every request and cannot be compacted, so they come out
        #: of the window before messages get any of it.
        self._overhead = estimate_request_tokens(system=system, messages=[], tools=tools)

    def budget_for(self, fraction: float) -> int:
        """Tokens the *messages* may occupy at `fraction` of the window.

        A parameter rather than a constant because `/compact` lets you aim lower
        on purpose — "get me to 40% before I start something big" is a different
        request from "keep me under the automatic ceiling".
        """
        return max(0, int(self.window * fraction) - self._overhead)

    @property
    def budget(self) -> int:
        """The automatic ceiling: what `__call__` holds the context under."""
        return self.budget_for(self.threshold)

    def estimate(self, messages: list[AgentMessage]) -> int:
        """What these messages cost, on the same scale as `budget`."""
        return estimate_request_tokens(system="", messages=messages, tools=[])

    async def __call__(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        """Return what to send. The argument is never modified."""
        if self.estimate(messages) <= self.budget:
            # The common case, and it must stay free: compaction that fires when
            # it is not needed is lost fidelity for nothing.
            return list(messages)
        # Read at call time, not construction: `/model` can change the window.
        return self._fit(list(messages), self.budget, step=int(self.window * STEP_FRACTION))

    def compact_to(self, messages: list[AgentMessage], fraction: float) -> list[AgentMessage]:
        """Compact to an explicit fraction of the window. What `/compact` calls.

        Unconditional, unlike `__call__`: a manual command that quietly did
        nothing because you happened to be under the ceiling would be worse than
        no command. Whether anything actually changed is the caller's to report.

        **Exact, with no step.** It runs once, on purpose, to a target someone
        chose, and its result replaces the transcript. There is no next request
        whose prefix it has to match, so rounding the drop up would only forget
        more than was asked.
        """
        return self._fit(list(messages), self.budget_for(fraction))

    def _fit(
        self, messages: list[AgentMessage], budget: int, *, step: int = 0
    ) -> list[AgentMessage]:
        """The algorithm.

        `budget` is passed rather than read off `self`, so the automatic ceiling
        and a manual target share one implementation. Two code paths that could
        disagree about what "compacted" means would be one bug waiting.

        `step` is the one place they differ, and it is a parameter for the same
        reason. `0` drops the minimum that fits, which is what `/compact` wants.
        Above zero, the drop is rounded up to whole steps (`_stepped`), which is
        what keeps the automatic pass's cut still from one turn to the next.
        """
        units = _units(messages)
        if len(units) <= 1:
            return self._shrink(messages, budget)

        head, rest = units[0], units[1:]

        # Reserve what is not negotiable before spending anything on history: the
        # opening turn, and the note explaining the gap. Measured with a
        # representative count so the reserve does not depend on the answer.
        reserve = self.estimate(head)
        reserve += self.estimate([UserMessage(content=_elision_note(len(rest)))])

        # Every unit's cost, oldest first. A unit that is already in the
        # transcript never changes, so these are the same numbers every turn,
        # which is what lets a stepped cut land in the same place.
        costs = [self.estimate(unit) for unit in rest]

        kept = 0
        used = 0
        for cost in reversed(costs):
            # `and kept` keeps the newest turn whatever it costs. A request
            # missing what the model just did is useless; an oversized one is
            # merely expensive, and `_shrink` still has a say.
            if used + cost + reserve > budget and kept:
                break
            kept += 1
            used += cost

        dropped = len(rest) - kept
        if step > 0 and dropped:
            dropped = _stepped(costs, dropped, step)

        sent: list[AgentMessage] = list(head)
        if dropped:
            sent.append(UserMessage(content=_elision_note(dropped)))
        for unit in rest[dropped:]:
            sent.extend(unit)

        # Dropping whole turns is not always enough — one turn can exceed the
        # whole budget on its own. Shrinking cannot orphan anything, because it
        # changes content and never the shape.
        if self.estimate(sent) > budget:
            sent = self._shrink(sent, budget)
        return sent

    def _shrink(self, messages: list[AgentMessage], budget: int) -> list[AgentMessage]:
        """Cut content, keeping every message in place.

        Shrinking is the safe half of compaction: the list keeps its length and
        its pairing, so no request can be invalidated by it. Only the bytes move.

        **Cheapest loss first.** Tool output goes before prose because it is the
        only thing that can be recovered — the model can run the tool again. The
        user's own messages are never cut at all: a tool result can be re-read and
        an explanation re-generated, but the instruction exists nowhere else, and
        an agent that half-remembers what it was asked is worse than one that
        admits the window is full.
        """
        shrunk = self._shrink_results(messages, budget)
        if self.estimate(shrunk) <= budget:
            return shrunk
        return self._trim_prose(shrunk, budget)

    def _shrink_results(self, messages: list[AgentMessage], budget: int) -> list[AgentMessage]:
        """Pass one: excerpt tool results."""
        positions = [i for i, m in enumerate(messages) if isinstance(m, ToolResultMessage)]
        if not positions:
            return messages

        fixed = sum(
            self.estimate([m]) for m in messages if not isinstance(m, ToolResultMessage)
        )
        share = max(MIN_RESULT_TOKENS, (budget - fixed) // len(positions))

        shrunk = list(messages)
        for position in positions:
            result = messages[position]
            assert isinstance(result, ToolResultMessage)  # narrowed by `positions`
            shrunk[position] = _excerpt(result, share)
        return shrunk

    def _trim_prose(self, messages: list[AgentMessage], budget: int) -> list[AgentMessage]:
        """Pass two: excerpt assistant prose.

        Reached only when cutting tool output was not enough — a chatty model, or
        a single answer larger than the whole budget. **Found by fuzzing**: 3,000
        random transcripts left 257 over budget, and every one of them had no tool
        results to cut, so pass one could do nothing. (`TIER-3.md` says 279. Both
        were written in `fc88a8f`, the fuzz script was not kept, so neither can be
        re-run; and the cut has changed since, now that history drops in steps.)
        """
        positions = [
            i
            for i, m in enumerate(messages)
            if isinstance(m, AssistantMessage)
            and any(isinstance(block, TextContent) for block in m.content)
        ]
        if not positions:
            # Nothing left that may be cut. Returning an oversized request is the
            # honest answer: a valid request the provider may reject beats an
            # invalid one it certainly will, and the size is visible to the caller.
            return messages

        trimmable = set(positions)
        fixed = sum(
            self.estimate([m]) for i, m in enumerate(messages) if i not in trimmable
        )
        share = max(MIN_RESULT_TOKENS, (budget - fixed) // len(positions))

        trimmed = list(messages)
        for position in positions:
            assistant = messages[position]
            assert isinstance(assistant, AssistantMessage)  # narrowed by `positions`
            trimmed[position] = _trim_prose(assistant, share)
        return trimmed
