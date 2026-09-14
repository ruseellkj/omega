"""Assembling the system prompt — the standing instructions, built once.

Both references give this its own module: Pi's
`packages/coding-agent/src/core/system-prompt.ts` and Tau's
`tau_coding/system_prompt.py`. omega had the same logic split across two files —
the base text and the assembly in `cli.py`, the guideline collection in
`builtin_tools.py` — which is one of those arrangements that is fine until you go
looking for it. This is the same code, in the place both references keep it.

**Why any of this is in the system prompt rather than in tool descriptions.**
A description is read *after* the model has already chosen a tool: it answers
"how do I call this", not "which one should I reach for". Steering between tools
— "use `read_file` rather than `cat`" — is advice about the whole toolset, so it
belongs where standing instructions live. omega originally put *"Whenever you are
asked to read a file, use this tool"* in `read_file`'s description, which is
close to useless as routing, and Pi and Tau both keep their descriptions purely
factual for exactly this reason.

That is not a cosmetic point. If the model habitually shells out to `cat`, the
file tools' approval and location checks never run, because `run_shell` has
neither. **Tool choice is a safety property here, not tidiness.**

**Guidelines travel with the tool that needs them.** `Tool.guidelines` carries
the text; this module only gathers it. The alternative — one hardcoded list here
— goes stale the first time a tool changes, because the advice would sit nowhere
near the thing it describes.

**Built once, at startup, never regenerated per turn.** Not laziness: prompt
caching at Tier 3 needs the start of every request to be byte-identical between
calls, and a prompt rebuilt each turn (with a timestamp in it, say) silently
destroys the cache. Cheaper to have the habit now than to debug a mysteriously
expensive agent later. Everything below is therefore deterministic — see the
ordering note in `collect_guidelines`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from omega_agent.tools import Tool

BASE_PROMPT = """You are omega, a terminal coding agent. Use the tools to inspect and edit files.

When asked to perform a coding task:
1. Inspect the codebase before changing it.
2. Make the change with write_file, or run commands with run_shell.
3. Verify your work before reporting that you are done."""

#: Project conventions, read once at startup. "Use uv, not pip" belongs in a file
#: rather than in every prompt.
PROJECT_INSTRUCTIONS_FILE = "OMEGA.md"


def collect_guidelines(tools: Sequence[Tool], extra: Sequence[str] = ()) -> list[str]:
    """Every tool's `guidelines`, plus `extra`, de-duplicated and in order.

    Tau's `collect_prompt_guidelines` with its conditional logic left out. Tau
    varies advice by which tools exist — "use bash for ls/grep/find" when it has
    no dedicated search tools, "prefer grep/find/ls over bash" when it does — and
    Pi does the same in `addGuideline`. omega has four tools and no such fork, so
    this is a loop and a set. When search tools arrive at Tier 3 the fork belongs
    here, which is the other reason this is a function of its own.

    **Order follows the tool list**, so the result is byte-identical between runs.
    A `set` iterated directly would reorder between processes and quietly break
    prompt caching — see the module docstring.

    `extra` is the seam for guidelines belonging to no single tool: project
    policy, or an extension's advice. Tau threads its extensions' guidelines
    through the same parameter.
    """
    seen: set[str] = set()
    collected: list[str] = []

    for text in [guideline for tool in tools for guideline in tool.guidelines] + list(extra):
        stripped = text.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            collected.append(stripped)

    return collected


def format_guidelines(tools: Sequence[Tool], extra: Sequence[str] = ()) -> str:
    """The guidelines as markdown bullets. Empty string when there are none."""
    return "\n".join(f"- {guideline}" for guideline in collect_guidelines(tools, extra))


def read_project_instructions(root: Path) -> str:
    """`OMEGA.md` from the working directory, or empty if unreadable.

    Unreadable is deliberately not an error. A missing or permission-denied
    `OMEGA.md` must not stop the agent starting — project conventions are an
    improvement, not a requirement.
    """
    path = root / PROJECT_INSTRUCTIONS_FILE
    try:
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    except OSError:
        return ""


def build_system_prompt(
    root: Path, tools: Sequence[Tool], extra_guidelines: Sequence[str] = ()
) -> str:
    """Base prompt, then tool guidelines, then project conventions.

    Sections are appended only when non-empty, so an agent with no guidelines and
    no `OMEGA.md` gets exactly `BASE_PROMPT` — which keeps the prompt honest
    instead of carrying empty headings.
    """
    sections = [BASE_PROMPT]

    guidelines = format_guidelines(tools, extra_guidelines)
    if guidelines:
        sections.append(f"# Tool guidelines\n\n{guidelines}")

    instructions = read_project_instructions(root)
    if instructions:
        sections.append(
            f"# Project instructions (from {PROJECT_INSTRUCTIONS_FILE})\n\n{instructions}"
        )

    return "\n\n".join(sections)
