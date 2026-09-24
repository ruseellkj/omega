"""The system clipboard, reached the way the platform expects.

## Why this exists at all

Textual can highlight a mouse selection, but its only way to copy is one escape
sequence: `App.copy_to_clipboard` writes OSC 52 and nothing else
(`textual/app.py:1770-1786` in 8.2.8), and its own docstring says *"This does not
work on macOS Terminal"*. So in omega a selection could be highlighted and
"copied" and still never reach the clipboard.

## The order, taken from Pi

Pi's `copyToClipboard` (`packages/coding-agent/src/utils/clipboard.ts`) tries the
platform's own tool first and falls back to OSC 52 only when that fails or the
session is remote. omega keeps that order, for three reasons:

- **A tool reports whether it worked, and an escape sequence does not.** `pbcopy`
  exits 0 or it doesn't. OSC 52 is written to the terminal and vanishes. The
  terminal may honour it, ignore it, or ask the user first. Only the first case
  lets the status line say "copied" and mean it.
- **Over SSH the tool writes the wrong machine's clipboard.** `pbcopy` on the
  remote host fills a clipboard nobody is looking at. OSC 52 travels back down
  the connection to the terminal you are actually sitting at, which is why a
  remote session always sends it as well.
- **OSC 52 is capped** at 100,000 encoded characters (`fits_osc52`), Pi's
  `MAX_OSC52_ENCODED_LENGTH`. Pi's comment is the reason: very large payloads can
  desynchronize terminal rendering. Textual has no cap, so selecting a whole
  long transcript would have sent megabytes of base64 into the screen.

## What was measured, and what was not

The macOS pair was run, not assumed: `❯ ✓ … café — 日本` goes through
`pbcopy` → `pbpaste` unchanged, including with `LANG` unset. So no locale is
forced on the child; a setting kept "just in case" would read as load-bearing.
The Linux tools are Pi's choices and Pi's order (Wayland, then X11, then
Termux). They were not run here. Windows has no native entry, so a copy on
Windows goes through OSC 52 alone. Whether it lands is then up to the terminal,
and the status line says so rather than claiming it.

## What it does not import

No Textual. OSC 52 itself is sent by the app, through Textual's own
`copy_to_clipboard`, because only the app owns the terminal. This module only
answers whether the text fits. That is also what lets the tests run every
branch here without a screen.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from typing import Protocol

#: Pi's cap on an OSC 52 payload, in *encoded* characters (`clipboard.ts`).
OSC52_LIMIT = 100_000

#: How long a clipboard tool may take before it counts as failed. Pi's figure.
#: `wl-copy` forks to keep serving the selection, and a pipe left open by that
#: child is how a copy hangs, which is why nothing here waits on its output.
TIMEOUT_SECONDS = 5.0


class Clipboard(Protocol):
    """What the app needs: put text somewhere, and get it back."""

    @property
    def remote(self) -> bool:
        """Whether a native copy lands on the wrong machine, so OSC 52 is needed too."""
        ...

    async def write(self, text: str) -> bool:
        """Copy `text`. True only when a tool confirmed it."""
        ...

    async def read(self) -> str | None:
        """The clipboard's text, or None when there is no way to read it."""
        ...


def is_remote(env: Mapping[str, str] | None = None) -> bool:
    """Whether this is an SSH or mosh session. Pi's three variables."""
    env = os.environ if env is None else env
    return any(env.get(name) for name in ("SSH_CONNECTION", "SSH_CLIENT", "MOSH_CONNECTION"))


def fits_osc52(text: str) -> bool:
    """Whether `text`, base64-encoded, is under Pi's cap.

    A size check rather than the escape itself: the app sends OSC 52 through
    Textual's own `copy_to_clipboard`, which already knows how to reach the
    terminal, so there is no second copy of that sequence to keep in step.
    """
    return 4 * -(-len(text.encode("utf-8")) // 3) <= OSC52_LIMIT


class SystemClipboard:
    """The platform's own clipboard tool, chosen once per call.

    Everything it looks at is injectable, so a test can be macOS, Wayland or a
    bare server without being any of them.
    """

    def __init__(
        self,
        *,
        platform: str | None = None,
        env: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._platform = sys.platform if platform is None else platform
        self._env = os.environ if env is None else env
        self._which = which
        self._timeout = timeout

    @property
    def remote(self) -> bool:
        return is_remote(self._env)

    def commands(self) -> tuple[list[str], list[str]] | None:
        """The (copy, paste) pair for this machine, or None if it has neither."""
        if self._platform == "darwin":
            return ["pbcopy"], ["pbpaste"]
        if not self._platform.startswith("linux"):
            return None
        if self._env.get("TERMUX_VERSION") and self._which("termux-clipboard-set"):
            return ["termux-clipboard-set"], ["termux-clipboard-get"]
        if self._env.get("WAYLAND_DISPLAY") and self._which("wl-copy"):
            return ["wl-copy"], ["wl-paste", "--no-newline"]
        if self._env.get("DISPLAY"):
            if self._which("xclip"):
                return (
                    ["xclip", "-selection", "clipboard"],
                    ["xclip", "-selection", "clipboard", "-o"],
                )
            if self._which("xsel"):
                return ["xsel", "--clipboard", "--input"], ["xsel", "--clipboard", "--output"]
        return None

    async def write(self, text: str) -> bool:
        pair = self.commands()
        if pair is None:
            return False
        return await run_copy(pair[0], text, timeout=self._timeout)

    async def read(self) -> str | None:
        pair = self.commands()
        if pair is None:
            return None
        return await run_paste(pair[1], timeout=self._timeout)


async def run_copy(command: list[str], text: str, *, timeout: float) -> bool:
    """Feed `text` to `command` on stdin. True on a clean exit within `timeout`.

    stdout and stderr go to `DEVNULL` rather than a pipe. A pipe is something
    to wait on, and `wl-copy`'s forked child holds it open, which is the hang
    Pi's comment describes.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return False
    try:
        await asyncio.wait_for(process.communicate(text.encode("utf-8")), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        return False
    return process.returncode == 0


async def run_paste(command: list[str], *, timeout: float) -> str | None:
    """Run `command` and return what it printed, or None if it failed."""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        return None
    if process.returncode != 0:
        return None
    return output.decode("utf-8", errors="replace")


def system() -> Clipboard:
    """The clipboard the app uses unless it is handed another.

    A function rather than a module-level instance so the test suite can
    replace it in one place. `tests/conftest.py` does, because the clipboard is
    user state and a test run that overwrote it would be a trace left on the
    laptop.
    """
    return SystemClipboard()


__all__ = [
    "OSC52_LIMIT",
    "Clipboard",
    "SystemClipboard",
    "fits_osc52",
    "is_remote",
    "run_copy",
    "run_paste",
    "system",
]
