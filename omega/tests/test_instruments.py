"""The instruments, the queues, and the two views of history.

Grouped because they are the same kind of thing: cheap additions that make the
agent usable, none of which required the loop to learn anything.

The context gauge and the cost counter are deliberately *instruments*, not fixes.
They measure beginner failures #1 and #9; Tier 3 fixes them. Being able to watch
a wall approach is what makes the Tier 3 work possible to tune.
"""

from __future__ import annotations

from typing import Any

from omega_agent.harness import Harness
from omega_agent.tools import Tool, ToolResult
from omega_agent.types import AssistantMessage, TextContent, ToolCall, Usage, UserMessage
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_coding.context import (
    DEFAULT_CONTEXT_WINDOW,
    ContextUsage,
    estimate_request_tokens,
    measure,
    window_for,
)
from omega_coding.cost import CostTracker, Price
from omega_coding.history import drop_empty_failed_turns


async def _ok(arguments: dict[str, Any], signal: Any) -> ToolResult:
    return ToolResult(content="ran")


OK_TOOL = Tool(
    name="ok",
    description="a tool with a description that costs tokens like a real one does",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    execute=_ok,
)


# ------------------------------------------------------------- context gauge


def test_an_empty_request_is_nearly_free() -> None:
    assert estimate_request_tokens(system="", messages=[], tools=[]) == 0


def test_tool_schemas_are_counted() -> None:
    """The part people forget, and it under-reports in the one bad direction.

    Schemas are re-sent on every single request. An estimator that measures only
    messages is wrong by a constant that grows with every tool added.
    """
    without = estimate_request_tokens(system="s", messages=[], tools=[])
    with_tool = estimate_request_tokens(system="s", messages=[], tools=[OK_TOOL])

    assert with_tool > without


def test_tool_call_arguments_are_counted() -> None:
    """A call carrying a whole file as an argument is exactly what fills a window."""
    small = AssistantMessage(
        model="m", content=[ToolCall(id="c", name="ok", arguments={"path": "a.py"})]
    )
    large = AssistantMessage(
        model="m", content=[ToolCall(id="c", name="ok", arguments={"content": "x" * 4000})]
    )

    assert estimate_request_tokens(
        system="", messages=[large], tools=[]
    ) > estimate_request_tokens(system="", messages=[small], tools=[]) + 500


def test_text_and_tool_results_are_counted() -> None:
    messages = [
        UserMessage(content="a" * 400),
        AssistantMessage(model="m", content=[TextContent(text="b" * 400)]),
    ]
    assert estimate_request_tokens(system="", messages=messages, tools=[]) == 200


def test_an_unknown_model_gets_the_conservative_window() -> None:
    """Over-reporting how full you are is safe; under-reporting is not."""
    assert window_for("some-new-model") == DEFAULT_CONTEXT_WINDOW


def test_a_known_large_window_is_recognised() -> None:
    assert window_for("claude-opus-5[1m]") == 1_000_000


def test_usage_renders_as_a_fraction_and_a_percent() -> None:
    usage = ContextUsage(
        system_tokens=20_000, message_tokens=25_000, tool_tokens=5_000, window=200_000
    )

    assert usage.percent == 25
    assert "50,000" in str(usage)
    assert "25%" in str(usage)


def test_a_zero_window_does_not_divide_by_zero() -> None:
    assert (
        ContextUsage(
            system_tokens=10, message_tokens=0, tool_tokens=0, window=0
        ).percent
        == 0
    )


def test_measure_ties_it_together() -> None:
    usage = measure(model="claude-opus-5[1m]", system="s", messages=[], tools=[OK_TOOL])

    assert usage.window == 1_000_000
    assert usage.estimated_tokens > 0


# ---------------------------------------------------------------- cost


def test_tokens_are_summed_across_turns() -> None:
    tracker = CostTracker()
    tracker.record(AssistantMessage(model="m", usage=Usage(input=100, output=20)))
    tracker.record(AssistantMessage(model="m", usage=Usage(input=150, output=30)))

    assert tracker.input_tokens == 250
    assert tracker.output_tokens == 50
    assert tracker.total_tokens == 300
    assert tracker.turns == 2


def test_no_price_means_no_dollar_figure() -> None:
    """The decision worth more than the code.

    A confidently wrong cost figure is worse than none: it gets believed, and
    budgets get planned on it. So omega ships no price table.
    """
    tracker = CostTracker()
    tracker.record(AssistantMessage(model="m", usage=Usage(input=1_000_000, output=0)))

    assert tracker.dollars is None
    assert "$" not in str(tracker)
    assert "1,000,000 in" in str(tracker)


def test_a_supplied_price_is_applied() -> None:
    tracker = CostTracker(Price(input_per_mtok=3.0, output_per_mtok=15.0))
    tracker.record(AssistantMessage(model="m", usage=Usage(input=1_000_000, output=1_000_000)))

    assert tracker.dollars == 18.0
    assert "$18.0000" in str(tracker)


async def test_it_works_as_a_harness_listener() -> None:
    tracker = CostTracker()
    harness = Harness(
        provider=FakeProvider([tool_turn("ok", {}), text_turn("done")]),
        model="m",
        system="s",
        tools=[OK_TOOL],
    )
    harness.add_listener(tracker.observe)

    async for _event in harness.run("go"):
        pass

    assert tracker.turns == 2, "one count per model response, not per event"


def test_price_from_env_needs_both_halves(monkeypatch: Any) -> None:
    """Input-only pricing would under-report by most of the bill."""
    from omega_coding.cost import price_from_env

    monkeypatch.setenv("OMEGA_PRICE_INPUT", "3.0")
    monkeypatch.delenv("OMEGA_PRICE_OUTPUT", raising=False)
    assert price_from_env() is None

    monkeypatch.setenv("OMEGA_PRICE_OUTPUT", "15.0")
    assert price_from_env() == Price(3.0, 15.0)


def test_a_malformed_price_is_ignored_not_crashed(monkeypatch: Any) -> None:
    from omega_coding.cost import price_from_env

    monkeypatch.setenv("OMEGA_PRICE_INPUT", "free")
    monkeypatch.setenv("OMEGA_PRICE_OUTPUT", "15.0")
    assert price_from_env() is None


# ----------------------------------------------------- two views of history


async def test_an_empty_failed_turn_is_not_sent() -> None:
    kept = [
        UserMessage(content="go"),
        AssistantMessage(model="m", stop_reason="error", error_message="upstream 500"),
        UserMessage(content="try again"),
    ]

    sent = await drop_empty_failed_turns(kept)

    assert [m.role for m in sent] == ["user", "user"]
    assert len(kept) == 3, "the transcript itself must be untouched"


async def test_a_failure_that_produced_text_is_kept() -> None:
    """The important half.

    A turn that streamed 500 tokens and then died still carries those tokens,
    and they are real context. Dropping them would discard work that was done.
    """
    partial = AssistantMessage(
        model="m",
        stop_reason="error",
        error_message="died halfway",
        content=[TextContent(text="here is what I found so far")],
    )

    sent = await drop_empty_failed_turns([UserMessage(content="go"), partial])

    assert len(sent) == 2


async def test_successful_turns_are_never_dropped() -> None:
    messages = [
        UserMessage(content="go"),
        AssistantMessage(model="m", stop_reason="stop", content=[TextContent(text="done")]),
    ]
    assert await drop_empty_failed_turns(messages) == messages


async def test_it_changes_what_the_provider_sees_not_what_is_kept() -> None:
    """End to end as a convert_to_llm hook."""
    from omega_agent.hooks import AgentHooks

    provider = FakeProvider([text_turn("second try")])
    harness = Harness(
        provider=provider,
        model="m",
        system="s",
        tools=[OK_TOOL],
        hooks=AgentHooks(convert_to_llm=drop_empty_failed_turns),
    )
    harness.messages.append(
        AssistantMessage(model="m", stop_reason="error", error_message="earlier failure")
    )

    async for _event in harness.run("go"):
        pass

    sent_roles = [m.role for m in provider.calls[0].messages]
    assert sent_roles == ["user"], "the failed turn should not have been sent"
    assert any(
        isinstance(m, AssistantMessage) and m.stop_reason == "error" for m in harness.messages
    ), "but it must still be in the transcript"


# --------------------------------------------------------------- the queues


async def test_steering_is_picked_up_between_turns() -> None:
    """A correction mid-run, without restarting the task."""
    provider = FakeProvider([tool_turn("ok", {}), text_turn("adjusted")])
    harness = Harness(provider=provider, model="m", system="s", tools=[OK_TOOL])

    def steer(event: Any) -> None:
        if event.type == "tool_execution_end":
            harness.queue_steering("actually, use pytest")

    harness.add_listener(steer)
    async for _event in harness.run("go"):
        pass

    second_request = [
        m.content for m in provider.calls[1].messages if isinstance(m, UserMessage)
    ]
    assert "actually, use pytest" in second_request


async def test_a_follow_up_continues_instead_of_ending() -> None:
    """The model stopped asking, but another task was queued while it worked."""
    provider = FakeProvider([text_turn("first done"), text_turn("second done")])
    harness = Harness(provider=provider, model="m", system="s", tools=[OK_TOOL])
    harness.queue_follow_up("now do the other thing")

    events = [event async for event in harness.run("do the first thing")]

    assert len(provider.calls) == 2, "the follow-up should have started a second turn"
    assert events[-1].reason == "stop"


async def test_a_queue_is_drained_not_replayed() -> None:
    provider = FakeProvider([text_turn("a"), text_turn("b"), text_turn("c")])
    harness = Harness(provider=provider, model="m", system="s", tools=[OK_TOOL])
    harness.queue_follow_up("once")

    async for _event in harness.run("go"):
        pass

    assert len(provider.calls) == 2, "the follow-up ran twice - the queue was not drained"


async def test_no_queued_messages_changes_nothing() -> None:
    provider = FakeProvider([text_turn("done")])
    harness = Harness(provider=provider, model="m", system="s", tools=[OK_TOOL])

    events = [event async for event in harness.run("go")]

    assert len(provider.calls) == 1
    assert events[-1].reason == "stop"


async def test_caller_supplied_queue_hooks_win() -> None:
    """The harness only fills these if nobody else did."""
    from omega_agent.hooks import AgentHooks
    from omega_agent.types import AgentMessage

    async def mine() -> list[AgentMessage]:
        return [UserMessage(content="from the caller")]

    provider = FakeProvider([text_turn("a"), text_turn("b")])
    harness = Harness(
        provider=provider,
        model="m",
        system="s",
        tools=[OK_TOOL],
        hooks=AgentHooks(get_follow_up_messages=mine),
    )
    harness.queue_follow_up("from the harness")

    async for _event in harness.run("go"):
        pass

    contents = [m.content for m in provider.calls[1].messages if isinstance(m, UserMessage)]
    assert "from the caller" in contents
    assert "from the harness" not in contents
