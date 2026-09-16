"""A status line: a spinner, what omega is actually doing, and for how long.

Between pressing enter and the first token arriving, Tier 2 printed **nothing**.
A turn that retried a rate limit three times sat silent for three and a half
seconds and then printed an error, which is indistinguishable from a hang. This
fills that silence.

**The labels are true, not decorative.** Claude Code rotates ~184 whimsical
gerunds — *Percolating*, *Flibbertigibbeting*, *Booping* — chosen for personality
and to avoid a fake progress percentage. That is a defensible choice and this is
not it: every label here names the thing that is actually happening, because the
ten agent events already know, and inventing vocabulary on top of real
information throws the information away.

Neither reference does the whimsy either. Tau animates a braille spinner and
appends elapsed time (`tui/terminal_title.py:15`); Pi's `WorkingIndicatorOptions`
carries `frames` and `intervalMs` and **no message field at all**. Both show
motion plus duration. So does this, with a label added because the events make it
free.

**It is a listener, exactly like `CostTracker`.** No loop or harness change; it
subscribes to events like anything else that watches a run. That is the whole
reason the agent-event vocabulary exists.

**Written to stderr, and only when stderr is a terminal.** The model's output
goes to stdout, so `omega > answer.txt` must not collect spinner frames. A pipe
gets nothing — which is also what makes the tests deterministic.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import sys
import time
from collections.abc import Callable
from types import TracebackType
from typing import IO

from omega_agent.agent_events import AgentEvent
from omega_agent.types import ToolCall

#: Tau's frames, from `tui/terminal_title.py`. Braille rather than `|/-\\` because
#: it animates in place without the glyph changing width.
FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

#: Fast enough to read as motion, slow enough not to spin the CPU.
FRAME_SECONDS = 0.1

#: Elapsed time is hidden below this. A number that appears instantly on every
#: turn is noise; one that appears when something is *taking a while* is a signal.
ELAPSED_AFTER_SECONDS = 1.0

#: Longest a target gets before it is cut. A status line that wraps is worse than
#: none, because a wrapped line cannot be erased with a single `\\r`.
_TARGET_WIDTH = 40

#: Shown while the request is in flight and nothing more specific is known.
#:
#: **Deliberately not "thinking".** Thinking is a distinct, real state in this
#: codebase — `events.py` declares `thinking_start` / `thinking_delta` /
#: `thinking_end`, and the Anthropic adapter emits them when extended thinking is
#: on. Using the word for "waiting on the network" would claim a capability that
#: may not be switched on, and would make the genuine thinking state
#: indistinguishable from an ordinary pause.
WAITING = "working"


def _clip(text: str, width: int = _TARGET_WIDTH) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= width else collapsed[: width - 1] + "…"


def describe(call: ToolCall) -> str:
    """What this tool call is doing, in words, with its target.

    "running tests" beats "run_shell", and both beat *Percolating*. The argument
    is included because *which* file is the part you actually want while waiting.
    """
    arguments = call.arguments

    if call.name == "run_shell":
        command = " ".join(str(arguments.get("command", "")).split())
        if not command:
            return "running a command"
        # The whole command would routinely be wider than the terminal, so a long
        # one degrades to its first word rather than being cut mid-flag.
        if len(command) <= _TARGET_WIDTH:
            return f"running {command}"
        return f"running {command.split()[0]}…"

    path = arguments.get("path")
    if isinstance(path, str):
        verb = {"read_file": "reading", "write_file": "writing", "edit_file": "editing"}.get(
            call.name, call.name
        )
        return f"{verb} {_clip(path)}"

    return call.name


class StatusLine:
    """Draws one self-erasing line on stderr while a turn is in flight.

    The erasing is the fiddly part, and the reason this is a class rather than a
    print statement. Every frame rewrites the same line with a carriage return,
    so the line must be cleared *before* anything else writes to the terminal —
    otherwise the model's first token lands in the middle of a spinner.
    """

    def __init__(
        self,
        stream: IO[str] | None = None,
        *,
        enabled: bool | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stream = stream if stream is not None else sys.stderr
        # Explicit for tests; otherwise a pipe or a redirect gets nothing.
        self._enabled = enabled if enabled is not None else self._stream.isatty()
        self._now = now

        self._label = ""
        self._started_at = 0.0
        self._drawn = 0
        #: Set by `clear`, lifted by `start`. Without it, erasing the line is
        #: pointless: the animation task redraws 100ms later, over whatever
        #: the model has printed in the meantime.
        self._paused = False
        self._frames = itertools.cycle(FRAMES)
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> StatusLine:
        self.start(WAITING)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    def start(self, label: str) -> None:
        """Set the label, and begin animating if anything is watching.

        The label is always updated; the animation is best-effort. Outside a
        running event loop there is nothing to schedule on — a synchronous
        caller, a test — and that is not an error worth raising. A status line
        that could crash a turn would be a poor trade for a spinner.
        """
        self._label = label
        self._started_at = self._now()
        self._paused = False

        if not self._enabled or self._task is not None:
            return
        try:
            self._task = asyncio.get_running_loop().create_task(self._animate())
        except RuntimeError:
            self._task = None

    async def stop(self) -> None:
        """Stop animating and leave the line blank.

        Cancelled rather than flagged, because a turn can end while the task is
        mid-sleep, and a flag would let one more frame land after the prompt has
        already been printed.
        """
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.clear()

    # ---------------------------------------------------------------- drawing

    def clear(self) -> None:
        """Erase the line **and stop drawing** until something starts it again.

        The pause is the whole point, and leaving it out was a real bug. Erasing
        alone achieves nothing, because the animation task wakes 100ms later and
        redraws. While the model is streaming to stdout, every frame begins with
        a carriage return — so each redraw lands on top of the text that just
        arrived, and the answer comes out shuffled and padded instead of in
        order.

        `start` is what lifts the pause, which keeps the rule simple: nothing is
        drawn unless something has just said what is happening.

        Overwritten with spaces rather than an ANSI erase sequence, so it behaves
        the same in a terminal that does not support one.
        """
        self._paused = True
        if not self._enabled or self._drawn == 0:
            return
        self._stream.write("\r" + " " * self._drawn + "\r")
        self._stream.flush()
        self._drawn = 0

    def render(self) -> str:
        """The line as it would appear. Separate from writing it, so it is testable."""
        elapsed = self._now() - self._started_at
        suffix = f" ({elapsed:.0f}s)" if elapsed >= ELAPSED_AFTER_SECONDS else ""
        return f"{next(self._frames)} {self._label}…{suffix}"

    def _draw(self) -> None:
        # Guarded here as well as in `clear`, so "disabled" is a property of the
        # object rather than of one call path. A redirect must collect nothing
        # regardless of who asked for a frame.
        if not self._enabled or self._paused:
            return
        line = self.render()
        # Pad to the previous width, so a shorter label cannot leave a tail behind.
        padding = max(self._drawn - len(line), 0)
        self._stream.write("\r" + line + " " * padding)
        self._stream.flush()
        self._drawn = len(line)

    async def _animate(self) -> None:
        while True:
            self._draw()
            await asyncio.sleep(FRAME_SECONDS)

    # ----------------------------------------------------------------- events

    def observe(self, event: AgentEvent) -> None:
        """Usable directly as a harness listener, like `CostTracker.observe`.

        **Clearing before the renderer prints is this method's real job.** The
        status line and the transcript share one terminal, and whoever writes
        second wins — so anything about to produce output erases the line first.
        """
        if event.type == "turn_start":
            self.start(WAITING)

        elif event.type == "tool_execution_start":
            self.clear()
            self.start(describe(event.tool_call))

        elif event.type == "tool_execution_end":
            self.clear()
            self.start(WAITING)

        elif event.type == "message_update":
            self._observe_stream(event.stream_event.type)

        elif event.type in {"turn_end", "agent_end"}:
            self.clear()

    def _observe_stream(self, kind: str) -> None:
        """React to the twelve provider events travelling inside `message_update`.

        **`thinking` is claimed only when the model is actually thinking.** It is
        a real, separate state — `events.py` declares `thinking_start`,
        `thinking_delta` and `thinking_end`, and `omega_ai/anthropic.py` emits
        them for models with extended thinking on. Using the word as a generic
        "please wait" would be borrowing a term that means something specific,
        which is the failure mode this module was written to avoid. Hence
        `WAITING` for the ordinary case.

        Thinking text is *not* printed by the renderer, so the line can stay up
        while it happens — unlike a text delta, which goes to stdout and must have
        the row to itself.
        """
        if kind == "thinking_start":
            self.clear()
            self.start("thinking")

        elif kind == "thinking_end":
            self.clear()
            self.start(WAITING)

        elif kind == "text_delta":
            # The model has started speaking on stdout. The line goes now and
            # stays gone until the next tool call.
            self.clear()
