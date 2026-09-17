"""The four file/shell tools.

Every test builds its tools rooted at `tmp_path`. That is not test hygiene — it
is the point of the factory. Tier 1's module-level constants captured the
current directory at import, which meant a test could not choose its own fence
and `--fake` in a temp directory got the wrong one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from omega_agent.tools import Tool, ToolError
from omega_coding.builtin_tools import build_tools
from omega_coding.paths import PathOutsideRoot


def _tools(root: Path, **kwargs: object) -> dict[str, Tool]:
    return {tool.name: tool for tool in build_tools(root, **kwargs)}  # type: ignore[arg-type]


# ------------------------------------------------------------------ read/write


async def test_read_file_returns_contents(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("contents here", encoding="utf-8")

    result = await _tools(tmp_path)["read_file"].execute({"path": "hello.txt"}, None)
    assert result.text == "contents here"


async def test_read_file_raises_a_message_the_model_can_act_on(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as excinfo:
        await _tools(tmp_path)["read_file"].execute({"path": "nope.txt"}, None)

    assert "not found" in str(excinfo.value).lower()


async def test_write_file_creates_parent_directories(tmp_path: Path) -> None:
    result = await _tools(tmp_path)["write_file"].execute(
        {"path": "nested/deep/out.txt", "content": "written"}, None
    )

    assert (tmp_path / "nested" / "deep" / "out.txt").read_text(encoding="utf-8") == "written"
    assert "7 chars" in result.text


# --------------------------------------------- outside the root: the tools allow it


async def test_the_tools_no_longer_refuse_paths_outside_the_root(tmp_path: Path) -> None:
    """Tier 2.5: the fence came out of the tools.

    This test asserted the opposite at Tier 2, and the reversal is the whole
    change. It is **not** a weakening on its own: `test_approval.py` now proves
    the same read is gated, which is where the fence's job went. Read the two
    together or neither means anything.
    """
    (tmp_path / "secret.txt").write_text("password")
    root = tmp_path / "project"
    root.mkdir()

    result = await _tools(root)["read_file"].execute({"path": "../secret.txt"}, None)
    assert result.text == "password"


async def test_confine_restores_the_hard_fence(tmp_path: Path) -> None:
    """`--confine` is the Tier 2 behaviour, kept for people who want it."""
    (tmp_path / "secret.txt").write_text("password")
    root = tmp_path / "project"
    root.mkdir()

    with pytest.raises(PathOutsideRoot):
        await _tools(root, confine=True)["read_file"].execute({"path": "../secret.txt"}, None)

    with pytest.raises(PathOutsideRoot):
        await _tools(root, confine=True)["write_file"].execute(
            {"path": "../escaped.txt", "content": "x"}, None
        )

    assert not (tmp_path / "escaped.txt").exists()


async def test_a_refused_write_creates_no_directories(tmp_path: Path) -> None:
    """Tier 1 ran mkdir(parents=True) before any check.

    A refusal that has already created `../a/b/c` on the way to being refused is
    not a refusal. The check has to gate the mkdir, not just the write. Now
    tested under `--confine`, since that is where a refusal still comes from the
    tool rather than from the gate.
    """
    root = tmp_path / "project"
    root.mkdir()

    with pytest.raises(PathOutsideRoot):
        await _tools(root, confine=True)["write_file"].execute(
            {"path": "../outside/a/b/c.txt", "content": "x"}, None
        )

    assert not (tmp_path / "outside").exists(), "directories were created outside the root"


async def test_a_denied_write_creates_no_directories_either(tmp_path: Path) -> None:
    """The same property, on the path that now matters more.

    Without `--confine` a write outside the root is stopped by the *gate*, before
    the tool runs at all — so `mkdir` is never reached. Worth pinning: the gate
    returning "not allowed" and the tool having already made three directories
    would be the Tier 1 bug wearing a hook.
    """
    from omega_agent.types import ToolCall
    from omega_coding.approval import ApprovalPolicy

    root = tmp_path / "project"
    root.mkdir()

    policy = ApprovalPolicy(root, asker=None)  # no channel: everything is denied
    decision = await policy(
        ToolCall(
            id="1",
            name="write_file",
            arguments={"path": "../outside/a/b/c.txt", "content": "x"},
        )
    )

    assert not decision.allowed
    assert not (tmp_path / "outside").exists()


# ---------------------------------------------------- reading part of a large file


async def test_offset_and_limit_take_a_window(tmp_path: Path) -> None:
    """Borrowed from both references, and absent at Tier 2.

    Without this, a file past the truncation budget is unreadable beyond its tail
    and the model's only recourse is `sed` through the shell — which the tool
    guidelines tell it not to reach for. Telling it not to while leaving it no
    alternative is how a guideline gets ignored.
    """
    (tmp_path / "many.txt").write_text("\n".join(f"line {n}" for n in range(1, 101)))
    read = _tools(tmp_path)["read_file"]

    result = await read.execute({"path": "many.txt", "offset": 10, "limit": 3}, None)

    assert result.text.strip() == "line 10\nline 11\nline 12"
    assert result.details is not None
    assert result.details["total_lines"] == 100
    assert result.details["next_offset"] == 13, "the model needs to know to continue"


async def test_reading_to_the_end_reports_no_next_offset(tmp_path: Path) -> None:
    """`next_offset: None` is how "you have it all" is said."""
    (tmp_path / "few.txt").write_text("a\nb\nc")
    read = _tools(tmp_path)["read_file"]

    result = await read.execute({"path": "few.txt", "offset": 2}, None)

    assert result.text == "b\nc"
    assert result.details is not None
    assert result.details["next_offset"] is None


async def test_each_root_gets_its_own_fence(tmp_path: Path) -> None:
    """Two roots in one process — impossible with import-time constants."""
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (one / "a.txt").write_text("from one")
    (two / "a.txt").write_text("from two")

    assert (await _tools(one)["read_file"].execute({"path": "a.txt"}, None)).text == "from one"
    assert (await _tools(two)["read_file"].execute({"path": "a.txt"}, None)).text == "from two"


# ----------------------------------------------------------------------- shell


async def test_run_shell_returns_combined_output(tmp_path: Path) -> None:
    result = await _tools(tmp_path)["run_shell"].execute(
        {"command": "echo out; echo err 1>&2"}, None
    )

    assert "out" in result.text
    assert "err" in result.text, "stderr must be captured too"


async def test_failing_command_carries_its_output_into_the_error(tmp_path: Path) -> None:
    """A bare 'exited with code 1' tells the model nothing and it retries blindly."""
    with pytest.raises(ToolError) as excinfo:
        await _tools(tmp_path)["run_shell"].execute(
            {"command": "echo why-it-failed; exit 3"}, None
        )

    message = str(excinfo.value)
    assert "why-it-failed" in message, "the output is the useful part of the failure"
    assert "code 3" in message


async def test_the_shell_starts_in_the_root(tmp_path: Path) -> None:
    result = await _tools(tmp_path)["run_shell"].execute({"command": "pwd"}, None)
    assert str(tmp_path.resolve()) in result.text


async def test_a_hung_command_is_killed(tmp_path: Path) -> None:
    """A TIER-1.md rough edge: run_shell had no timeout, so a hang was forever."""
    with pytest.raises(ToolError) as excinfo:
        await _tools(tmp_path, timeout=0.3)["run_shell"].execute({"command": "sleep 30"}, None)

    assert "timed out" in str(excinfo.value)


async def test_a_running_command_honours_cancellation(tmp_path: Path) -> None:
    """Ctrl-C during a long command should not mean waiting out the timeout."""
    from omega_agent.cancellation import CancelSignal

    signal = CancelSignal()

    async def cancel_shortly() -> None:
        await asyncio.sleep(0.2)
        signal.cancel()

    asyncio.ensure_future(cancel_shortly())

    with pytest.raises(ToolError) as excinfo:
        await _tools(tmp_path, timeout=30.0)["run_shell"].execute(
            {"command": "sleep 30"}, signal
        )

    assert "Cancelled" in str(excinfo.value)


async def test_the_prepare_seam_can_rewrite_a_command(tmp_path: Path) -> None:
    """The hole a Tier 3+ sandbox slots into, with no other file changing."""
    tools = _tools(tmp_path, prepare_shell=lambda command: f"echo wrapped: $({command})")

    result = await tools["run_shell"].execute({"command": "echo inner"}, None)
    assert "wrapped: inner" in result.text


# ---------------------------------------------------------------- descriptions


async def test_tool_descriptions_state_the_budget_they_enforce(tmp_path: Path) -> None:
    """The model is told the constraint it will be subject to."""
    tools = _tools(tmp_path)
    for name in ("read_file", "run_shell"):
        assert "truncated" in tools[name].description.lower()


async def test_file_tool_descriptions_state_the_confinement(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    for name in ("read_file", "write_file", "edit_file"):
        assert "working directory" in tools[name].description


async def test_descriptions_have_no_run_together_sentences(tmp_path: Path) -> None:
    """The model reads these. A missing space after a full stop is a real defect."""
    for tool in build_tools(tmp_path):
        for glued in (".W", ".P", ".C", ".O"):
            assert glued not in tool.description, f"{tool.name}: {glued}"


async def test_schemas_are_well_formed(tmp_path: Path) -> None:
    for tool in build_tools(tmp_path):
        assert tool.parameters["type"] == "object"
        assert "properties" in tool.parameters
        for name in tool.parameters.get("required", []):
            assert name in tool.parameters["properties"]


async def test_there_are_eight_tools_now(tmp_path: Path) -> None:
    """Four became eight across Tier 3: three search tools, then read_image.

    The count is asserted rather than the set alone, because a tool appearing
    without anyone noticing is how `_PATH_ARGUMENTS` in `approval.py` falls out
    of date — and a tool missing from that table loses its outside-root check in
    silence.
    """
    assert sorted(t.name for t in build_tools(tmp_path)) == [
        "edit_file",
        "find_files",
        "list_files",
        "read_file",
        "read_image",
        "run_shell",
        "search_files",
        "write_file",
    ]


# ------------------------------------------------------------- tool descriptions


def test_the_descriptions_do_not_route_between_tools(tmp_path: Path) -> None:
    """Tier 2 wrote steering into the descriptions. Both references do not.

    "Whenever you are asked to read a file, use this tool" is read *after* the
    model has already picked a tool, so as routing it is close to useless — and
    it crowds out the part a description is for, which is how to drive the
    parameters. Pi and Tau both keep descriptions factual and put the steering
    in the system prompt.
    """
    for tool in build_tools(tmp_path):
        assert "whenever you are asked" not in tool.description.lower(), (
            f"{tool.name} is still routing from its description"
        )


def test_every_tool_says_something_about_paths_or_scope(tmp_path: Path) -> None:
    """A description that omits the constraint invites the call that hits it."""
    by_name = {tool.name: tool for tool in build_tools(tmp_path)}

    for name in ("read_file", "write_file", "edit_file"):
        assert "working directory" in by_name[name].description, name
    assert "not restricted" in by_name["run_shell"].description, (
        "the shell's lack of confinement is the one thing it must admit"
    )
