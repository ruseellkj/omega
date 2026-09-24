"""`clipboard.py` without a screen.

Every test here chooses its platform and its tools by injection, and the ones
that run a real process run `sh`, `cat`, `false` or `sleep` — never `pbcopy`.
The suite must not touch the developer's clipboard, which is the rule
`conftest.fake_clipboard` enforces for the app and the last test here checks.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from conftest import RecordingClipboard  # the suite's own conftest
from omega_agent.harness import Harness
from omega_ai.fake import FakeProvider, text_turn
from omega_coding import clipboard
from omega_coding.clipboard import SystemClipboard, fits_osc52, is_remote, run_copy, run_paste


def _which(*present: str) -> Callable[[str], str | None]:
    return lambda name: f"/usr/bin/{name}" if name in present else None


# ------------------------------------------------------------ which tool


def test_macos_uses_pbcopy_and_pbpaste() -> None:
    pair = SystemClipboard(platform="darwin", env={}, which=_which()).commands()
    assert pair == (["pbcopy"], ["pbpaste"])


def test_wayland_is_preferred_over_x11_when_both_are_there() -> None:
    """Pi's order (`clipboard.ts`): Wayland first, X11 only without it."""
    both = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    pair = SystemClipboard(platform="linux", env=both, which=_which("wl-copy", "xclip")).commands()
    assert pair is not None and pair[0] == ["wl-copy"]


def test_x11_falls_back_from_xclip_to_xsel() -> None:
    x11 = {"DISPLAY": ":0"}
    with_xclip = SystemClipboard(platform="linux", env=x11, which=_which("xclip")).commands()
    only_xsel = SystemClipboard(platform="linux", env=x11, which=_which("xsel")).commands()
    assert with_xclip is not None and with_xclip[0][0] == "xclip"
    assert only_xsel is not None and only_xsel[0][0] == "xsel"


def test_a_server_with_no_display_has_no_native_clipboard() -> None:
    """The case OSC 52 exists for: a Linux box over SSH, no display at all."""
    assert SystemClipboard(platform="linux", env={}, which=_which("xclip")).commands() is None


def test_windows_has_no_native_entry() -> None:
    """Deliberately: no Windows tool was run here, so none is claimed."""
    assert SystemClipboard(platform="win32", env={}, which=_which()).commands() is None


async def test_no_tool_means_the_write_reports_failure() -> None:
    backend = SystemClipboard(platform="linux", env={}, which=_which())
    assert await backend.write("x") is False
    assert await backend.read() is None


# ------------------------------------------------------------- OSC 52


def test_osc52_is_capped_at_pis_limit() -> None:
    """100,000 encoded characters. Base64 is 4 characters per 3 bytes."""
    assert fits_osc52("a" * 75_000), "exactly 100,000 encoded"
    assert not fits_osc52("a" * 75_001)


def test_the_cap_counts_bytes_not_characters() -> None:
    """`日` is three bytes in UTF-8, so it costs four encoded characters, not 1.33."""
    assert fits_osc52("日" * 25_000)
    assert not fits_osc52("日" * 25_001)


def test_a_remote_session_is_detected_by_pis_variables() -> None:
    assert is_remote({"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"})
    assert is_remote({"MOSH_CONNECTION": "1"})
    assert not is_remote({"TERM": "xterm-256color"})
    assert SystemClipboard(platform="darwin", env={"SSH_CLIENT": "x"}).remote


# ------------------------------------------------- the process, for real


async def test_the_text_reaches_the_tool_intact(tmp_path: Path) -> None:
    """The bytes a tool receives are the UTF-8 of the text, unchanged."""
    target = tmp_path / "copied"
    text = "❯ ✓ … café — 日本\nsecond line"
    assert await run_copy(["sh", "-c", f"cat > {target}"], text, timeout=5)
    assert target.read_text(encoding="utf-8") == text


async def test_reading_returns_what_the_tool_printed(tmp_path: Path) -> None:
    source = tmp_path / "clip"
    source.write_text("❯ back again", encoding="utf-8")
    assert await run_paste(["cat", str(source)], timeout=5) == "❯ back again"


async def test_a_tool_that_fails_is_a_failure() -> None:
    assert await run_copy(["false"], "x", timeout=5) is False
    assert await run_paste(["false"], timeout=5) is None


async def test_a_tool_that_is_missing_is_a_failure_not_a_crash() -> None:
    assert await run_copy(["omega-no-such-tool"], "x", timeout=5) is False
    assert await run_paste(["omega-no-such-tool"], timeout=5) is None


async def test_a_tool_that_hangs_is_given_up_on() -> None:
    """`wl-copy` can hang a naive caller (Pi's comment). A copy that never
    finishes must not hold the app's worker forever."""
    assert await run_copy(["sleep", "10"], "x", timeout=0.2) is False
    assert await run_paste(["sleep", "10"], timeout=0.2) is None


# ------------------------------------------------------------ the guard


def test_the_suite_never_reaches_the_real_clipboard(
    tmp_path: Path, fake_clipboard: RecordingClipboard
) -> None:
    """An app built the way `cli.py` builds it, with no clipboard argument,
    must get the fake. If this fails, some test run has been copying into the
    developer's real clipboard."""
    from omega_coding.tui.app import OmegaApp

    harness = Harness(provider=FakeProvider([text_turn("x")]), model="m", system="s", tools=[])
    assert clipboard.system() is fake_clipboard
    assert OmegaApp(harness)._system_clipboard is fake_clipboard
