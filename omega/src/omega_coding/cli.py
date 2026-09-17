"""A print-based REPL.

Deliberately **not** a terminal UI. At Tier 2 a TUI would still hide more than it
shows; plain `print` keeps every event visible. A real UI arrives at Tier 3, and
it will subscribe to exactly the events this file already reads — that is the
test of whether the vocabulary was designed for a renderer or for a printer.

`--fake` runs the whole agent against scripted responses — no key, no network,
no credits. It exercises the same loop, the same tools, and the same streaming
path as the real thing.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path

from omega_agent.agent_events import AgentEvent
from omega_agent.harness import Harness
from omega_agent.hooks import AgentHooks
from omega_agent.loop import DEFAULT_MAX_TURNS
from omega_agent.provider import ModelProvider
from omega_agent.session import JsonlSessionStore
from omega_ai.anthropic import DEFAULT_MODEL as ANTHROPIC_MODEL
from omega_ai.anthropic import AnthropicProvider
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_ai.openai import DEFAULT_MODEL as OPENAI_MODEL
from omega_ai.openai import OpenAIProvider
from omega_coding.approval import Answer, ApprovalPolicy, ApprovalRequest
from omega_coding.builtin_tools import build_tools
from omega_coding.commands import CommandContext, dispatch
from omega_coding.compact import Compactor
from omega_coding.context import measure
from omega_coding.cost import CostTracker, price_from_env
from omega_coding.env import USER_CONFIG, find_env_files, load_environment
from omega_coding.eventlog import EventLog, sweep_old_logs
from omega_coding.history import drop_empty_failed_turns
from omega_coding.redact import redact_message, redacting_hook
from omega_coding.status import StatusLine
from omega_coding.system_prompt import PROJECT_INSTRUCTIONS_FILE, build_system_prompt
from omega_coding.truncate import sweep_old_spills
from omega_coding.tui import run_tui

_ARG_PREVIEW = 80
_RESULT_PREVIEW = 100


def _missing_key_message(variable: str, *, extra: str = "") -> str:
    """Say where we looked.

    A key reported as "not set" while it sits in a file three directories up is
    an afternoon nobody should have to spend. Listing the search path turns a
    dead end into an instruction.
    """
    searched = "\n".join(f"    {path}" for path in find_env_files(Path.cwd()))
    lines = [
        f"{variable} is not set. Looked for a .env in:",
        searched,
        "",
        "Put it in ./.env for this project, or in",
        f"    ~/{USER_CONFIG}",
        "to set it once for every project. An exported variable overrides both.",
    ]
    if extra:
        lines.append(extra)
    lines.append("Or run `omega --fake` to try omega without a key.")
    return "\n".join(lines)


def _fake_provider() -> FakeProvider:
    """A canned two-turn scenario, repeated for each prompt.

    Shows the whole path: a tool call, a real tool execution, and a final answer.
    """
    scenario = [
        tool_turn(
            "run_shell",
            {"command": "echo 'hello from omega'"},
            text="Let me check something first.",
        ),
        text_turn("That worked — the shell tool ran and returned its output."),
    ]
    return FakeProvider([scenario[i % 2] for i in range(40)])


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def _run_turn(harness: Harness, prompt: str) -> None:
    """One user prompt, run to completion, printed as it happens.

    Worth comparing against the Tier 1 version. That one had to watch the
    transcript list grow to notice tool results, because no event described them,
    and it tracked a `streaming_text` flag by hand to know when to emit a
    newline. Both of those were symptoms of reading a vocabulary built for the
    layer below. Every branch here is driven by an event that means what it says.
    """
    interrupt = _install_interrupt_handler(harness)
    status = StatusLine()

    try:
        async with status:
            async for event in harness.run(prompt):
                # Status first: it erases its own line before the renderer writes,
                # so the two never share a row of the terminal.
                status.observe(event)
                _render(event)
    finally:
        interrupt()


def _install_interrupt_handler(harness: Harness) -> Callable[[], None]:
    """Point SIGINT at the harness for the duration of a turn.

    Tier 1 let Ctrl-C kill the process. That was not just abrupt — it could
    leave a tool call unanswered, which makes the transcript **permanently**
    invalid. Now the interrupt cancels the turn, the turn ends properly, and the
    next prompt heals whatever was half-finished.

    Returns the function that removes the handler again, so the default
    behaviour is back in place while sitting at the prompt: a Ctrl-C there
    should quit, not be swallowed.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, harness.cancel)
    except NotImplementedError:
        # add_signal_handler is Unix-only. Elsewhere the KeyboardInterrupt path
        # in main() is the fallback, which is abrupt but at least says so.
        return lambda: None

    def restore() -> None:
        loop.remove_signal_handler(signal.SIGINT)

    return restore


async def _ask_in_terminal(request: ApprovalRequest) -> Answer:
    """Ask the user, off the event loop.

    `input` blocks, so it goes to a thread. Calling it directly would stall the
    whole agent — including the streaming that is mid-flight behind it.

    The default on a bare Enter is **no**. A prompt whose easiest answer is "yes"
    is not really asking.
    """
    print(f"\n  omega wants to use {request.tool_name}:")
    print(f"    {_clip(request.summary, 200)}")
    # What "always" grants differs inside and outside the root, and a keystroke
    # that means two things should say which one it means before you press it.
    print(f"    ({request.scope_note})")

    while True:
        try:
            answer = await asyncio.to_thread(input, "  allow? [y]es / [a]lways / [N]o: ")
        except EOFError:
            # Nothing is watching after all. Treat that as a refusal, not consent.
            print("  no input available - declining.", file=sys.stderr)
            return "deny"

        choice = answer.strip().lower()
        if choice in {"y", "yes"}:
            return "once"
        if choice in {"a", "always"}:
            return "always"
        if choice in {"n", "no", ""}:
            return "deny"
        print("  please answer y, a, or n.")


def _render(event: AgentEvent) -> None:
    """Print one agent event.

    A separate function from the loop that drives it, because this is exactly
    what a Tier 3 TUI replaces: same events in, widgets out instead of lines.
    Keeping it standalone means that swap touches one function.
    """
    if event.type == "message_update":
        # The twelve still travel; this is where they arrive.
        raw = event.stream_event
        if raw.type == "text_delta":
            print(raw.delta, end="", flush=True)
        elif raw.type == "text_end":
            print()

    elif event.type == "tool_execution_start":
        call = event.tool_call
        print(f"  → {call.name}({_clip(str(call.arguments), _ARG_PREVIEW)})")

    elif event.type == "tool_execution_end":
        result = event.result
        marker = "x" if result.is_error else "<"
        first_line = result.text.splitlines()[0] if result.text else ""
        print(f"  {marker} {_clip(first_line, _RESULT_PREVIEW)}")

    elif event.type == "agent_end" and event.reason == "aborted":
        # Not a failure - the user asked for it. Said plainly, because a
        # stack-trace-shaped message for "I pressed Ctrl-C" is noise.
        print("\n[cancelled]", file=sys.stderr)

    elif event.type == "agent_end" and event.reason != "stop":
        # `stop` is the only success. The rest mean "unfinished", and a user
        # should be told which one it was.
        print(f"\n[{event.reason}] {event.error_message or ''}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(prog="omega", description="A terminal coding agent (Tier 2).")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Use scripted responses instead of a real provider. No API key needed.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Approve tool calls automatically. Does not disable the refuse-outright "
            "list - that is not a prompt you can skip."
        ),
    )
    parser.add_argument(
        "--confine",
        action="store_true",
        help=(
            "Refuse any file path outside the working directory outright, instead of "
            "asking. Restores the Tier 2 hard fence."
        ),
    )
    # Named to match Claude Code exactly, because muscle memory is a real cost:
    # `--continue` picks up where you were, `--resume <id>` goes to a named one.
    # Tier 2 had these the other way round, which meant `--resume` did what every
    # other agent calls `--continue`.
    parser.add_argument(
        "-c",
        "--continue",
        dest="continue_latest",
        action="store_true",
        help="Continue the most recent session for this project.",
    )
    parser.add_argument(
        "--resume",
        metavar="ID",
        help="Resume a specific session by id. Use --sessions to list them.",
    )
    parser.add_argument(
        "--sessions",
        action="store_true",
        help="List saved sessions for this project and exit.",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help=(
            "Run the terminal UI instead of the print REPL. Lets you type while a "
            "turn is running to steer it. Requires --yes: approval prompts have no "
            "modal yet."
        ),
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Do not write a structured event log for this session.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write this session to disk.",
    )
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai"],
        default="anthropic",
        help=(
            "Which wire format to speak. `openai` also reaches Groq, Together, "
            "Ollama and vLLM - see --base-url."
        ),
    )
    parser.add_argument(
        "--base-url",
        metavar="URL",
        help=(
            "Override the endpoint for --provider openai, e.g. "
            "http://localhost:11434/v1 for Ollama."
        ),
    )
    parser.add_argument(
        "--model", default=None, help="Model id. Defaults to the provider's own default."
    )
    parser.add_argument(
        "--max-turns", type=int, default=DEFAULT_MAX_TURNS, help="Loop iteration cap."
    )
    args = parser.parse_args()

    # Choosing a provider is the *only* thing in this file that knows two of them
    # exist. Everything below - the harness, the hooks, the tools, the renderer -
    # is written against the interface and cannot tell which one it got.
    # Loaded before anything else, and searched outward from where you are
    # rather than from wherever omega happens to be installed. That difference
    # is the whole point: a globally installed omega has no idea where its own
    # source tree lives, and should not need to.
    env_files = load_environment()

    provider: ModelProvider
    model = args.model
    if args.fake:
        provider = _fake_provider()
        model = model or "fake-model"
        print("omega (fake provider - scripted responses, nothing is sent anywhere)")
    elif args.provider == "openai":
        base_url = args.base_url or os.environ.get("OPENAI_BASE_URL")
        if not os.environ.get("OPENAI_API_KEY") and not base_url:
            sys.exit(
                _missing_key_message(
                    "OPENAI_API_KEY",
                    extra=(
                        "Or pass --base-url to reach a local server (Ollama, vLLM) "
                        "that needs no key."
                    ),
                )
            )
        provider = OpenAIProvider(base_url=base_url)
        model = model or OPENAI_MODEL
        print(f"omega ({model} via openai{f' at {base_url}' if base_url else ''})")
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit(_missing_key_message("ANTHROPIC_API_KEY"))
        provider = AnthropicProvider()
        model = model or ANTHROPIC_MODEL
        print(f"omega ({model})")

    print("Type 'exit' to quit.\n")

    root = Path.cwd()
    print(f"Working directory: {root}")
    if args.confine:
        print("Paths outside it are refused outright (--confine).")
    else:
        print("Paths outside it need your approval, reads included.")
    if args.yes:
        # Said plainly, because --yes now means more than it used to. With no
        # fence on the file tools, this is unprompted read and write access to
        # the whole disk - the refuse-outright list is all that is left.
        print(
            "Tool calls are approved automatically (--yes): no prompts, anywhere on "
            "this machine. Only the refuse-outright list still applies."
        )

    for path in env_files:
        print(f"Loaded environment from {path}")

    # Old truncation spill files, from this run or any other. Swept at startup
    # rather than at exit, because the run that leaves them is the one that
    # crashed - an exit-time sweep would miss exactly the cases that matter.
    sweep_old_spills()

    tools = build_tools(root, confine=args.confine)
    system = build_system_prompt(root, tools)
    if PROJECT_INSTRUCTIONS_FILE in system:
        print(f"Loaded project instructions from {PROJECT_INSTRUCTIONS_FILE}.")

    tracker = CostTracker(price_from_env())

    # One instance, two callers: the automatic pass below and `/compact`. Two
    # compactors could disagree about the budget, which is the kind of drift that
    # only shows up as "why did it compact at a different point that time".
    compactor = Compactor(model=model, system=system, tools=tools)

    # Policy arrives as hooks, so the loop knows nothing about approvals or
    # secrets. Swapping either is a change to this composition, nothing else.
    hooks = AgentHooks(
        before_tool_call=ApprovalPolicy(
            root,
            asker=None if args.yes else _ask_in_terminal,
            auto_approve=args.yes,
            confine=args.confine,
        ),
        after_tool_call=redacting_hook,
        # The same masking, at the point it actually belongs. `after_tool_call`
        # only ever saw tool output; this sees every message, so a key in the
        # model's answer or in your own prompt is masked too.
        before_record=redact_message,
        # Keep failed turns in the transcript, out of the request. The simpler
        # sibling of the seam compaction fills below.
        convert_to_llm=drop_empty_failed_turns,
        # Tier 3, beginner failure #1. Constructed here rather than taking the
        # model and tools as hook arguments, because widening `ContextTransform`
        # would touch `hooks.py`, `loop.py` and `history.py` to spare one line.
        transform_context=compactor,
    )

    store = None if args.no_save else JsonlSessionStore(root)

    # One harness for the whole session: it owns the transcript, so successive
    # prompts are a conversation rather than a series of unrelated questions.
    harness = Harness(
        provider=provider,
        model=model,
        system=system,
        # Rooted at the directory omega was started in: that is the fence.
        tools=tools,
        hooks=hooks,
        max_turns=args.max_turns,
        store=store,
    )

    harness.add_listener(tracker.observe)

    # The second listener, and the evidence that `add_listener` was a seam rather
    # than a claim. Written by default: a debug log you have to remember to turn
    # on is one you do not have when it matters.
    if not args.no_log:
        logs = Path.home() / ".omega" / "logs"
        sweep_old_logs(logs)
        # Resolved lazily: `session_id` is None until the first turn creates it,
        # so binding the name here put every session in one shared file.
        harness.add_listener(
            EventLog(lambda: logs / f"{harness.session_id or 'unsaved'}.jsonl")
        )

    if args.sessions:
        if store is None:
            sys.exit("--sessions needs a session store; remove --no-save.")
        rows = store.list_sessions()
        if not rows:
            print(f"No saved sessions for {root}.")
        else:
            print(f"Sessions for {root} ({store.directory}):\n")
            for row in rows:
                when = row.modified.astimezone().strftime("%Y-%m-%d %H:%M")
                summary = row.first_prompt or "(no prompt recorded)"
                print(f"  {row.session_id}  {when}  {row.messages:>4} msgs  {summary}")
            print("\nResume one with: omega --resume <id>")
        return

    if args.resume or args.continue_latest:
        if store is None:
            sys.exit("Cannot resume with --no-save.")
        session_id = args.resume or store.latest_session_id()
        if session_id is None:
            print("No previous session found for this project - starting a new one.")
        else:
            restored = harness.resume(session_id)
            print(f"Resumed {session_id} ({restored} messages).")

    if args.tui:
        # Said rather than discovered. `_ask_in_terminal` blocks on `input()` in a
        # thread, which cannot work under Textual - without --yes the first tool
        # call would wait forever on a prompt nobody can see. An approval modal is
        # the first thing to add here.
        if not args.yes:
            print(
                "--tui needs --yes for now: approval prompts have no modal yet, and "
                "a prompt you cannot see is a hang.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        asyncio.run(run_tui(harness))
        return

    asyncio.run(
        _repl(
            harness=harness,
            context=CommandContext(
                harness=harness,
                store=store,
                tracker=tracker,
                model=model,
                system=system,
                tools=tools,
                hooks=hooks,
                compactor=compactor,
            ),
            model=model,
            system=system,
        )
    )


async def _close_provider(provider: ModelProvider) -> None:
    """Shut the provider's HTTP client down while the event loop still exists.

    Not part of `ModelProvider`: that protocol is one method on purpose, and
    `FakeProvider` has no client to close. This is a composition-root concern, so
    it is asked for by duck-typing rather than widened into the contract.

    Without it the client's connection pool is collected at interpreter
    shutdown - after the loop is gone - and prints a
    `generator didn't stop after athrow()` traceback under the session summary.
    Related to the per-turn traceback fixed earlier and *not* the same bug: that
    one was a new event loop per prompt, this one is a client never closed.
    """
    closer = getattr(provider, "aclose", None)
    if closer is not None:
        with contextlib.suppress(Exception):
            await closer()


async def _repl(
    *, harness: Harness, context: CommandContext, model: str, system: str
) -> None:
    """The conversation, on **one** event loop for the whole session.

    Tier 2 called `asyncio.run(_run_turn(...))` inside this loop, so every prompt
    built and tore down an event loop while the provider's HTTP client — created
    once at startup — outlived all of them. Sharing a connection pool across event
    loops is undefined behaviour in asyncio, and exactly the sort of thing that
    produces a traceback nobody can reproduce on demand.

    The cost of the fix is that `input` has to move off the loop, which is what
    `_ask_in_terminal` already does for approvals and for the same reason: a
    blocking `input` stalls everything else the loop is running.
    """
    try:
        await _converse(harness=harness, context=context, model=model, system=system)
    finally:
        # Inside the loop that created it, on every exit path - Ctrl-C, /exit,
        # EOF. Closing after `asyncio.run` returns would be too late.
        await _close_provider(harness.provider)


async def _converse(
    *, harness: Harness, context: CommandContext, model: str, system: str
) -> None:
    """The prompt loop itself, split out so `_repl` can own the shutdown."""
    while True:
        try:
            prompt = await asyncio.to_thread(input, "You: ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not prompt.strip():
            continue

        # Commands first. `None` means it was not one, so it goes to the model.
        outcome = await dispatch(prompt, context)
        if outcome == "exit":
            if harness.session_id is not None:
                print(f"Session saved: {harness.session_id} (continue with `omega -c`)")
            break
        if outcome == "handled":
            continue

        try:
            await _run_turn(harness, prompt)
        except KeyboardInterrupt:
            # Only reachable where add_signal_handler is unavailable. The
            # transcript may now hold an unanswered tool call - the next run()
            # repairs it before sending anything, so this is a report, not a
            # warning to act on.
            print("\n[interrupted]", file=sys.stderr)

        # The two instruments. Neither fixes anything - they make failures #1
        # and #9 visible before they bite, which is what Tier 3 needs in order
        # to know where to put a threshold.
        usage = measure(
            model=model, system=system, messages=harness.messages, tools=context.tools
        )
        print(f"\n  [{usage} | {context.tracker}]")
        print()


if __name__ == "__main__":
    main()
