"""Output budgets - beginner-failure #2."""

from __future__ import annotations

from pathlib import Path

from omega.truncate import MAX_BYTES, MAX_LINES, truncate_output


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
