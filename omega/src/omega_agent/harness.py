"""The harness — the object that owns the conversation.

Tier 1 had nothing here, and `cli.py` held a bare `list`. That is fine while the
transcript has exactly one reader. It stops being fine the moment something else
needs it, and in Tier 2 three things do:

* **persistence** must write entries as they appear
* **orphan repair** must scan the transcript after an interrupt
* **the steering queue** must add to it between turns

`python/TIER-1.md` named this directly: orphan repair "needs a harness that owns
`messages`". So one object owns it, and the loop stays a function over a list.

**Nothing flows downward from a UI.** A listener subscribes and is called; it
cannot reach in and drive the loop. When you type while the agent works, the text
is *queued* on the harness and the loop picks it up when it is ready. That
strictness is why Tau's whole agent-to-UI bridge is 99 lines, and why the same
agent could later run on a server with the UI somewhere else.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import replace

from omega_agent.agent_events import AgentEvent
from omega_agent.cancellation import CancelSignal
from omega_agent.hooks import AgentHooks
from omega_agent.loop import DEFAULT_MAX_TURNS, run_agent_loop
from omega_agent.provider import ModelProvider
from omega_agent.session import SessionStore
from omega_agent.tools import Tool
from omega_agent.types import AgentMessage, AssistantMessage, ToolResultMessage, UserMessage

#: A subscriber. Synchronous and returning nothing, deliberately: a listener that
#: could block or fail would be able to stall the loop it is only watching.
AgentListener = Callable[[AgentEvent], None]

#: What a synthesized tool result says.
#:
#: Honest on purpose. We know that no result was recorded; we do *not* know
#: whether the tool ran. A note claiming "nothing happened" would be a lie the
#: model then reasons from — it might skip re-checking a file that was in fact
#: half-written. "Unknown, verify" is both true and actionable.
INTERRUPTED_NOTE = (
    "Interrupted: this tool call was cancelled before a result was recorded. "
    "Its effect is unknown - verify the current state before relying on it."
)


class Harness:
    """Holds the transcript and the run configuration; drives the loop.

    One harness is one conversation. Call `run()` repeatedly and the transcript
    accumulates, which is what makes a REPL a conversation rather than a series
    of unrelated questions.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider,
        model: str,
        system: str,
        tools: Sequence[Tool],
        hooks: AgentHooks | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        signal: CancelSignal | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.system = system
        self.tools = list(tools)
        self.max_turns = max_turns

        #: Queues the loop drains between turns. The harness owns them because
        #: it is the only thing that outlives a single run, and it supplies the
        #: two hooks that read them — unless the caller already did, in which
        #: case theirs wins and these stay unused.
        self._steering: list[AgentMessage] = []
        self._follow_ups: list[AgentMessage] = []

        base = hooks if hooks is not None else AgentHooks()
        self.hooks = replace(
            base,
            get_steering_messages=base.get_steering_messages or self._drain_steering,
            get_follow_up_messages=base.get_follow_up_messages or self._drain_follow_ups,
        )

        #: Concrete, not the Protocol: the harness *owns* cancellation, so it
        #: needs to be able to set and clear it, not merely ask. Everything
        #: below only ever asks.
        self.signal = signal if signal is not None else CancelSignal()

        #: The transcript. The harness owns it; the loop appends to it.
        self.messages: list[AgentMessage] = []
        self._listeners: list[AgentListener] = []

        #: Persistence is optional. Without a store the harness behaves exactly
        #: as it did in Step 1 and touches no files.
        self.store = store
        self.session_id: str | None = None

        #: How much of `messages` is already on disk. The loop appends directly
        #: to the list, and so does `repair_orphans`, so tracking a high-water
        #: mark catches every writer without any of them knowing about storage.
        self._persisted = 0

        #: The same trick, one step earlier. Counts how much of `messages` has
        #: been through `before_record`. Separate from `_persisted` because
        #: recording must happen even with no store — `--no-save` still sends the
        #: transcript to a provider, so a key in it is still a key leaked.
        self._recorded = 0

    def add_listener(self, listener: AgentListener) -> None:
        """Subscribe to the agent events. Boundary D, and it only goes one way."""
        self._listeners.append(listener)

    def cancel(self) -> None:
        """Ask the current turn to stop.

        Safe to call from a signal handler: it sets one boolean and returns. The
        turn ends at the next check — in the provider stream, in a tool, or
        before the next request — and ends *properly*, with a valid transcript.
        """
        self.signal.cancel()

    # ------------------------------------------------------------------ repair

    def repair_orphans(self) -> int:
        """Answer every tool call that has no result. Returns how many it fixed.

        **The fix for the nastiest of the nine beginner failures.** An assistant
        message carrying a tool call with no matching result is rejected by
        providers — not once, but on every future request. The conversation is
        invalid forever. Interrupt at the wrong moment, persist the transcript,
        and you have written a file that can never be resumed.

        Results are inserted **beside the call that lacks one**, not appended to
        the end. Order is a provider requirement: a result belongs to the
        assistant turn that asked for it, so in a multi-turn transcript appending
        would answer the wrong message.
        """
        repaired: list[AgentMessage] = []
        fixed = 0
        index = 0

        while index < len(self.messages):
            message = self.messages[index]
            repaired.append(message)
            index += 1

            if not isinstance(message, AssistantMessage) or not message.tool_calls:
                continue

            # Consume the results that already follow this assistant message.
            answered: set[str] = set()
            while index < len(self.messages):
                following = self.messages[index]
                if not isinstance(following, ToolResultMessage):
                    break
                answered.add(following.tool_call_id)
                repaired.append(following)
                index += 1

            for call in message.tool_calls:
                if call.id in answered:
                    continue
                repaired.append(
                    ToolResultMessage(
                        tool_call_id=call.id,
                        tool_name=call.name,
                        content=INTERRUPTED_NOTE,  # type: ignore[arg-type]
                        is_error=True,
                    )
                )
                fixed += 1

        # Rewrite in place: callers may be holding this list.
        self.messages[:] = repaired
        return fixed

    # ------------------------------------------------------------------ queues

    def queue_steering(self, text: str) -> None:
        """Add guidance to be picked up between turns, mid-run.

        "Actually, use pytest not unittest" while it is already working. The
        loop reads this after tool results and before the next request, so the
        correction lands without restarting the task.
        """
        self._steering.append(UserMessage(content=text))

    def queue_follow_up(self, text: str) -> None:
        """Queue the next task, to start when the current one finishes."""
        self._follow_ups.append(UserMessage(content=text))

    async def _drain_steering(self) -> list[AgentMessage]:
        drained, self._steering = self._steering, []
        return drained

    async def _drain_follow_ups(self) -> list[AgentMessage]:
        drained, self._follow_ups = self._follow_ups, []
        return drained

    # ---------------------------------------------------------------- sessions

    def resume(self, session_id: str) -> int:
        """Load a stored transcript into this harness. Returns how many messages.

        **Repairs orphans immediately**, and that is the point where Step 2 and
        Step 5 meet. An interrupted session is exactly the one you want to
        resume, and exactly the one carrying an unanswered tool call — which
        providers reject on every future request. Loading it without repairing it
        would hand back a conversation that can never be continued.
        """
        if self.store is None:
            raise ValueError("Cannot resume without a session store.")

        self.session_id = session_id
        self.messages[:] = self.store.load(session_id)
        self._persisted = len(self.messages)

        # Deliberately 0, not len(). What is on disk was redacted on the way in
        # *if* this harness wrote it — a session written before `before_record`
        # existed was not, and resuming is the one chance to clean it.
        self._recorded = 0

        # Anything the repair adds is beyond the high-water mark, so it is
        # written on the next flush and the session is only repaired once.
        self.repair_orphans()
        return len(self.messages)

    def start_new_session(self) -> str | None:
        """Forget the conversation and begin a new one. Returns the id left behind.

        The mirror of `resume`, touching the same three pieces of state: the
        messages, the high-water mark, and the session id.

        **Nothing is deleted.** The previous transcript stays on disk, keeps its
        place in `--sessions`, and can still be reopened with `--resume`. Erasing
        it would make an append-only log a lie — and a `/clear` that destroys work
        is a `/clear` people are afraid to type.

        Setting `session_id` to None is the entire mechanism for starting the next
        file: `run` already creates a session when it finds none, so this needs no
        new path through the store.
        """
        previous = self.session_id
        self.messages.clear()
        self._persisted = 0
        self._recorded = 0
        self.session_id = None
        return previous

    def replace_transcript(self, messages: Sequence[AgentMessage]) -> int:
        """Swap the working transcript for a smaller one. Returns how many remain.

        The third method touching the same state as `resume` and
        `start_new_session`, and the one with the subtlest failure mode.

        **`_persisted` must move too.** It is a high-water mark, not a count of
        what is on disk in general: `_flush` writes `messages[_persisted:]`.
        Shrink `messages` from 20 to 4 and leave the mark at 20, and the next
        sixteen real messages are silently never written — a data-loss bug that
        no test of compaction itself would catch. Setting it to the new length
        means "everything currently held is accounted for".

        **Nothing is deleted from disk.** The session file is append-only and
        still holds every original entry; later messages append after them, so
        `--resume` returns the *full* history rather than the compacted view. The
        file is the record, this list is the working set, and they are allowed to
        differ — the same two-views split `history.py` describes, one level out.
        """
        self.messages[:] = list(messages)
        self._persisted = len(self.messages)
        self._recorded = len(self.messages)
        return len(self.messages)

    async def _record(self) -> None:
        """Offer every new message to `before_record`, then persist.

        **The seam redaction actually wanted.** `after_tool_call` fires when a
        *tool* returns a value, which is one of several ways into the transcript;
        this fires on the transcript itself, so it covers all of them — the
        model's own answer, the user's prompt, a synthesized interrupt note, and
        anything a later tier adds.

        Driven by a high-water mark for the same reason persistence is: the loop
        appends straight to `messages` and so does `repair_orphans`, so counting
        how far we have got catches every writer without any of them knowing this
        exists.

        Replaces rather than mutates. A hook returning a new message means the
        caller's object is never edited underneath anything holding a reference,
        which is the same guarantee `transform_context` gives one layer up.
        """
        if self.hooks.before_record is not None:
            while self._recorded < len(self.messages):
                index = self._recorded
                self.messages[index] = await self.hooks.before_record(self.messages[index])
                self._recorded += 1
        else:
            self._recorded = len(self.messages)
        self._flush()

    def _flush(self) -> None:
        """Write whatever is not on disk yet.

        Called after every event rather than at the end of a run: the process
        dying mid-task is the event persistence exists for, so a transcript that
        is only saved on success saves nothing worth having.
        """
        if self.store is None or self.session_id is None:
            return
        while self._persisted < len(self.messages):
            self.store.append(self.session_id, self.messages[self._persisted])
            self._persisted += 1

    # --------------------------------------------------------------------- run

    async def run(self, prompt: str) -> AsyncIterator[AgentEvent]:
        """One user prompt, run to completion.

        Events are both handed to every listener and yielded to the caller, so a
        UI can subscribe while a test simply iterates.
        """
        # Clear a stale cancellation before anything else, or the previous
        # Ctrl-C cancels this turn too.
        self.signal.reset()

        # Heal an interrupted transcript on the way in. The user should not have
        # to know this failure mode exists — the request that would have failed
        # permanently simply succeeds.
        self.repair_orphans()

        if self.store is not None and self.session_id is None:
            self.session_id = self.store.create_session(model=self.model)

        self.messages.append(UserMessage(content=prompt))
        await self._record()

        async for event in run_agent_loop(
            provider=self.provider,
            model=self.model,
            system=self.system,
            messages=self.messages,
            tools=self.tools,
            hooks=self.hooks,
            max_turns=self.max_turns,
            signal=self.signal,
        ):
            for listener in self._listeners:
                listener(event)
            await self._record()
            yield event

        await self._record()
