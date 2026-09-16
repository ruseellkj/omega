"""Search tools — `list_files`, `find_files`, `search_files`.

**Why these exist when one reference does without them.** Tau ships four tools —
read, write, edit, bash — and no search at all; the model is expected to shell
out. That is a defensible position, and omega held it until a live run made the
cost visible: asked how many lines `loop.py` had, the model called
`read_file(limit=1000)` to pull the whole file into the context, then
`run_shell("wc -l")`. Both answers were available for a fraction of the tokens.
Pi has `find`, `grep` and `ls` for this reason.

**Pure Python, no ripgrep.** Pi shells out to `rg` and downloads the binary if it
is missing (`grep.ts:172`). omega's suite runs offline against no external
binary, and buying a search tool with that property would be a poor trade.

The tests that matter are not the ones proving a match is found. They are:

* **a symlink cannot walk out of the root** — `rglob` follows directory symlinks,
  so a link to `$HOME` inside the repo would enumerate the home directory with no
  outside-root path ever handed to the gate
* **the gate still sees these calls** — a new tool absent from `_PATH_ARGUMENTS`
  loses its location check silently
* **one enormous line cannot eat the whole budget**
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omega_agent.hooks import ToolCallDecision
from omega_agent.tools import ToolError
from omega_agent.types import ToolCall
from omega_coding.approval import ApprovalPolicy
from omega_coding.builtin_tools import build_tools


def _tools(root: Path) -> dict[str, object]:
    return {tool.name: tool for tool in build_tools(root)}


async def _run(root: Path, name: str, **arguments: object) -> str:
    tool = _tools(root)[name]
    result = await tool.execute(arguments, None)  # type: ignore[attr-defined]
    return result.text


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "loop.py").write_text("def run():\n    return 42\n")
    (tmp_path / "src" / "harness.py").write_text("class Harness:\n    pass\n")
    (tmp_path / "README.md").write_text("# omega\nA coding agent.\n")
    return tmp_path


# ------------------------------------------------------------------ list_files


async def test_list_files_shows_a_directory(project: Path) -> None:
    listing = await _run(project, "list_files", path=".")

    assert "src" in listing
    assert "README.md" in listing


async def test_list_files_marks_directories(project: Path) -> None:
    """A name alone does not say whether it can be read or descended into."""
    listing = await _run(project, "list_files", path=".")

    assert "src/" in listing, "a trailing slash is the cheapest possible signal"


# ------------------------------------------------------------------ find_files


async def test_find_files_matches_a_glob(project: Path) -> None:
    found = await _run(project, "find_files", pattern="**/*.py")

    assert "src/loop.py" in found
    assert "src/harness.py" in found
    assert "README.md" not in found


async def test_find_files_reports_when_nothing_matches(project: Path) -> None:
    """Silence reads as a broken tool. An empty result has to say so."""
    found = await _run(project, "find_files", pattern="**/*.rs")

    assert "no files" in found.lower()


# ---------------------------------------------------------------- search_files


async def test_search_files_finds_a_line_and_says_where(project: Path) -> None:
    hits = await _run(project, "search_files", pattern="return 42")

    assert "src/loop.py" in hits
    assert "2" in hits, "the line number is most of the value"


async def test_search_files_takes_a_regex(project: Path) -> None:
    hits = await _run(project, "search_files", pattern=r"class \w+:")

    assert "harness.py" in hits


async def test_search_files_can_ignore_case(project: Path) -> None:
    hits = await _run(project, "search_files", pattern="a CODING agent", ignore_case=True)

    assert "README.md" in hits


async def test_search_files_can_filter_by_glob(project: Path) -> None:
    hits = await _run(project, "search_files", pattern="omega", glob="*.md")

    assert "README.md" in hits


async def test_a_bad_regex_says_what_was_wrong(project: Path) -> None:
    """A typo in a pattern must name itself.

    The tool raises `ToolError`; `execute_tool_call` is what turns that into an
    ordinary result the model reads. Asserting on the raise is asserting at the
    right layer — the message is the part that decides whether the model retries
    once or five times.
    """
    with pytest.raises(ToolError) as raised:
        await _run(project, "search_files", pattern="[unclosed")

    assert "[unclosed" in str(raised.value)
    assert "unterminated" in str(raised.value).lower()


# ------------------------------------------------- the properties that matter


async def test_a_symlink_cannot_walk_out_of_the_root(tmp_path: Path) -> None:
    """**The escape this file exists to prevent.**

    `Path.rglob` follows directory symlinks. A link inside the project pointing
    at somewhere sensitive would enumerate it with no outside-root path ever
    reaching the approval gate — the gate cannot ask about a path it never sees.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "own.py").write_text("mine\n")

    secret_dir = tmp_path / "elsewhere"
    secret_dir.mkdir()
    (secret_dir / "id_rsa").write_text("PRIVATE KEY MATERIAL\n")
    (project / "link").symlink_to(secret_dir, target_is_directory=True)

    found = await _run(project, "find_files", pattern="**/*")
    hits = await _run(project, "search_files", pattern="PRIVATE KEY MATERIAL")

    assert "id_rsa" not in found, "the link was followed out of the root"
    assert "id_rsa" not in hits, "and its contents were searched"
    assert hits.startswith("no matches"), "nothing outside the root was reachable"
    assert "own.py" in found, "and ordinary files still work"


async def test_the_gate_still_asks_about_a_search_outside_the_root(tmp_path: Path) -> None:
    """A new tool missing from `_PATH_ARGUMENTS` loses its location check in
    silence — no error, no prompt, just an unguarded read."""
    asked: list[str] = []

    async def record(request: object) -> str:
        asked.append(getattr(request, "tool_name", "?"))
        return "deny"

    policy = ApprovalPolicy(tmp_path, asker=record)  # type: ignore[arg-type]
    outside = str(tmp_path.parent / "somewhere-else")

    decision = await policy(
        ToolCall(id="1", name="search_files", arguments={"pattern": "x", "path": outside})
    )

    assert asked == ["search_files"], "the gate never saw it"
    assert decision.allowed is False


async def test_searching_inside_the_root_does_not_prompt(tmp_path: Path) -> None:
    """Read-only and in-root: prompting here would make the gate noise people
    learn to click through."""

    async def never(request: object) -> str:  # pragma: no cover - must not run
        raise AssertionError("a read inside the root must not prompt")

    policy = ApprovalPolicy(tmp_path, asker=never)  # type: ignore[arg-type]

    for name in ("list_files", "find_files", "search_files"):
        decision: ToolCallDecision = await policy(
            ToolCall(id="1", name=name, arguments={"pattern": "x", "path": str(tmp_path)})
        )
        assert decision.allowed is True, name


async def test_one_enormous_line_cannot_eat_the_whole_budget(tmp_path: Path) -> None:
    """A single minified file is one 2 MB line. Capping only the total lets that
    one hit crowd out every other file's matches."""
    (tmp_path / "min.js").write_text("needle" + "x" * 500_000 + "\n")
    (tmp_path / "real.py").write_text("needle here\n")

    hits = await _run(tmp_path, "search_files", pattern="needle")

    assert "real.py" in hits, "the useful hit survived the huge one"
    assert len(hits) < 100_000, "and the result is still readable"


async def test_binary_files_are_skipped_not_fatal(tmp_path: Path) -> None:
    """A repo has images. A decode error is a signal to move on."""
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe binary")
    (tmp_path / "code.py").write_text("findme\n")

    hits = await _run(tmp_path, "search_files", pattern="findme")

    assert "code.py" in hits


async def test_results_are_capped(tmp_path: Path) -> None:
    for i in range(50):
        (tmp_path / f"f{i}.txt").write_text("match\n")

    hits = await _run(tmp_path, "search_files", pattern="match", limit=10)

    assert hits.count("match") <= 15, "the limit is respected"
    assert "limit" in hits.lower(), "and the truncation is stated"
