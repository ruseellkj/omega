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

from collections.abc import Callable
from typing import Any

import httpx
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


@pytest.mark.parametrize("status", [501, 520, 529])
def test_a_5xx_outside_the_list_is_still_retried(status: int) -> None:
    """Anthropic answers 529 when it is overloaded, and the list stopped at 504, skipping 501.

    Nothing showed it while the SDK retried underneath: a 529 still got three
    requests, all of them the SDK's. Both SDKs retry any status from 500 up, and
    so do both references (Pi `provider-retry.ts:28-33`, Tau `anthropic.py:351`).
    """
    assert is_retryable(_Status(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_a_bad_request_is_never_retried(status: int) -> None:
    """Sending it again changes nothing and hides the actual bug."""
    assert is_retryable(_Status(status)) is False


def test_the_servers_own_retry_verdict_wins() -> None:
    """`x-should-retry` is the service saying whether another attempt can work.

    Both SDKs obey it before looking at the status, and Pi's loop, which mirrors
    theirs, does the same (`provider-retry.ts:24-26`). While the SDK retried, it
    was obeyed down there; omega's layer has to read it now.
    """
    assert is_retryable(_Status(503, {"x-should-retry": "false"})) is False
    assert is_retryable(_Status(400, {"x-should-retry": "true"})) is True


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


def test_a_retry_after_ms_header_is_read_first() -> None:
    """Both SDKs read `retry-after-ms` before `retry-after`, and so does Pi
    (`provider-retry.ts:52`). While the SDK retried, it read the header down
    there; once its loop was off, nothing did."""
    assert retry_after_of(_Status(429, {"retry-after-ms": "1500"})) == 1.5
    assert retry_after_of(_Status(429, {"retry-after-ms": "250", "retry-after": "9"})) == 0.25


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

    def __init__(self, body: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(body)
        self.status_code = 429
        self.response = type("R", (), {"headers": headers or {}})()


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


def test_a_429_that_waiting_cannot_fix_stays_refused_whatever_the_header_says() -> None:
    """`x-should-retry: true` must not reopen these. The markers were copied
    from real failures; what headers those real 429s carry nobody here can see,
    and a server's hint is not worth three retries of a failure known to repeat."""
    for body in (OPENAI_NO_CREDITS, ANTHROPIC_ZERO_LIMIT):
        assert not is_retryable(_Status429(body, {"x-should-retry": "true"}))


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


# ------------------------------------- one retry layer, through the real clients
#
# Every test above hands the adapter a stub client, so none of them could see
# the SDK's own retry loop. These build the client exactly as omega does and
# swap only its transport, so the requests counted are the ones that would go
# over the wire.

RATE_LIMITED: dict[str, dict[str, Any]] = {
    "anthropic": {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
    "openai": {
        "error": {"message": "slow down", "type": "requests", "code": "rate_limit_exceeded"}
    },
}

#: The two real bodies above, as the JSON the SDK receives.
WAITING_CANNOT_FIX: dict[str, dict[str, Any]] = {
    "anthropic": {
        "type": "error",
        "error": {
            "type": "rate_limit_error",
            "message": "This request would exceed the rate limit you configured in "
            "workspace Anthropic Academy of 0 input tokens per minute.",
        },
    },
    "openai": {
        "error": {
            "message": "You have no credits remaining.",
            "type": "insufficient_quota",
            "code": "credit_balance_exhausted",
        }
    },
}

OVERLOADED = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}


def _reply(
    status: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: httpx.Response(status, json=body, headers=headers)


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _aim_at(
    monkeypatch: pytest.MonkeyPatch,
    vendor: str,
    respond: Callable[[httpx.Request], httpx.Response],
) -> tuple[Any, list[httpx.Request]]:
    """omega's provider, holding the client omega builds, pointed at `respond`.

    Only the transport and the address change. Every argument omega passes to
    the SDK is kept, so a retry setting omega adds, or forgets, is exactly what
    gets counted.
    """
    import anthropic
    import openai

    from omega_ai import anthropic as anthropic_adapter
    from omega_ai import openai as openai_adapter

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond(request)

    module: Any = anthropic_adapter if vendor == "anthropic" else openai_adapter
    sdk: Any = anthropic.AsyncAnthropic if vendor == "anthropic" else openai.AsyncOpenAI

    def build(**kwargs: Any) -> Any:
        kwargs["base_url"] = "http://sdk.test"
        transport = httpx.MockTransport(handler)
        return sdk(**kwargs, http_client=httpx.AsyncClient(transport=transport))

    monkeypatch.setattr(module, sdk.__name__, build)
    fast = RetryPolicy(attempts=3, base_delay=0.0)
    if vendor == "anthropic":
        return anthropic_adapter.AnthropicProvider(api_key="k", retry=fast), seen
    return openai_adapter.OpenAIProvider(api_key="k", retry=fast), seen


async def _drain(provider: Any) -> list[Any]:
    return [
        event
        async for event in provider.stream_response(model="m", system="s", messages=[], tools=[])
    ]


@pytest.mark.parametrize("vendor", ["anthropic", "openai"])
async def test_one_rate_limit_costs_three_requests_not_nine(
    monkeypatch: pytest.MonkeyPatch, vendor: str
) -> None:
    """**The SDK retried underneath omega. Measured before the fix.**

    Both SDKs retry twice by default (`DEFAULT_MAX_RETRIES = 2`), and omega built
    its clients without `max_retries`. So each of omega's three attempts was
    three requests: nine in all, about 5.5 s on localhost where the policy
    allows 1.5 s, and 32 s rather than 8 s when the server sent
    `Retry-After: 4`. Pi turns the SDK's loop off for the same reason
    (`anthropic-messages.ts:557`, `openai-responses.ts:147`).
    """
    provider, seen = _aim_at(monkeypatch, vendor, _reply(429, RATE_LIMITED[vendor]))

    events = await _drain(provider)

    assert events[-1].type == "error"
    assert len(seen) == 3, f"one rate-limited call sent {len(seen)} requests"


@pytest.mark.parametrize("vendor", ["anthropic", "openai"])
async def test_a_429_that_waiting_cannot_fix_is_sent_once(
    monkeypatch: pytest.MonkeyPatch, vendor: str
) -> None:
    """`is_permanent_quota_failure` says never retry this, and it was right, but
    the SDK had already sent the request three times before the rule ran."""
    provider, seen = _aim_at(monkeypatch, vendor, _reply(429, WAITING_CANNOT_FIX[vendor]))

    await _drain(provider)

    assert len(seen) == 1, f"a 429 that waiting cannot fix was sent {len(seen)} times"


@pytest.mark.parametrize("vendor", ["anthropic", "openai"])
async def test_a_server_that_says_do_not_retry_is_obeyed(
    monkeypatch: pytest.MonkeyPatch, vendor: str
) -> None:
    """The SDK obeyed `x-should-retry: false` and sent nothing more, and then
    omega's layer, which never read the header, sent the request twice more."""
    respond = _reply(429, RATE_LIMITED[vendor], {"x-should-retry": "false"})
    provider, seen = _aim_at(monkeypatch, vendor, respond)

    await _drain(provider)

    assert len(seen) == 1, f"the server said stop and got {len(seen)} requests"


@pytest.mark.parametrize(
    ("vendor", "respond"),
    [
        ("anthropic", _reply(529, OVERLOADED)),
        ("openai", _reply(529, OVERLOADED)),
        ("anthropic", _refused),
        ("openai", _refused),
    ],
    ids=["anthropic-529", "openai-529", "anthropic-refused", "openai-refused"],
)
async def test_what_the_sdk_used_to_retry_omega_still_retries(
    monkeypatch: pytest.MonkeyPatch,
    vendor: str,
    respond: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Turning the SDK's loop off hands its whole job to omega's. **This one
    passed before the fix and failed after the first half of it.**

    Two failures were retried *only* by the SDK. A 5xx outside omega's list,
    such as 501 or Anthropic's 529 "overloaded", was one. A refused connection
    reaches omega as the SDK's `APIConnectionError`, which is not the builtin
    `ConnectionError` that `is_retryable` knows. With `max_retries=0` and
    nothing else, each got one request.
    """
    provider, seen = _aim_at(monkeypatch, vendor, respond)

    events = await _drain(provider)

    assert events[-1].type == "error"
    assert len(seen) == 3, f"expected omega's three attempts, got {len(seen)}"
