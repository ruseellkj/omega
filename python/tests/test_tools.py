"""The three Tier-1 tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from omega.builtin_tools import READ_FILE, RUN_SHELL, WRITE_FILE
from omega.tools import ToolError


async def test_read_file_returns_contents(tmp_path: Path) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("contents here", encoding="utf-8")

    result = await READ_FILE.execute({"path": str(target)}, None)
    assert result.text == "contents here"


async def test_read_file_raises_a_message_the_model_can_act_on(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as excinfo:
        await READ_FILE.execute({"path": str(tmp_path / "nope.txt")}, None)

    assert "not found" in str(excinfo.value).lower()


async def test_write_file_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deep" / "out.txt"

    result = await WRITE_FILE.execute({"path": str(target), "content": "written"}, None)

    assert target.read_text(encoding="utf-8") == "written"
    assert "7 chars" in result.text


async def test_run_shell_returns_combined_output() -> None:
    result = await RUN_SHELL.execute({"command": "echo out; echo err 1>&2"}, None)

    assert "out" in result.text
    assert "err" in result.text, "stderr must be captured too"


async def test_failing_command_carries_its_output_into_the_error() -> None:
    """A bare 'exited with code 1' tells the model nothing and it retries blindly."""
    with pytest.raises(ToolError) as excinfo:
        await RUN_SHELL.execute({"command": "echo why-it-failed; exit 3"}, None)

    message = str(excinfo.value)
    assert "why-it-failed" in message, "the output is the useful part of the failure"
    assert "code 3" in message


async def test_tool_descriptions_state_the_budget_they_enforce() -> None:
    """The model is told the constraint it will be subject to."""
    for tool in (READ_FILE, RUN_SHELL):
        assert "truncated" in tool.description.lower()


async def test_schemas_are_well_formed() -> None:
    for tool in (READ_FILE, WRITE_FILE, RUN_SHELL):
        assert tool.parameters["type"] == "object"
        assert "properties" in tool.parameters
        for name in tool.parameters.get("required", []):
            assert name in tool.parameters["properties"]
