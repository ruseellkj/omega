"""Compaction — beginner failure #1, and the first thing Tier 3 closes.

Tier 2 could only *watch* the context fill: `/context` showed the wall coming and
nothing moved it. These tests describe the thing that moves it.

Four properties carry the weight, and only the first is about size:

* **It comes back under budget.** The headline, and the easy half.
* **It never orphans a tool call.** An `AssistantMessage` carrying a `ToolCall`
  and the `ToolResultMessage` answering it are one indivisible unit. Cut between
  them and every future request is rejected — the same permanent failure
  `repair_orphans` exists to undo, except compaction would recreate it on every
  single request.
* **The transcript is untouched.** Compaction rewrites what is *sent*, never what
  is *kept*. That split is what makes a compaction bug recoverable instead of
  destructive.
* **It is deterministic.** Same input, same output, every time. Prompt caching
  (failure #9, the other half of Tier 3) needs a byte-identical prefix across
  requests, and a compactor that drifts would defeat it before it is written.
"""

from __future__ import annotations

from omega_agent.harness import Harness
from omega_agent.hooks import AgentHooks
from omega_agent.types import (
    AgentMessage,
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from omega_ai.fake import FakeProvider, text_turn
from omega_coding.compact import Compactor

# A small window keeps the fixtures readable: 4,000 tokens is 16,000 characters,
# so a "big" tool result is a few thousand characters rather than a megabyte.
WINDOW = 4_000


def _compactor(**overrides: object) -> Compactor:
    settings: dict[str, object] = {
        "model": "test-model",
        "system": "be helpful",
        "tools": [],
        "window": WINDOW,
    }
    settings.update(overrides)
    return Compactor(**settings)  # type: ignore[arg-type]


def _turn_with_tool(call_id: str, output: str) -> list[AgentMessage]:
    """One indivisible unit: the call, and the result answering it."""
    return [
        AssistantMessage(
            model="test-model",
            stop_reason="toolUse",
            content=[ToolCall(id=call_id, name="read_file", arguments={"path": "a.py"})],
        ),
        ToolResultMessage(tool_call_id=call_id, tool_name="read_file", content=output),
    ]


def _long_conversation(turns: int = 12, output_chars: int = 3_000) -> list[AgentMessage]:
    """A transcript that comfortably overflows `WINDOW`."""
    messages: list[AgentMessage] = [UserMessage(content="port the parser to the new API")]
    for i in range(turns):
        messages.extend(_turn_with_tool(f"call-{i}", f"file {i} contents: " + "x" * output_chars))
    return messages


def _call_ids(messages: list[AgentMessage]) -> set[str]:
    return {
        call.id
        for message in messages
        if isinstance(message, AssistantMessage)
        for call in message.tool_calls
    }


def _result_ids(messages: list[AgentMessage]) -> set[str]:
    return {m.tool_call_id for m in messages if isinstance(m, ToolResultMessage)}


# ------------------------------------------------------- does it do anything at all


async def test_a_short_conversation_is_returned_untouched() -> None:
    """Compaction that fires when it is not needed is a bug, not a safety margin.

    Every rewrite costs fidelity, and an agent that starts forgetting at 10% full
    is worse than one that never compacts.
    """
    messages: list[AgentMessage] = [
        UserMessage(content="hello"),
        AssistantMessage(model="m", stop_reason="stop", content=[TextContent(text="hi")]),
    ]

    assert await _compactor()(messages) == messages


async def test_a_long_conversation_comes_back_under_budget() -> None:
    """The headline. Tier 2 could measure this wall; it could not move it."""
    compactor = _compactor()
    messages = _long_conversation()

    assert compactor.estimate(messages) > compactor.budget, "the fixture must overflow"

    compacted = await compactor(messages)

    assert compactor.estimate(compacted) <= compactor.budget


# --------------------------------------------------------- the property that matters


async def test_no_tool_call_is_left_without_its_result() -> None:
    """**The test this module exists for.**

    A `ToolCall` with no matching `ToolResultMessage` is rejected by providers on
    every future request, not just once. `repair_orphans` fixes that when an
    interrupt causes it; compaction causing it would re-break the request each
    time, and no repair would help because the transcript itself is fine.
    """
    compacted = await _compactor()(_long_conversation())

    assert _call_ids(compacted) == _result_ids(compacted)


async def test_no_result_is_left_without_its_call() -> None:
    """The mirror. A result answering a call the model cannot see is equally invalid."""
    compacted = await _compactor()(_long_conversation())

    for message in compacted:
        if isinstance(message, ToolResultMessage):
            assert message.tool_call_id in _call_ids(compacted)


async def test_a_single_turn_too_big_to_fit_is_still_valid() -> None:
    """The nastiest case: one unit alone exceeds the whole budget.

    Dropping half of it would orphan a call; keeping it whole blows the budget.
    The only correct answer is to keep the pairing and shrink the *content*, so
    the request stays valid even when it cannot be made small enough.
    """
    huge: list[AgentMessage] = [UserMessage(content="go")]
    huge.extend(_turn_with_tool("only", "y" * (WINDOW * 4 * 3)))

    compacted = await _compactor()(huge)

    assert _call_ids(compacted) == _result_ids(compacted), "validity beats size"
    assert _call_ids(compacted) == {"only"}


# --------------------------------------------------------------- what it preserves


async def test_the_original_task_survives() -> None:
    """The first user message is the task. Forget it and the agent finishes
    something else — which is the failure mode people actually report."""
    messages = _long_conversation()

    compacted = await _compactor()(messages)

    assert compacted[0] == messages[0]


async def test_the_most_recent_turn_survives_intact() -> None:
    """Recency is what the model is mid-way through. The oldest context is the
    affordable thing to lose; the newest never is."""
    messages = _long_conversation()

    compacted = await _compactor()(messages)

    assert compacted[-1] == messages[-1]


async def test_it_says_that_it_dropped_something() -> None:
    """A silent gap invites the model to invent what was there.

    Naming the omission costs a few tokens and converts a hallucination risk into
    a fact the model can work with.
    """
    compacted = await _compactor()(_long_conversation())

    assert any(
        isinstance(m, UserMessage) and "compact" in m.content.lower() for m in compacted
    ), "the elision has to be visible in the context"


# ------------------------------------------------------ the two-views guarantee


async def test_the_transcript_is_never_modified() -> None:
    """`transform_context` rewrites what is *sent*. What is *kept* is untouched.

    This is what makes a compaction bug a bad request rather than lost work: the
    JSONL on disk and `harness.messages` still hold every original message.
    """
    messages = _long_conversation()
    before = [m.model_copy(deep=True) for m in messages]

    await _compactor()(messages)

    assert messages == before


async def test_compacting_does_not_shrink_the_harness_transcript() -> None:
    """The same guarantee, stated through the object that owns the transcript."""
    harness = Harness(
        provider=FakeProvider([text_turn("ok")]),
        model="test-model",
        system="s",
        tools=[],
    )
    harness.messages.extend(_long_conversation())
    kept = len(harness.messages)

    compacted = await _compactor()(harness.messages)

    assert len(compacted) < kept, "the request really did shrink"
    assert len(harness.messages) == kept, "and the transcript really did not"


# ------------------------------------------------------------------- determinism


async def test_the_same_input_compacts_identically() -> None:
    """Prompt caching needs a byte-identical prefix across requests.

    `transform_context` runs on *every* loop iteration, not once per turn, so a
    compactor carrying state or varying its output would change the prefix
    mid-turn and defeat caching before it is even written.
    """
    compactor = _compactor()
    messages = _long_conversation()

    first = await compactor(messages)
    second = await compactor(messages)

    assert first == second


async def test_two_compactors_agree() -> None:
    """No hidden state on the instance — the same settings give the same answer."""
    messages = _long_conversation()

    assert await _compactor()(messages) == await _compactor()(messages)


# ------------------------------------------ conversations with nothing to shrink


async def test_a_conversation_of_pure_assistant_text_still_fits() -> None:
    """Found by fuzzing, not by reading.

    3,000 random transcripts showed 257 that stayed over budget, and every one had
    the same shape: no tool results at all. Dropping old turns is not enough when
    the newest turn is itself a wall of assistant prose, and the first version of
    `_shrink` only knew how to cut tool results.

    Assistant text is the *second* thing to cut, after tool output — a tool result
    can be re-derived by running the tool again, and an explanation cannot.
    """
    compactor = _compactor()
    messages: list[AgentMessage] = [UserMessage(content="explain the parser")]
    for i in range(8):
        messages.append(
            AssistantMessage(
                model="m",
                stop_reason="stop",
                content=[TextContent(text=f"part {i} " + "a" * 4_000)],
            )
        )

    compacted = await compactor(messages)

    assert compactor.estimate(compacted) <= compactor.budget


async def test_one_enormous_assistant_answer_is_shrunk_not_dropped() -> None:
    """A single message can exceed the budget on its own. It has to be cut, not
    discarded — an empty request teaches the model nothing."""
    compactor = _compactor()
    messages: list[AgentMessage] = [
        UserMessage(content="go"),
        AssistantMessage(
            model="m", stop_reason="stop", content=[TextContent(text="z" * (WINDOW * 4 * 2))]
        ),
    ]

    compacted = await compactor(messages)

    assert compactor.estimate(compacted) <= compactor.budget
    assert len(compacted) == len(messages), "shrunk in place, not removed"


async def test_the_users_own_words_are_never_shrunk() -> None:
    """The task is the one thing worth blowing the budget for.

    A tool result can be re-read and an explanation re-generated; the instruction
    cannot be recovered from anywhere, and an agent that half-remembers what it
    was asked is worse than one that admits the context is full.
    """
    task = "port the parser " + "q" * (WINDOW * 4 * 2)
    compacted = await _compactor()([UserMessage(content=task)])

    assert compacted == [UserMessage(content=task)]


# ------------------------------------------------------------- through the loop


async def test_the_loop_really_consults_the_compactor() -> None:
    """The wiring, not the algorithm.

    Everything above tests `Compactor` directly. This one runs a real turn and
    checks the two halves meet: `transform_context` is reached from inside
    `run()`, the request is smaller than the transcript, and the transcript still
    *grew* — because the turn appended to it while compaction only ever touched
    the derived copy.
    """
    compactor = _compactor(window=2_000)
    seen: dict[str, int] = {}

    async def spy(messages: list[AgentMessage]) -> list[AgentMessage]:
        sent = await compactor(messages)
        seen["received"] = compactor.estimate(messages)
        seen["sent"] = compactor.estimate(sent)
        return sent

    harness = Harness(
        provider=FakeProvider([text_turn("done")]),
        model="test-model",
        system="be helpful",
        tools=[],
        hooks=AgentHooks(transform_context=spy),
    )
    for i in range(10):
        harness.messages.extend(_turn_with_tool(f"loop-{i}", "data " * 800))
    before = len(harness.messages)

    async for _ in harness.run("what did you find?"):
        pass

    assert seen["received"] > compactor.budget, "the transcript really did overflow"
    assert seen["sent"] <= compactor.budget, "and the request really did not"
    assert len(harness.messages) > before, "the turn appended; nothing was taken away"
