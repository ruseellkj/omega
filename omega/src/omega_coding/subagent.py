"""A subagent is the headless driver, called from a tool.

`headless.py` claimed this in Tier 2 and nothing tested it:

> "a subagent *is* this function called from inside a tool."

It holds. **Nothing in the loop, the harness, or the provider contract changed to
make this work** — a subagent turned out to be a `Tool` whose `execute` calls
`run_headless`. If it had needed a new mechanism, the layering argument would
have been wrong, and this file is where that would have shown.

## What it buys: context isolation

Failure #1 from the other side. "Where is authentication handled?" can mean
reading twenty files. Asked in the main conversation, all twenty land in the
context and stay there for the rest of the session — every later turn re-sends
them and re-pays for them. Asked through a subagent, the parent gains one
paragraph and the twenty files are discarded with the child.

Compaction makes a full context survivable. This keeps it from filling.

## The two things that had to be got right

**1 · Recursion is stopped by absence, not by a counter.** A child holding this
tool can spawn a child, forever, and the loop cannot tell — from its point of
view a nested agent is an ordinary tool call that happens to be slow. So the
child is handed the parent's tool list *with this tool removed*. A depth counter
would work too and would be one more thing to remember to increment; a tool that
is not there cannot be called.

**2 · The child runs under the parent's hooks.** Approval, redaction, history —
all of it. Building a fresh policy for the child would make a second place for a
deny list to be correct, which is the mistake `paths.py` spends a docstring
warning about and the one `!cmd` made for real. A refusal is a refusal at any
depth.

## What it deliberately does not do

* **No parallelism.** One subagent at a time, because tool calls are sequential
  and making these the exception would be a scheduling change dressed as a
  feature.
* **No session of its own.** The child's transcript is not persisted: it exists
  to be summarised and thrown away, and a session file per exploration would be
  noise in `/sessions`.
* **No nested cost line.** The child's tokens are real and are not separately
  reported yet. Worth knowing before trusting `/cost` while using this.
"""

from __future__ import annotations

from pathlib import Path

from omega_agent.hooks import AgentHooks
from omega_agent.provider import ModelProvider
from omega_agent.tools import Tool, ToolResult
from omega_agent.types import CancellationToken
from omega_coding.headless import run_headless

#: The one place the name is written. The recursion stop matches on it, so a
#: literal in two places would be a silent way to reopen that hole.
SUBAGENT_TOOL_NAME = "run_subagent"

#: Tighter than the parent's on purpose. A child burning the same allowance as
#: its parent leaves nothing for the parent to finish the task with, and a
#: subagent that needs thirty turns is a task that wanted decomposing further.
DEFAULT_SUBAGENT_MAX_TURNS = 12

SUBAGENT_SYSTEM_PROMPT = """You are a sub-agent working on one focused task for another agent.

Answer the task you were given and nothing else. You cannot ask questions — there
is nobody to answer them — so if something is ambiguous, state the assumption you
made and continue.

Your reply is the *only* thing that reaches the agent that called you. It will not
see the files you read or the commands you ran, so include the details it needs
and leave out the search that found them."""


def child_tools_for(tools: list[Tool]) -> list[Tool]:
    """The parent's tools minus this one. **The recursion stop.**

    A separate function rather than a line inside the factory, so the property
    can be asserted directly instead of inferred from behaviour — `Tool` is a
    slotted dataclass and cannot carry an attribute saying what it did.
    """
    return [tool for tool in tools if tool.name != SUBAGENT_TOOL_NAME]


def build_subagent_tool(
    *,
    provider: ModelProvider,
    model: str,
    tools: list[Tool],
    root: Path,
    hooks: AgentHooks,
    approve: bool,
    max_turns: int = DEFAULT_SUBAGENT_MAX_TURNS,
) -> Tool:
    """A tool that runs a nested agent on one task and returns its answer.

    `tools` is the **parent's** list. The child gets it with this tool filtered
    out, which is the recursion stop — see the module docstring.
    """
    child_tools = child_tools_for(tools)

    async def run_subagent(
        arguments: dict[str, object], signal: CancellationToken | None
    ) -> ToolResult:
        task = str(arguments.get("task", "")).strip()
        if not task:
            return ToolResult(content="No task was given.")  # type: ignore[arg-type]

        result = await run_headless(
            provider=provider,
            model=model,
            system=SUBAGENT_SYSTEM_PROMPT,
            prompt=task,
            tools=child_tools,
            # The parent's, not a fresh set. Approval and redaction apply at
            # every depth or they apply at none.
            hooks=hooks,
            max_turns=max_turns,
            # Passed through so Ctrl-C reaches a child mid-flight. Without it the
            # parent would end its turn while the child kept running tools.
            signal=signal,  # type: ignore[arg-type]
            approve=approve,
            root=root,
            # No store: the child's transcript is summarised and discarded.
        )

        if result.ok:
            # An agent that finished with nothing to say is a real outcome, and
            # an empty tool result reads to the model as a broken tool.
            answer = result.text or "The sub-agent finished without an answer."
            return ToolResult(content=answer)  # type: ignore[arg-type]

        # Not raised. A tool that raises into the parent loop is a tool that can
        # end the session, and the child failing is ordinary news.
        if result.reason == "max_turns":
            # `max_turns`, not `length`. Two different caps that both mean "it
            # stopped early": `length` is the *provider* running out of output
            # tokens, `max_turns` is the *loop* refusing to iterate again. This
            # branch never fired until mypy pointed out the comparison could not
            # be true — and the test passed anyway, because the fallback message
            # happens to contain the word "turn".
            report = (
                f"The sub-agent hit its turn limit of {max_turns} before finishing. "
                "Narrow the task and ask again."
            )
        else:
            detail = result.error_message or ""
            report = f"The sub-agent stopped early ({result.reason}). {detail}".strip()

        if result.text:
            report += f"\n\nWhat it had so far:\n{result.text}"

        # A plain result, not a raise and not an error flag. `ToolResult` has no
        # `is_error` - `TIER-2.md` records that gap - and raising would end the
        # parent's turn over a child that merely ran long. The parent is told
        # what happened and decides.
        return ToolResult(content=report)  # type: ignore[arg-type]

    return Tool(
        name=SUBAGENT_TOOL_NAME,
        description=(
            "Run a focused sub-task in a separate agent and get back only its answer. "
            "Use this for exploration whose details you do not need to keep - 'find where "
            "X is handled', 'summarise how Y works'. The sub-agent has the same tools you "
            "do but its own conversation, so what it reads does not enter yours. It cannot "
            "ask you questions, so give it everything it needs in one go."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "The complete task, written for someone with no other context."
                    ),
                }
            },
            "required": ["task"],
        },
        execute=run_subagent,
    )
