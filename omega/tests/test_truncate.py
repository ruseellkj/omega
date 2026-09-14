"""Output budgets - beginner-failure #2."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from omega_coding.truncate import (
    MAX_BYTES,
    MAX_LINES,
    sweep_old_spills,
    truncate_output,
)


def test_short_output_is_untouched_and_writes_no_file() -> None:
    body, info = truncate_output("hello\nworld")

    assert body == "hello\nworld"
    assert info.truncated is False
    assert info.full_output_path is None


def test_line_budget_keeps_the_tail() -> None:
    """Errors and stack traces are at the end, so the end is what survives."""
    text = "\n".join(f"line {i}" for i in range(MAX_LINES + 500))
    body, info = truncate_output(text)

    assert info.truncated is True
    assert info.truncated_by == "lines"
    assert "line 2499" in body, "the last line must survive"
    assert "line 0\n" not in body, "the first line must not"


def test_notice_states_the_range_and_the_path() -> None:
    text = "\n".join(f"line {i}" for i in range(MAX_LINES + 10))
    body, info = truncate_output(text)

    assert f"of {MAX_LINES + 10}" in body, "the model must know the true size"
    assert info.full_output_path is not None
    assert info.full_output_path in body, "the model must be told where the rest is"


def test_full_output_is_recoverable_from_the_spill_file() -> None:
    """Truncation is not data loss if the rest is on disk and the path is given."""
    text = "\n".join(f"line {i}" for i in range(MAX_LINES + 100))
    _body, info = truncate_output(text)

    assert info.full_output_path is not None
    assert Path(info.full_output_path).read_text(encoding="utf-8") == text


def test_byte_budget_applies_to_few_very_long_lines() -> None:
    """One enormous line is a different failure from ten thousand normal ones."""
    text = "x" * (MAX_BYTES + 5_000)
    body, info = truncate_output(text)

    assert info.truncated is True
    assert info.truncated_by == "bytes"
    assert len(body.encode("utf-8")) < len(text.encode("utf-8"))


# ------------------------------------------------- spill files do not pile up


def test_old_spill_files_are_swept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The leak this fixes was found on a real machine, not in a test.

    `_spill` writes with `delete=False` on purpose — the path is handed to the
    model and has to outlive the call. Nothing tidied them afterwards, so a
    working laptop had spill files from three separate days, up to 50KB each.
    """
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    fresh = tmp_path / "omega-aaaa-output.txt"
    stale = tmp_path / "omega-bbbb-output.txt"
    fresh.write_text("recent")
    stale.write_text("ancient")
    os.utime(stale, (0, 0))  # epoch: unambiguously old

    swept = sweep_old_spills()

    assert swept == 1
    assert fresh.exists(), "a file from this week is still useful"
    assert not stale.exists()


def test_the_sweep_only_touches_omega_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prefix is what makes tidying a shared temp directory safe.

    The temp directory belongs to the whole machine. A sweep matching `*.txt`
    would be clearing other programs' files, which is a far worse bug than the
    one it fixes.
    """
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    someone_else = tmp_path / "important-backup.txt"
    also_not_ours = tmp_path / "notomega-data.txt"
    ours = tmp_path / "omega-cccc-shell.txt"
    for path in (someone_else, also_not_ours, ours):
        path.write_text("x")
        os.utime(path, (0, 0))

    assert sweep_old_spills() == 1
    assert someone_else.exists() and also_not_ours.exists()
    assert not ours.exists()


def test_sweeping_a_missing_directory_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup must survive a temp directory it cannot fully control."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "nope"))

    assert sweep_old_spills() == 0
