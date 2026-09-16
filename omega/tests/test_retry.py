"""Retry with backoff — beginner failure #5.

One 429 used to throw away the whole run. The tests split into two halves,
because the design has two halves:

* **which failures are worth retrying** — a 429 means "not now", a 400 means
  "not ever, that request is wrong", and retrying the second hides a bug
* **when a retry is still safe** — a stream that has already emitted tokens
  cannot be restarted without producing them twice

The second is the one that makes streaming retry different from ordinary
request/response retry, and it is the one worth a test with a real adapter
behind it.
"""

from __future__ import annotations

import pytest

from omega_ai.retry import (
    DEFAULT_RETRY,
    RetryPolicy,
    delay_for,
    is_permanent_quota_failure,
    is_retryable,
    retry_after_of,
)


class _Status(Exception):
    """Stands in for an SDK error. `is_retryable` reads the attribute, not the class."""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.response = type("R", (), {"headers": headers or {}})()


# --------------------------------------------------------- what to retry


@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, 502, 503, 504])
def test_server_side_try_again_is_retryable(status: int) -> None:
    assert is_retryable(_Status(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_a_bad_request_is_never_retried(status: int) -> None:
    """Sending it again changes nothing and hides the actual bug."""
    assert is_retryable(_Status(status)) is False


def test_a_dropped_connection_is_retryable() -> None:
    assert is_retryable(ConnectionError("reset by peer")) is True
    assert is_retryable(TimeoutError()) is True


def test_cancellation_is_never_retried() -> None:
    """The user asked to stop. Trying harder is the opposite of what they meant."""
    import asyncio

    assert is_retryable(asyncio.CancelledError()) is False


def test_an_unrecognised_error_is_not_retried() -> None:
    """Fail closed: unknown failure modes get reported, not hammered."""
    assert is_retryable(ValueError("who knows")) is False


# ------------------------------------------------------------- how long


def test_backoff_is_exponential_and_capped() -> None:
    policy = RetryPolicy(attempts=6, base_delay=0.5, max_delay=4.0)

    assert [delay_for(n, policy) for n in range(5)] == [0.5, 1.0, 2.0, 4.0, 4.0]


def test_the_servers_own_advice_wins() -> None:
    """It knows when it will be ready; a backoff curve is guessing."""
    assert delay_for(0, DEFAULT_RETRY, retry_after=3.0) == 3.0


def test_retry_after_is_still_capped() -> None:
    """A server asking for an hour should not silently hang the agent."""
    policy = RetryPolicy(max_delay=8.0)
    assert delay_for(0, policy, retry_after=3600.0) == 8.0


def test_a_retry_after_header_is_read() -> None:
    assert retry_after_of(_Status(429, {"retry-after": "2.5"})) == 2.5


def test_a_date_formatted_retry_after_falls_back_to_backoff() -> None:
    """Legal, rare, and not worth a date parser."""
    assert retry_after_of(_Status(429, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None


def test_no_header_means_no_advice() -> None:
    assert retry_after_of(_Status(429)) is None
    assert retry_after_of(ValueError("plain")) is None


# ----------------------------------------- when a retry is still safe


async def test_a_failed_connection_is_retried_and_succeeds() -> None:
    """The whole point: a 503 on connect costs a pause, not the run."""
    from omega_ai.anthropic import AnthropicProvider
    from stub_anthropic import StubClient

    client = StubClient(fail_times=2, error=lambda: _Status(503))
    provider = AnthropicProvider(
        client=client,  # type: ignore[arg-type]
        retry=RetryPolicy(attempts=3, base_delay=0.0),
    )

    events = [
        event
        async for event in provider.stream_response(
            model="m", system="s", messages=[], tools=[]
        )
    ]

    assert client.attempts == 3, "it should have tried twice more"
    assert events[-1].type == "done"


async def test_retries_are_invisible_above_the_provider() -> None:
    """boundaries-and-layout.md:102 - retries happen below this line.

    The loop must not be able to tell. If a retry produced an extra `start` or a
    stray `error`, everything above would have to learn about retrying.
    """
    from omega_ai.anthropic import AnthropicProvider
    from stub_anthropic import StubClient

    provider = AnthropicProvider(
        client=StubClient(fail_times=1, error=lambda: _Status(503)),  # type: ignore[arg-type]
        retry=RetryPolicy(attempts=3, base_delay=0.0),
    )

    events = [
        event
        async for event in provider.stream_response(
            model="m", system="s", messages=[], tools=[]
        )
    ]

    assert [e.type for e in events].count("start") == 1
    assert [e.type for e in events].count("done") == 1
    assert not [e for e in events if e.type == "error"]


async def test_a_bad_request_is_reported_immediately() -> None:
    from omega_ai.anthropic import AnthropicProvider
    from stub_anthropic import StubClient

    client = StubClient(fail_times=99, error=lambda: _Status(400))
    provider = AnthropicProvider(
        client=client,  # type: ignore[arg-type]
        retry=RetryPolicy(attempts=3, base_delay=0.0),
    )

    events = [
        event
        async for event in provider.stream_response(
            model="m", system="s", messages=[], tools=[]
        )
    ]

    assert client.attempts == 1, "a 400 must not be retried"
    assert events[-1].type == "error"


async def test_giving_up_still_produces_exactly_one_terminal_event() -> None:
    """Exhausting the retries is a failure, not a contract violation."""
    from omega_ai.anthropic import AnthropicProvider
    from stub_anthropic import StubClient

    client = StubClient(fail_times=99, error=lambda: _Status(503))
    provider = AnthropicProvider(
        client=client,  # type: ignore[arg-type]
        retry=RetryPolicy(attempts=3, base_delay=0.0),
    )

    events = [
        event
        async for event in provider.stream_response(
            model="m", system="s", messages=[], tools=[]
        )
    ]

    assert client.attempts == 3
    assert [e.type for e in events] == ["error"]
    assert events[0].error.stop_reason == "error"


async def test_a_failure_after_output_is_not_retried() -> None:
    """The constraint that makes streaming retry different.

    Restarting a stream that has already emitted tokens produces them twice. So
    once anything has gone upward the retry budget is spent, and the failure is
    reported with the partial content attached - the Tier 1 behaviour, unchanged.
    """
    from omega_ai.anthropic import AnthropicProvider
    from stub_anthropic import StubClient

    client = StubClient(fail_times=0, fail_midstream_after=2, error=lambda: _Status(503))
    provider = AnthropicProvider(
        client=client,  # type: ignore[arg-type]
        retry=RetryPolicy(attempts=3, base_delay=0.0),
    )

    events = [
        event
        async for event in provider.stream_response(
            model="m", system="s", messages=[], tools=[]
        )
    ]

    assert client.attempts == 1, "a mid-stream failure must not restart the stream"
    assert events[-1].type == "error"
    assert [e.type for e in events].count("start") == 1


# ------------------------------------------------------------ auth resolver


async def test_the_key_is_resolved_before_every_request() -> None:
    """A string cannot refresh; a callback can. That is the entire seam."""
    from omega_ai.anthropic import AnthropicProvider
    from stub_anthropic import StubClient

    keys = iter(["first-key", "second-key"])
    client = StubClient()

    async def resolve() -> str:
        return next(keys)

    provider = AnthropicProvider(client=client, auth=resolve)  # type: ignore[arg-type]

    async for _e in provider.stream_response(model="m", system="s", messages=[], tools=[]):
        pass
    assert client.api_key == "first-key"

    async for _e in provider.stream_response(model="m", system="s", messages=[], tools=[]):
        pass
    assert client.api_key == "second-key", "the key was cached instead of re-resolved"


async def test_the_key_is_re_resolved_on_retry() -> None:
    """The case the seam exists for: the token expired, so the retry needs a new one."""
    from omega_ai.anthropic import AnthropicProvider
    from stub_anthropic import StubClient

    resolved: list[str] = []

    async def resolve() -> str:
        resolved.append(f"key-{len(resolved)}")
        return resolved[-1]

    provider = AnthropicProvider(
        client=StubClient(fail_times=1, error=lambda: _Status(503)),  # type: ignore[arg-type]
        auth=resolve,
        retry=RetryPolicy(attempts=3, base_delay=0.0),
    )

    async for _e in provider.stream_response(model="m", system="s", messages=[], tools=[]):
        pass

    assert resolved == ["key-0", "key-1"], "a retry reused the key that had just failed"


def test_a_static_key_still_works() -> None:
    """The resolver is additive; nothing that worked in Tier 1 changed."""
    from omega_ai.anthropic import AnthropicProvider
    from stub_anthropic import StubClient

    provider = AnthropicProvider(client=StubClient(), api_key="static")  # type: ignore[arg-type]
    assert provider is not None


# ------------------------------------------- 429 is two different failures


class _Status429(Exception):
    """Minimal stand-in for an SDK error: a status code and a body."""

    def __init__(self, body: str) -> None:
        super().__init__(body)
        self.status_code = 429


#: Both bodies are real, copied from failures hit while running omega.
OPENAI_NO_CREDITS = (
    "Error code: 429 - {'error': {'message': 'You have no credits remaining. Add "
    "credits to continue using the API at https://platform.openai.com/settings/"
    "organization/billing/.', 'type': 'insufficient_quota', 'code': "
    "'credit_balance_exhausted'}}"
)
ANTHROPIC_ZERO_LIMIT = (
    "Error code: 429 - {'type': 'error', 'error': {'type': 'rate_limit_error', "
    "'message': 'This request would exceed the rate limit you configured in "
    "workspace Anthropic Academy of 0 input tokens per minute.'}}"
)
GENUINE_RATE_LIMIT = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for gpt-5 in "
    "organization org-x on requests per min', 'type': 'requests', 'code': "
    "'rate_limit_exceeded'}}"
)


def test_running_out_of_credits_is_not_retried() -> None:
    """Out of credits is not a rate limit, and waiting cannot fix it.

    Retrying spends three delays to reproduce the same failure, and calling it
    "rate limited" sends the user to the wrong settings page.
    """
    assert is_permanent_quota_failure(_Status429(OPENAI_NO_CREDITS))
    assert not is_retryable(_Status429(OPENAI_NO_CREDITS))


def test_a_workspace_limit_of_zero_is_not_retried() -> None:
    """A limit configured to 0 returns 429 forever. It reads as throttling and
    is really a settings problem."""
    assert is_permanent_quota_failure(_Status429(ANTHROPIC_ZERO_LIMIT))
    assert not is_retryable(_Status429(ANTHROPIC_ZERO_LIMIT))


def test_a_genuine_rate_limit_is_still_retried() -> None:
    """The distinction has to cut one way only.

    Treating every 429 as permanent would undo failure #5 entirely — real
    throttling is exactly what backoff exists for.
    """
    assert not is_permanent_quota_failure(_Status429(GENUINE_RATE_LIMIT))
    assert is_retryable(_Status429(GENUINE_RATE_LIMIT))


def test_the_message_names_the_right_remedy() -> None:
    """An error a user cannot act on is only half reported.

    "rate limited" for a billing problem is worse than unhelpful: it implies
    waiting will work.
    """
    from omega_ai.anthropic import _explain as explain_anthropic
    from omega_ai.openai import _explain as explain_openai

    openai_message = explain_openai(_Status429(OPENAI_NO_CREDITS))  # type: ignore[arg-type]
    assert "credits" in openai_message.lower()
    assert "sk-proj-" in openai_message, "a project key is scoped to one project"
    assert "rate limited" not in openai_message.lower()

    anthropic_message = explain_anthropic(_Status429(ANTHROPIC_ZERO_LIMIT))  # type: ignore[arg-type]
    assert "set to 0" in anthropic_message
    assert "console" in anthropic_message.lower()
