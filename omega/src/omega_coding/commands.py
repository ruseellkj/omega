"""Commands typed into the conversation: `/help`, `/sessions`, `!git status`.

Before this, omega recognised exactly two words — `exit` and `quit` — and sent
everything else to the model. So typing `clear` produced a paid explanation of
how to clear a *terminal*, and `--sessions` was reachable only by quitting the
conversation you wanted to look away from.

**The problem is a missing channel.** There was no way to tell the program
something rather than ask the model something. A leading `/` or `!` is that
channel, which is the answer both references reached.

## Where this differs from Pi and Tau, and why

**A registry, not an `if`-chain.** Tau keeps a `CommandRegistry` that refuses
duplicate names (`commands.py:152-204`); Pi hardcodes ~25 string comparisons in
one function and keeps a *separate* list purely to feed autocomplete
(`interactive-mode.ts:2725-2857`, `slash-commands.ts:13-17`) — two places to
update, and no error when they disagree. Tau's shape wins at six entries and
keeps winning at twenty.

**Handlers act directly.** Tau's return a `CommandResult` carrying ~22
declarative flags for a frontend to interpret, because Tau *has* two frontends
honouring different subsets — its print mode ignores most of them
(`cli.py:785-797`). omega has one frontend, so a flag object would be ceremony
with nothing on the other end.

**`/help`, `/clear` and `/sessions` exist here and in neither reference.** Both
have autocomplete and interactive pickers, so discovery is a keystroke and
listing is a modal. omega has no TUI, so each has to be a command you can type.
Tau's equivalent of `/clear` is `/new`; Pi's is also `/new`, though its handler
is still called `handleClearCommand`.

**An unknown `/foo` is an error.** Both references forward it to the model as an
ordinary prompt. With eight commands and a metered API on the other side, a typo
deserves a correction rather than an invoice.

**`!cmd` does not touch the conversation — and both references disagree.** In
Tau `!` adds output to context and `!!` hides it (`session.py:2529-2542`); Pi is
the same (`interactive-mode.ts:2861-2877`). The surprising half of that is a
shell command silently enlarging every later request, so the plain form here is
the free one. A context-adding variant can arrive later as `!!`, inverted from
both — recorded in `TIER-2.md` rather than hidden.

One thing copied exactly: a bare `!` with nothing after it goes to the model, as
in Pi (`interactive-mode.ts:2865`).

**There is no second path to the shell.** `!cmd` runs through the same
`run_shell` tool the model uses, so it meets the same approval gate, the same
refuse-outright list, the same timeout and the same output budget. A private
shell here would be a second place for a deny list to be correct, which is the
mistake `paths.py` spends a docstring warning about.
"""

from __future__ import annotations

import difflib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Literal

from omega_agent.harness import Harness
from omega_agent.hooks import AgentHooks
from omega_agent.session import SessionStore
from omega_agent.tool_runner import execute_tool_call
from omega_agent.tools import Tool
from omega_agent.types import ToolCall
from omega_coding.compact import Compactor
from omega_coding.context import measure
from omega_coding.cost import CostTracker

#: What the REPL should do next. `None` from `dispatch` is the third case, and the
#: important one: *this was not a command, send it to the model*.
Outcome = Literal["handled", "exit"]

#: The prefix meaning "run this in a shell, do not involve the model".
SHELL_PREFIX = "!"


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Everything a handler is allowed to touch.

    Passed rather than reached for, so the surface a command can affect is one
    readable list instead of whatever it happens to be able to import.
    """

    harness: Harness
    store: SessionStore | None
    tracker: CostTracker
    model: str
    system: str
    tools: list[Tool]
    #: Carried so `!cmd` runs the *same* path the model does. Without it the
    #: escape would call `tool.execute` directly and skip the approval gate
    #: entirely — see `run_shell_escape`, where that was a real bug.
    hooks: AgentHooks
    #: Filled by `cli.py`. None when compaction is not wired, which is every
    #: caller that builds a context without one - `/compact` says so rather than
    #: pretending to work.
    compactor: Compactor | None = None
    args: str = ""


CommandHandler = Callable[[CommandContext], Awaitable[Outcome]]


@dataclass(frozen=True, slots=True)
class Command:
    """One command. `usage` is separate from `summary` so `/help` can align them."""

    name: str
    usage: str
    summary: str
    run: CommandHandler


# ----------------------------------------------------------------- handlers


async def _help(context: CommandContext) -> Outcome:
    width = max(len(command.usage) for command in COMMANDS)
    print("\n  Commands - everything else goes to the model.\n")
    for command in COMMANDS:
        print(f"    {command.usage:<{width}}  {command.summary}")
    print(f"    {'!<command>':<{width}}  run a shell command; no model, no tokens")
    print()
    return "handled"


async def _sessions(context: CommandContext) -> Outcome:
    """Saved sessions for this project, newest first.

    Reads every transcript, which is the honest cost of having no index. Measured
    at 0.28 ms per session, so a hundred of them is under 30 ms. Tau keeps an
    index because its picker re-filters on every keystroke; this does not.
    """
    if context.store is None:
        print("\n  Sessions are not being saved (--no-save).\n")
        return "handled"

    rows = context.store.list_sessions()
    if not rows:
        print("\n  No saved sessions for this project yet.\n")
        return "handled"

    print()
    for row in rows:
        when = row.modified.astimezone().strftime("%Y-%m-%d %H:%M")
        current = "  (current)" if row.session_id == context.harness.session_id else ""
        summary = row.first_prompt or "(no prompt recorded)"
        print(f"    {row.session_id}  {when}  {row.messages:>4} msgs  {summary}{current}")
    print("\n  Switch with /resume <id>.\n")
    return "handled"


async def _resume(context: CommandContext) -> Outcome:
    """Swap the live conversation for a stored one, without restarting omega."""
    session_id = context.args.strip()
    if not session_id:
        print("\n  Usage: /resume <id>. Run /sessions to see what is saved.\n")
        return "handled"
    if context.store is None:
        print("\n  Sessions are not being saved (--no-save), so there is nothing to resume.\n")
        return "handled"
    if not context.store.load(session_id):
        print(f"\n  No session {session_id} for this project. Try /sessions.\n")
        return "handled"

    restored = context.harness.resume(session_id)
    print(f"\n  Resumed {session_id} ({restored} messages).\n")
    return "handled"


async def _clear(context: CommandContext) -> Outcome:
    """Start a fresh conversation. The old one stays on disk."""
    messages = len(context.harness.messages)
    previous = context.harness.start_new_session()

    if previous is None:
        print("\n  Cleared. Nothing had been saved yet.\n")
    else:
        # Naming the id is the point: it is what makes "kept, not deleted"
        # checkable rather than a promise.
        print(f"\n  Cleared. {previous} kept ({messages} messages) - reopen with /resume.\n")
    return "handled"


async def _cost(context: CommandContext) -> Outcome:
    tracker = context.tracker
    print(f"\n  {tracker}   across {tracker.turns} model response(s)")
    if tracker.dollars is None:
        print("  No price set - export OMEGA_PRICE_INPUT and OMEGA_PRICE_OUTPUT for a total.")
    print()
    return "handled"


async def _context(context: CommandContext) -> Outcome:
    usage = measure(
        model=context.model,
        system=context.system,
        messages=context.harness.messages,
        tools=context.tools,
    )
    print(f"\n  {usage}")
    print("  An estimate - characters/4, tool schemas included. Never exact, never wildly low.\n")
    return "handled"


async def _compact(context: CommandContext) -> Outcome:
    """Shrink the conversation now, instead of waiting for the ceiling.

    **Why this exists when compaction is already automatic.** The automatic pass
    fires at 80% — mid-task, whenever the next request happens to cross it. This
    is the "I am about to start something big, clear the decks first" button, and
    it takes a target: `/compact 40` aims at 40% of the window rather than 80%.

    Both references have the same pair (Pi `slash-commands.ts:38`, Tau
    `commands.py:230`), and both take an argument — theirs is free text steering a
    model-written summary. omega's compaction is mechanical, so there is nothing
    to instruct; a percentage is the parameter that means something here, and the
    divergence is deliberate.

    Unlike the automatic pass, this one edits the **transcript**, not just the
    request. Nothing is lost: the session file is append-only and still holds
    every original message.
    """
    compactor = context.compactor
    if compactor is None:
        print("\n  Compaction is not configured for this session.\n")
        return "handled"

    fraction = compactor.threshold
    target = context.args.strip().rstrip("%")
    if target:
        try:
            percent = float(target)
        except ValueError:
            print(f"\n  Usage: /compact [percent]. '{target}' is not a number.\n")
            return "handled"
        if not 1 <= percent <= 100:
            print("\n  Usage: /compact [percent], between 1 and 100.\n")
            return "handled"
        fraction = percent / 100

    messages = context.harness.messages
    before_messages, before_tokens = len(messages), compactor.estimate(messages)

    compacted = compactor.compact_to(messages, fraction)
    after_tokens = compactor.estimate(compacted)

    if after_tokens >= before_tokens:
        # Truthful rather than encouraging. A command that always claims success
        # teaches you to stop reading it.
        print(f"\n  Already at ~{before_tokens:,} tokens - nothing worth dropping.\n")
        return "handled"

    context.harness.replace_transcript(compacted)
    freed = before_tokens - after_tokens
    print(
        f"\n  Compacted to {int(fraction * 100)}%: "
        f"{before_messages} messages (~{before_tokens:,} tokens) "
        f"-> {len(compacted)} (~{after_tokens:,}), {freed:,} freed."
    )
    print("  The session file still has everything - /resume reopens the full history.\n")
    return "handled"


async def _exit(context: CommandContext) -> Outcome:
    return "exit"


COMMANDS: tuple[Command, ...] = (
    Command("help", "/help", "list these commands", _help),
    Command("sessions", "/sessions", "saved sessions for this project", _sessions),
    Command("resume", "/resume <id>", "switch to another session", _resume),
    Command("clear", "/clear", "start a fresh session; the old one is kept", _clear),
    Command("cost", "/cost", "tokens and spend so far", _cost),
    Command("context", "/context", "how full the context window is", _context),
    Command("compact", "/compact [pct]", "shrink the conversation now", _compact),
    Command("exit", "/exit", "leave omega", _exit),
)

_BY_NAME: dict[str, Command] = {command.name: command for command in COMMANDS}


# ----------------------------------------------------------------- dispatch


async def run_shell_escape(command: str, context: CommandContext) -> Outcome:
    """Run `command` the way the model would, and print what came back.

    **Through `execute_tool_call`, not `tool.execute`.** That distinction was a
    real bug: the approval gate is not inside the tool, it is a `before_tool_call`
    hook consulted by `tool_runner`. Calling the tool directly ran the command
    with no gate at all — `!rm -rf /` reached the shell and was stopped only by
    the operating system, which is not a safety property omega can claim.

    Going through the runner inherits the whole path instead: the gate, the
    refuse-outright list, redaction on the way back, and the guarantee that
    nothing raises. Reaching for `subprocess` here would have meant a second
    place for a deny list to be correct, which is the mistake `paths.py` spends a
    docstring warning about.
    """
    by_name = {tool.name: tool for tool in context.tools}
    if "run_shell" not in by_name:
        print("\n  No shell tool is available in this session.\n")
        return "handled"

    result = await execute_tool_call(
        ToolCall(id="escape", name="run_shell", arguments={"command": command}),
        by_name,
        context.hooks,
        context.harness.signal,
    )

    # A refusal, a deny-list block and a non-zero exit all arrive as ordinary
    # results with `is_error` set. Every one of them is something to read.
    print(f"\n{result.text}\n")
    return "handled"


async def dispatch(text: str, context: CommandContext) -> Outcome | None:
    """Handle `text` if it is a command. `None` means "send it to the model".

    Three outcomes rather than a boolean, because "handled" and "not a command"
    are genuinely different from "handled, and now quit".
    """
    stripped = text.strip()

    if stripped.startswith(SHELL_PREFIX):
        command = stripped[len(SHELL_PREFIX) :].strip()
        # A bare `!` is not a shell command. Pi treats it as ordinary text and so
        # does this: passing it on is a better guess than refusing it.
        if command:
            return await run_shell_escape(command, context)
        return None

    if not stripped.startswith("/"):
        # `exit` and `quit` predate the slash commands and stay. They are what
        # people type, and breaking that for consistency helps nobody.
        if stripped.lower() in {"exit", "quit"}:
            return "exit"
        return None

    name, _, args = stripped[1:].partition(" ")
    command_entry = _BY_NAME.get(name.lower())
    if command_entry is None:
        _unknown(name)
        return "handled"

    return await command_entry.run(replace(context, args=args))


def _unknown(name: str) -> None:
    """Say so, and guess what was meant.

    Both references forward an unknown `/foo` to the model. With eight commands and
    a metered API on the other side, a typo is worth a correction rather than a
    charge.
    """
    close = difflib.get_close_matches(name.lower(), _BY_NAME, n=1, cutoff=0.6)
    suggestion = f" Did you mean /{close[0]}?" if close else ""
    print(f"\n  Unknown command /{name}.{suggestion} Try /help.\n")
