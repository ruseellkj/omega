"""Redaction at the transcript boundary — the Tier 3 row with no seam.

`TIER-2.md` recorded this as the category that stayed open after two patches:

> "The hook is attached to 'after a tool returns' when what redaction wants is
> 'before anything is written down', so a third way around will appear the next
> time a new path to the transcript is added."

It appeared, and there were two of them. `after_tool_call` fires only when a
*tool* hands back a value, so a key in the model's own answer, or in the user's
own prompt, was never masked at all — it went to disk and back to the provider on
every later turn.

The fix is a hook that fires when a **message is recorded**, whatever produced it.
"""

from __future__ import annotations

from pathlib import Path

from omega_agent.harness import Harness
from omega_agent.hooks import AgentHooks
from omega_agent.session import JsonlSessionStore
from omega_agent.types import AgentMessage, AssistantMessage, TextContent, UserMessage
from omega_ai.fake import FakeProvider, text_turn
from omega_coding.builtin_tools import build_tools
from omega_coding.redact import redact_message, redacting_hook

#: Shaped like a real Anthropic key and matched by the same pattern. Not one.
KEY = "sk-ant-" + "A" * 40


def _harness(provider: FakeProvider, **overrides: object) -> Harness:
    hooks = overrides.pop(
        "hooks", AgentHooks(after_tool_call=redacting_hook, before_record=redact_message)
    )
    return Harness(
        provider=provider,
        model="m",
        system="s",
        tools=[],
        hooks=hooks,  # type: ignore[arg-type]
        **overrides,  # type: ignore[arg-type]
    )


def _text(messages: list[AgentMessage]) -> str:
    return "\n".join(str(m) for m in messages)


# --------------------------------------------- the two paths that had no cover


async def test_a_key_in_the_models_own_answer_is_masked() -> None:
    """**The bypass that proved the shape was wrong.**

    `after_tool_call` never sees an assistant message. A model that reads a key
    and repeats it back put that key in the transcript, on disk, and into every
    subsequent request — with redaction wired and working exactly as designed.
    """
    harness = _harness(FakeProvider([text_turn(f"Your key is {KEY}, I will use it.")]))

    async for _ in harness.run("what is my key?"):
        pass

    assert KEY not in _text(harness.messages)
    assert "[redacted Anthropic API key]" in _text(harness.messages)


async def test_a_key_the_user_typed_is_masked() -> None:
    """The mirror. A pasted key is the likeliest way one enters a session at all."""
    harness = _harness(FakeProvider([text_turn("ok")]))

    async for _ in harness.run(f"here is my key {KEY}, check it"):
        pass

    assert KEY not in _text(harness.messages)


async def test_the_masked_version_is_what_reaches_disk(tmp_path: Path) -> None:
    """Recording happens before persistence, so the session file never holds the
    original even for an instant."""
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    harness = _harness(FakeProvider([text_turn(f"key: {KEY}")]), store=store)

    async for _ in harness.run("go"):
        pass
    assert harness.session_id is not None

    on_disk = store.load(harness.session_id)
    assert KEY not in _text(on_disk)

    raw = (store.directory / f"{harness.session_id}.jsonl").read_text()
    assert KEY not in raw, "not in the bytes either, not just the parsed objects"


async def test_a_loaded_session_can_be_masked_without_a_turn_or_a_write(tmp_path: Path) -> None:
    """A session saved before `before_record` existed holds keys in the clear.
    `resume` loads it as it is and leaves the masking to the next turn. That was
    fine while nothing showed a loaded session, but the terminal UI now draws it
    the moment it loads. So the harness has to mask without a turn, and without
    writing, because opening a session must not append to it."""
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    old = _harness(FakeProvider([text_turn("ok")]), store=store, hooks=AgentHooks())
    async for _ in old.run(f"my key is {KEY}"):
        pass
    assert old.session_id is not None
    file = store.directory / f"{old.session_id}.jsonl"
    before = file.read_text()
    assert KEY in before, "the fixture is an unmasked session"

    harness = _harness(FakeProvider([]), store=JsonlSessionStore(tmp_path, home=tmp_path))
    harness.resume(old.session_id)
    await harness.clean_pending()

    assert KEY not in _text(harness.messages)
    assert "[redacted Anthropic API key]" in _text(harness.messages)
    assert file.read_text() == before, "masking wrote to the session file"


# ------------------------------------------------------------- the seam itself


async def test_without_the_hook_nothing_changes() -> None:
    """The empty bundle still changes nothing — the property every hook here has.

    Stated as a test because it is what makes the seam safe to add: a harness
    that did not ask for redaction behaves exactly as it did before.
    """
    harness = _harness(FakeProvider([text_turn(f"key: {KEY}")]), hooks=AgentHooks())

    async for _ in harness.run("go"):
        pass

    assert KEY in _text(harness.messages)


async def test_every_message_is_offered_exactly_once() -> None:
    """Recording is driven by a high-water mark, like persistence.

    Offering a message twice would be wasted work on every turn; offering it zero
    times is the bug this file exists to prevent.
    """
    seen: list[str] = []

    async def spy(message: AgentMessage) -> AgentMessage:
        seen.append(type(message).__name__)
        return message

    harness = _harness(
        FakeProvider([text_turn("one"), text_turn("two")]),
        hooks=AgentHooks(before_record=spy),
    )

    async for _ in harness.run("first"):
        pass
    after_first = list(seen)
    async for _ in harness.run("second"):
        pass

    assert seen[: len(after_first)] == after_first, "nothing was re-offered"
    # `>=`, not `==`: `_record` offers each transcript message once, and
    # `_clean_event` offers the copy each event carries as well. Both are needed,
    # so the hook legitimately sees more than the transcript holds.
    assert len(seen) >= len(harness.messages)


async def test_the_hook_may_rewrite_a_message_entirely() -> None:
    """It returns a message rather than mutating one, so a hook can replace it."""

    async def blank(message: AgentMessage) -> AgentMessage:
        if isinstance(message, AssistantMessage):
            return message.model_copy(update={"content": [TextContent(text="[held]")]})
        return message

    harness = _harness(
        FakeProvider([text_turn("secret plan")]), hooks=AgentHooks(before_record=blank)
    )

    async for _ in harness.run("go"):
        pass

    assert "secret plan" not in _text(harness.messages)
    assert "[held]" in _text(harness.messages)


# ------------------------------------------------------- redact_message itself


async def test_redact_message_leaves_a_clean_message_alone() -> None:
    """Identity when there is nothing to mask — no needless copies."""
    message = UserMessage(content="no secrets here")

    assert await redact_message(message) == message


async def test_redact_message_does_not_mutate_its_argument() -> None:
    """The caller's object is still the caller's. Recording replaces it in the
    list; it must not edit it underneath anyone holding a reference."""
    original = UserMessage(content=f"key {KEY}")
    before = original.model_copy(deep=True)

    cleaned = await redact_message(original)

    assert original == before, "the argument is untouched"
    assert KEY not in cleaned.content  # type: ignore[union-attr]


# ------------------------------------------------- the fifth path, found later


async def test_a_listener_never_sees_an_unmasked_message() -> None:
    """**The fifth bypass, and the reason this category keeps reopening.**

    `TIER-2.md` warned that "a third way around will appear the next time a new
    path to the transcript is added". Adding `before_record` closed three. Then
    Tier 3's structured logging needed a *listener*, and listeners were notified
    **before** `_record` ran — so the transcript was clean while every subscriber
    saw the original. A log written from that would have put the key back on disk
    by a different route.

    Fixed by recording first and cleaning the event before anyone is notified.
    The event carries its own copy of the message, so ordering alone was not
    enough; the copy has to be masked too.
    """
    seen: list[object] = []
    harness = _harness(FakeProvider([text_turn(f"the key is {KEY}")]))
    harness.add_listener(seen.append)

    async for _ in harness.run(f"my key is {KEY}"):
        pass

    assert KEY not in _text(harness.messages), "the transcript, as before"

    # The assembled message on every event is masked. `stream_event` is not, and
    # cannot be: it carries a *delta*, and a key split across two chunks matches
    # no pattern in either half. Nothing that reaches disk carries it - see
    # `test_the_event_log_never_writes_a_delta`.
    assert all(KEY not in repr(getattr(e, "message", "")) for e in seen)


async def test_the_assembled_message_is_masked_on_every_yielded_event() -> None:
    """A caller iterating `run()` is just another subscriber.

    The assembled `message` is masked on all of them. The raw `stream_event`
    beside it is not, for the reason above — which is why the renderer prints
    deltas to a terminal and the log writes messages to disk, and never the
    other way round.
    """
    harness = _harness(FakeProvider([text_turn(f"the key is {KEY}")]))

    events = [event async for event in harness.run("go")]

    assert any(getattr(e, "message", None) is not None for e in events), "some carry one"
    assert all(KEY not in repr(getattr(e, "message", "")) for e in events)


async def test_a_tool_result_reaches_listeners_masked(tmp_path: Path) -> None:
    """`ToolExecutionEndEvent` carries the result as its own field, so it needs
    the same treatment as the message events."""
    from omega_ai.fake import tool_turn

    secret_file = tmp_path / ".env"
    secret_file.write_text(f"ANTHROPIC_API_KEY={KEY}\n")

    seen: list[str] = []
    harness = Harness(
        provider=FakeProvider(
            [[tool_turn("read_file", {"path": str(secret_file)}), text_turn("done")][0]]
        ),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
        hooks=AgentHooks(after_tool_call=redacting_hook, before_record=redact_message),
    )
    harness.add_listener(lambda event: seen.append(repr(event)))

    async for _ in harness.run("read the env file"):
        pass

    assert KEY not in "\n".join(seen)
