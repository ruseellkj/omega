"""Assembling the system prompt.

Two of these moved here from `test_tools.py` when `collect_guidelines` moved out
of `builtin_tools.py` — the tests follow the module, not the topic.

What is worth defending here is the *placement* of steering text. A tool's
description is read after the model has already chosen that tool, so routing
advice in a description does nothing. Both references put it in the system prompt
instead, and that is what these tests pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omega_agent.tools import Tool, ToolResult
from omega_coding.builtin_tools import build_tools
from omega_coding.system_prompt import (
    BASE_PROMPT,
    PROJECT_INSTRUCTIONS_FILE,
    build_system_prompt,
    collect_guidelines,
    format_guidelines,
    read_project_instructions,
)


async def _noop(arguments: dict[str, Any], signal: Any) -> ToolResult:
    return ToolResult(content="ok")  # type: ignore[arg-type]


def _tool(name: str, *guidelines: str) -> Tool:
    return Tool(
        name=name,
        description="d",
        parameters={"type": "object"},
        guidelines=guidelines,
        execute=_noop,
    )


# ------------------------------------------------------------------- collecting


def test_guidelines_reach_the_system_prompt_not_the_description(tmp_path: Path) -> None:
    """Tau's `prompt_guidelines`, ported. The `cat` line is the one that matters.

    It is what stops a model shelling out to read a file — which would route
    around the gate's oversight of the file tools entirely, since `run_shell` has
    neither an approval-free path nor a location check. And it can only work from
    the system prompt, because by the time a description is read the tool has
    already been chosen.
    """
    tools = build_tools(tmp_path)
    prompt = build_system_prompt(tmp_path, tools)

    assert "instead of cat" in prompt
    assert all(g not in t.description for t in tools for g in t.guidelines)


def test_collection_is_stable_and_deduplicated() -> None:
    """Byte-identical between runs, because prompt caching needs a stable prefix.

    A `set` iterated directly would reorder between processes and quietly cost
    money at Tier 3 — the same trap `build_system_prompt` avoids by being called
    once at startup.
    """
    tools = [_tool("a", "shared", "first"), _tool("b", "shared", "second")]

    assert collect_guidelines(tools) == ["shared", "first", "second"], "tool order, deduped"
    assert collect_guidelines(tools) == collect_guidelines(tools)
    assert collect_guidelines([]) == []


def test_blank_guidelines_are_dropped_and_whitespace_trimmed() -> None:
    """A tool with an empty guideline must not produce a bare bullet."""
    assert collect_guidelines([_tool("a", "  spaced  ", "", "   ")]) == ["spaced"]


def test_extra_guidelines_come_last_and_dedupe_against_the_tools() -> None:
    """The seam for advice belonging to no single tool — Tau's extension path."""
    tools = [_tool("a", "from the tool")]

    assert collect_guidelines(tools, ["project policy"]) == ["from the tool", "project policy"]
    assert collect_guidelines(tools, ["from the tool"]) == ["from the tool"], "no duplicate"


def test_format_guidelines_is_markdown_bullets() -> None:
    assert format_guidelines([_tool("a", "one", "two")]) == "- one\n- two"
    assert format_guidelines([]) == ""


# -------------------------------------------------------------------- assembling


def test_a_toolless_prompt_is_exactly_the_base_prompt(tmp_path: Path) -> None:
    """No guidelines and no OMEGA.md means no headings.

    An empty "# Tool guidelines" section would be a heading promising content
    that is not there, which is worse than its absence.
    """
    assert build_system_prompt(tmp_path, []) == BASE_PROMPT


def test_project_instructions_are_appended_when_present(tmp_path: Path) -> None:
    (tmp_path / PROJECT_INSTRUCTIONS_FILE).write_text("Use uv, never pip.\n", encoding="utf-8")

    prompt = build_system_prompt(tmp_path, [])

    assert prompt.startswith(BASE_PROMPT)
    assert "Use uv, never pip." in prompt
    assert PROJECT_INSTRUCTIONS_FILE in prompt, "say where the conventions came from"


def test_the_order_is_base_then_guidelines_then_project(tmp_path: Path) -> None:
    """Order is fixed, so the prompt prefix stays stable for caching."""
    (tmp_path / PROJECT_INSTRUCTIONS_FILE).write_text("project rule", encoding="utf-8")

    prompt = build_system_prompt(tmp_path, [_tool("a", "tool rule")])

    assert prompt.index(BASE_PROMPT) < prompt.index("tool rule") < prompt.index("project rule")


def test_a_missing_or_unreadable_omega_md_is_not_an_error(tmp_path: Path) -> None:
    """Conventions are an improvement, not a requirement.

    A directory where the file should be is the cheap way to make the read fail
    with an OSError rather than a missing-file check.
    """
    assert read_project_instructions(tmp_path) == ""

    (tmp_path / PROJECT_INSTRUCTIONS_FILE).mkdir()
    assert read_project_instructions(tmp_path) == ""
