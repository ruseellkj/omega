"""Retry with backoff — beginner failure #5.

One HTTP 429 and Tier 1 threw away the whole run, including twenty round trips
of progress. The fix is small; *where* it goes is the part worth getting right.

**It lives below the event boundary.** `04-boundaries-and-layout.md:102` lists it
as one of the four promises Layer 1 makes Layer 2: "retries happen *below* this
line and are invisible above it." The loop never learns that a request was
attempted three times, which is why retry can be added, tuned, or replaced
without anything above the adapter changing.

**A stream can only be retried before it has emitted anything.** This is the
constraint that makes streaming retry different from request/response retry: once
500 tokens have gone upward, restarting produces them a second time. So the
adapter counts what it has yielded and stops being allowed to retry after the
first event. A failure after that point is reported as an error event carrying
the partial content — which is the Tier 1 behaviour, unchanged.

**Only some failures are worth retrying.** A 429 or a 503 is the server saying
"not now"; a 400 is the server saying "not ever, that request is wrong". Retrying
the second wastes time and hides the bug.

**This is the only layer, and that was measured.** Both SDKs also retry twice by
default, and the clients were first built without saying otherwise, so each
attempt here hid two more: nine requests for one 429, 32 s instead of 8 s under
`Retry-After: 4`, and a 429 that waiting cannot fix sent three times before the
rule below could refuse it. The adapters now pass `max_retries=0`; Pi turns the
SDK's loop off too (`anthropic-messages.ts:557`). This module took over what only
the SDK had handled: any 5xx, the server's `x-should-retry` verdict, and a
`retry-after-ms` header. A dropped connection arrives as each SDK's own exception
type, so the adapter that knows the type adds that.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

#: The 4xx statuses that still mean "try again": timeout, conflict, too early,
#: rate limit. Every 5xx is retryable too; `is_retryable` says so in one line.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429})

#: Explicitly *not* retryable, listed to make the intent readable: the request
#: itself is wrong, and sending it again changes nothing.
_FATAL_STATUS = frozenset({400, 401, 403, 404, 413, 422})


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How hard to try. Small numbers on purpose.

    A coding agent has a human waiting. Ninety seconds of silent backoff is worse
    than a clear failure they can react to, so the ceiling is deliberately low.
    """

    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0


DEFAULT_RETRY = RetryPolicy()


#: **429 means two different things**, and only one is worth waiting for.
#:
#: Both vendors reuse it for "you are going too fast" *and* for "your account
#: cannot pay for this". The status code alone cannot tell them apart, so these
#: markers are matched against the body. Every one was seen in a real failure,
#: not invented:
#:
#: * `insufficient_quota` / `credit_balance_exhausted` — OpenAI, out of credits
#: * `billing_not_active` — OpenAI, billing disabled
#: * `of 0 input tokens per minute` — Anthropic, a workspace limit configured to
#:   zero, which no amount of waiting will raise
#:
#: Matching vendor prose is brittle, and that is acceptable **because the failure
#: mode is safe**: a marker that stops matching restores the old behaviour —
#: three wasted retries and a slightly wrong message — rather than breaking
#: anything.
_PERMANENT_429_MARKERS = (
    "insufficient_quota",
    "credit_balance_exhausted",
    "billing_not_active",
    "of 0 input tokens per minute",
)


def is_permanent_quota_failure(exc: BaseException) -> bool:
    """A 429 that will still be a 429 in an hour.

    Out of credits is not a rate limit. Retrying it spends the user's time to
    produce an identical failure, and reports "rate limited" for a billing
    problem — which sends them to the wrong settings page.
    """
    detail = str(exc).lower()
    return any(marker in detail for marker in _PERMANENT_429_MARKERS)


def is_retryable(exc: BaseException) -> bool:
    """Whether sending the same request again could plausibly work.

    Checks the status code by attribute rather than by exception class, so it
    works for any SDK's error type — and for the stubs in the tests.
    """
    if isinstance(exc, asyncio.CancelledError):
        # Never. The user asked to stop.
        return False
    status = getattr(exc, "status_code", None)
    if status == 429 and is_permanent_quota_failure(exc):
        # Ahead of the header: these markers come from real failures that repeat,
        # and a server's hint is not worth three more of them.
        return False
    # The service's own verdict beats every rule below. Both SDKs read it first,
    # and so does Pi's loop, which mirrors theirs (`provider-retry.ts:24-26`).
    verdict = _header(exc, "x-should-retry")
    if verdict in {"true", "false"}:
        return verdict == "true"
    if isinstance(exc, ConnectionError | TimeoutError):
        return True

    if not isinstance(status, int):
        return False
    if status in _FATAL_STATUS:
        return False
    # Any 5xx, as both SDKs and both references have it (Pi
    # `provider-retry.ts:33`, Tau `anthropic.py:351`). The list stopped at 504
    # and skipped 501, so it left out Anthropic's 529, "overloaded", among others.
    return status in _RETRYABLE_STATUS or status >= 500


def _header(exc: BaseException, name: str) -> str | None:
    """One header of the response an SDK error carries, if it carries one."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except AttributeError:
        return None
    return value if isinstance(value, str) else None


def retry_after_of(exc: BaseException) -> float | None:
    """The server's own advice, if it gave any.

    A `Retry-After` header beats any backoff curve we might invent: the service
    knows when it will be ready and we are guessing. `retry-after-ms` is read
    first, as both SDKs and Pi (`provider-retry.ts:52`) read it.
    """
    for name, per_second in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        raw = _header(exc, name)
        if raw is None:
            continue
        try:
            return max(0.0, float(raw) / per_second)
        except ValueError:
            # A date-formatted Retry-After is legal and rare; fall back to backoff
            # rather than parsing dates for it.
            continue
    return None


def delay_for(attempt: int, policy: RetryPolicy, *, retry_after: float | None = None) -> float:
    """Seconds to wait before attempt `attempt + 1` (0-based).

    Exponential, capped. No jitter: omega is one client, not a fleet, so the
    thundering-herd problem jitter solves does not exist here — and a
    deterministic delay is one less thing making a test flaky.
    """
    if retry_after is not None:
        return min(retry_after, policy.max_delay)
    # `2.0` not `2`: mypy types `int ** int` as Any, because a negative
    # exponent would make it a float, and that Any spreads to the return.
    return min(policy.base_delay * (2.0**attempt), policy.max_delay)
