"""What the run has cost so far — anatomy.md #28.

Tier 1 captured `Usage` on every response and did nothing with it. This adds the
counter, which is a few lines, and one decision that is worth more than the code:

**omega ships no price table.**

A hardcoded table of dollars-per-million-tokens is wrong the moment prices change
or a model is renamed, and a *confidently wrong* cost figure is worse than none —
it gets believed, and budgets get planned on it. So token counts are always
reported, and dollars only when someone has supplied a price. `dollars` returns
`None` otherwise, and the CLI renders tokens alone rather than inventing a number.

Prices come from the environment (`OMEGA_PRICE_INPUT` / `OMEGA_PRICE_OUTPUT`, in
dollars per million tokens) or are passed in directly. Tau's provider catalog is
the real answer to this and it is a Tau-parity item, not a Tier 2 one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from omega_agent.agent_events import AgentEvent
from omega_agent.types import AssistantMessage


@dataclass(frozen=True, slots=True)
class Price:
    """Dollars per million tokens."""

    input_per_mtok: float
    output_per_mtok: float


def price_from_env() -> Price | None:
    """A price if the environment supplies one, otherwise nothing.

    Both halves are required. A price with only the input side would silently
    under-report by roughly the output ratio, which for a coding agent is most of
    the bill.
    """
    raw_input = os.environ.get("OMEGA_PRICE_INPUT")
    raw_output = os.environ.get("OMEGA_PRICE_OUTPUT")
    if raw_input is None or raw_output is None:
        return None
    try:
        return Price(input_per_mtok=float(raw_input), output_per_mtok=float(raw_output))
    except ValueError:
        return None


class CostTracker:
    """Sums token usage across a run, and prices it if it can."""

    __slots__ = (
        "_price",
        "cache_read_tokens",
        "cache_write_tokens",
        "input_tokens",
        "output_tokens",
        "turns",
    )

    def __init__(self, price: Price | None = None) -> None:
        self._price = price
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_write_tokens = 0
        self.cache_read_tokens = 0
        self.turns = 0

    def record(self, message: AssistantMessage) -> None:
        self.input_tokens += message.usage.input
        self.output_tokens += message.usage.output
        self.cache_write_tokens += message.usage.cache_write
        self.cache_read_tokens += message.usage.cache_read
        self.turns += 1

    @property
    def cached_fraction(self) -> float:
        """How much of the input arrived from cache, 0.0 to 1.0.

        **The number that says whether failure #9 is actually fixed.** Sending a
        cache marker is easy and proves nothing; this rising above zero on the
        second turn of a session is the evidence.

        Expect zero on the first turn of every session — an entry has to be
        written before it can be read, and the ephemeral lifetime is minutes, so
        a fresh session always starts cold. Zero here is not a fault.
        """
        if self.input_tokens <= 0:
            return 0.0
        return self.cache_read_tokens / self.input_tokens

    def observe(self, event: AgentEvent) -> None:
        """Usable directly as a harness listener.

        Counts on `message_end`, which fires exactly once per model response -
        including failed ones, because a failed response still billed for its
        input.
        """
        if event.type == "message_end":
            self.record(event.message)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def dollars(self) -> float | None:
        """The bill, or `None` when no price is known. Never a guess."""
        if self._price is None:
            return None
        return (
            self.input_tokens * self._price.input_per_mtok
            + self.output_tokens * self._price.output_per_mtok
        ) / 1_000_000

    def __str__(self) -> str:
        tokens = f"{self.input_tokens:,} in / {self.output_tokens:,} out"
        dollars = self.dollars
        return tokens if dollars is None else f"{tokens} / ${dollars:.4f}"
