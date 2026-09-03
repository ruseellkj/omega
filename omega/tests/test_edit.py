"""The edit tool, and the write race it shares with write_file.

Tier 1 could only rewrite whole files. Fine at fifty lines; hopeless at two
thousand, where handing the model back its own file and asking for a full copy
burns tokens and invites transcription errors.

The design decision worth defending is **uniqueness**. `edit` refuses when
`old_text` matches more than once, rather than replacing the first match. A
model that meant the second occurrence and silently got the first has made a
wrong edit that reports success — the worst available outcome, because nothing
downstream can detect it. Refusing costs one turn.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from omega_agent.tools import Tool, ToolError
from omega_coding.builtin_tools import build_tools
from omega_coding.file_lock import FileLocks
from omega_coding.paths import PathOutsideRoot


def _edit(root: Path, locks: FileLocks | None = None, *, confine: bool = False) -> Tool:
    tools = build_tools(root, locks=locks, confine=confine)
    return next(t for t in tools if t.name == "edit_file")


# ------------------------------------------------------------- the happy path


async def test_it_replaces_an_exact_unique_match(tmp_path: Path) -> None:
    target = tmp_path / "code.py"
    target.write_text("def add(a, b):\n    return a - b\n")

    result = await _edit(tmp_path).execute(
        {"path": "code.py", "old_text": "return a - b", "new_text": "return a + b"}, None
    )

    assert target.read_text() == "def add(a, b):\n    return a + b\n"
    assert "code.py" in result.text


async def test_it_leaves_the_rest_of_the_file_alone(tmp_path: Path) -> None:
    """The whole reason edit exists rather than write."""
    target = tmp_path / "big.txt"
    target.write_text("\n".join(f"line {n}" for n in range(1, 501)))

    await _edit(tmp_path).execute(
        {"path": "big.txt", "old_text": "line 250", "new_text": "CHANGED"}, None
    )

    lines = target.read_text().splitlines()
    assert len(lines) == 500
    assert lines[249] == "CHANGED"
    assert lines[0] == "line 1"
    assert lines[-1] == "line 500"


async def test_an_empty_new_text_deletes(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("keep\nremove me\nkeep\n")

    await _edit(tmp_path).execute(
        {"path": "f.txt", "old_text": "remove me\n", "new_text": ""}, None
    )

    assert target.read_text() == "keep\nkeep\n"


# ------------------------------------------------------------------ the refusals


async def test_it_refuses_an_ambiguous_match_and_says_where(tmp_path: Path) -> None:
    target = tmp_path / "f.py"
    target.write_text("x = 1\nprint(x)\ny = 2\nprint(x)\n")

    with pytest.raises(ToolError) as excinfo:
        await _edit(tmp_path).execute(
            {"path": "f.py", "old_text": "print(x)", "new_text": "pass"}, None
        )

    message = str(excinfo.value)
    assert "2 times" in message
    assert "2, 4" in message, "the model needs the line numbers to disambiguate"
    assert target.read_text() == "x = 1\nprint(x)\ny = 2\nprint(x)\n", "nothing written"


async def test_it_refuses_a_missing_match_and_says_why(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hello\n")

    with pytest.raises(ToolError) as excinfo:
        await _edit(tmp_path).execute(
            {"path": "f.txt", "old_text": "goodbye", "new_text": "x"}, None
        )

    message = str(excinfo.value)
    assert "not found" in message
    assert "whitespace" in message, "the usual cause should be named"


async def test_it_refuses_an_empty_old_text(tmp_path: Path) -> None:
    """`"".count()` would report a match at every position."""
    (tmp_path / "f.txt").write_text("anything")

    with pytest.raises(ToolError) as excinfo:
        await _edit(tmp_path).execute(
            {"path": "f.txt", "old_text": "", "new_text": "x"}, None
        )

    assert "write_file" in str(excinfo.value), "point at the right tool"


async def test_it_refuses_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as excinfo:
        await _edit(tmp_path).execute(
            {"path": "nope.txt", "old_text": "a", "new_text": "b"}, None
        )

    assert "not found" in str(excinfo.value).lower()


async def test_it_follows_the_same_path_rules_as_every_other_file_tool(
    tmp_path: Path,
) -> None:
    """Inherited for free, in both directions.

    `edit_file` never had path logic of its own — it calls whatever the factory
    was given. So when the fence moved out of the tools at Tier 2.5, edit
    followed without being touched, and `--confine` brings it back the same way.
    That is the argument for one resolution point rather than four, tested rather
    than asserted.
    """
    outside = tmp_path / "secret.txt"
    outside.write_text("password")
    root = tmp_path / "project"
    root.mkdir()

    # Default: allowed by the tool, and gated by approval instead.
    await _edit(root).execute(
        {"path": "../secret.txt", "old_text": "password", "new_text": "hacked"}, None
    )
    assert outside.read_text() == "hacked"

    outside.write_text("password")

    with pytest.raises(PathOutsideRoot):
        await _edit(root, confine=True).execute(
            {"path": "../secret.txt", "old_text": "password", "new_text": "hacked"}, None
        )
    assert outside.read_text() == "password"


# ----------------------------------------------------------- content vs details


async def test_the_model_gets_a_summary_and_the_ui_gets_the_diff(tmp_path: Path) -> None:
    """The content/details split, finally earning its keep.

    TIER-1.md admitted nothing read `details` yet — it existed so a diff renderer
    would have somewhere to put a diff while the model still received one line.
    This is that.
    """
    target = tmp_path / "f.py"
    target.write_text("a = 1\nb = 2\n")

    result = await _edit(tmp_path).execute(
        {"path": "f.py", "old_text": "b = 2", "new_text": "b = 3"}, None
    )

    assert "replaced 1 line(s) with 1" in result.text
    assert "diff" not in result.text, "the diff must not cost the model tokens"

    assert result.details is not None
    diff = result.details["diff"]
    assert "-b = 2" in diff
    assert "+b = 3" in diff


# --------------------------------------------------------------- failure #8


async def test_two_concurrent_edits_to_one_file_both_land(tmp_path: Path) -> None:
    """Beginner failure #8, and it is a real race, not a hypothetical one.

    The file tools do their I/O with `asyncio.to_thread`, so there *is* an await
    between the read and the write. Without the per-path lock both edits read the
    original text and the second write discards the first. Remove
    `async with table.for_path(path)` from `edit_file` and this test fails.
    """
    target = tmp_path / "shared.txt"
    target.write_text("A\nB\n")
    edit = _edit(tmp_path, locks=FileLocks())

    await asyncio.gather(
        edit.execute({"path": "shared.txt", "old_text": "A", "new_text": "1"}, None),
        edit.execute({"path": "shared.txt", "old_text": "B", "new_text": "2"}, None),
    )

    assert target.read_text() == "1\n2\n", "one edit was silently discarded"


async def test_a_write_and_an_edit_contend_on_the_same_lock(tmp_path: Path) -> None:
    """Two lock tables would be two independent illusions of safety."""
    locks = FileLocks()
    tools = {t.name: t for t in build_tools(tmp_path, locks=locks)}
    target = tmp_path / "f.txt"
    target.write_text("original\n")

    await asyncio.gather(
        tools["write_file"].execute({"path": "f.txt", "content": "replaced\n"}, None),
        tools["edit_file"].execute(
            {"path": "f.txt", "old_text": "original", "new_text": "edited"}, None
        ),
        return_exceptions=True,
    )

    # Either order is legal; what must not happen is a torn or empty file.
    assert target.read_text() in {"replaced\n", "edited\n"}
