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
from omega_agent.tools import Tool
from omega_ai.anthropic import DEFAULT_MODEL as ANTHROPIC_MODEL
from omega_ai.anthropic import AnthropicProvider
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_ai.openai import DEFAULT_MODEL as OPENAI_MODEL
from omega_ai.openai import OpenAIProvider
from omega_coding.approval import Answer, ApprovalPolicy, ApprovalRequest
from omega_coding.builtin_tools import build_tools, collect_guidelines
from omega_coding.context import measure
from omega_coding.cost import CostTracker, price_from_env
from omega_coding.env import USER_CONFIG, find_env_files, load_environment
from omega_coding.history import drop_empty_failed_turns
from omega_coding.redact import redacting_hook

SYSTEM_PROMPT = """You are omega, a terminal coding agent. Use the tools to inspect and edit files.

When asked to perform a coding task:
1. Inspect the codebase before changing it.
2. Make the change with write_file, or run commands with run_shell.
3. Verify your work before reporting that you are done."""

_ARG_PREVIEW = 80
_RESULT_PREVIEW = 100

#: Project conventions, read once at startup. "Use uv, not pip" belongs in a file
#: rather than in every prompt.
PROJECT_INSTRUCTIONS_FILE = "OMEGA.md"


def _system_prompt(root: Path, tools: list[Tool]) -> str:
    """The standing instructions: base prompt, tool guidelines, project conventions.

    The guidelines come from the tools themselves rather than being written out
    here, which is how "use read_file instead of cat" ends up in the system
    prompt while living next to `read_file`. Tau does the same thing with
    `prompt_guidelines`, and the reason is drift: advice kept away from the tool
    it describes goes stale the first time the tool changes.

    Built **once**, at startup, and never regenerated per turn. That is not
    laziness — prompt caching at Tier 3 requires the start of every request to be
    byte-identical between calls, and a system prompt rebuilt each turn (with a
    timestamp in it, say) silently destroys the cache. Cheaper to get the habit
    right now than to debug a mysteriously expensive agent later.
    """
    path = root / PROJECT_INSTRUCTIONS_FILE
    try:
        extra = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    except OSError:
        extra = ""

    sections = [SYSTEM_PROMPT]
    guidelines = collect_guidelines(tools)
    if guidelines:
        sections.append(f"# Tool guidelines\n\n{guidelines}")
    if extra:
        sections.append(
            f"# Project instructions (from {PROJECT_INSTRUCTIONS_FILE})\n\n{extra}"
        )
    return "\n\n".join(sections)


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

    try:
        async for event in harness.run(prompt):
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
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue the most recent session in this directory.",
    )
    parser.add_argument(
        "--session",
        metavar="ID",
        help="Continue a specific session by id.",
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

    tools = build_tools(root, confine=args.confine)
    system = _system_prompt(root, tools)
    if PROJECT_INSTRUCTIONS_FILE in system:
        print(f"Loaded project instructions from {PROJECT_INSTRUCTIONS_FILE}.")

    tracker = CostTracker(price_from_env())

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
        # Keep failed turns in the transcript, out of the request. The simpler
        # sibling of the seam compaction uses at Tier 3.
        convert_to_llm=drop_empty_failed_turns,
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

    if args.session or args.resume:
        if store is None:
            sys.exit("Cannot resume with --no-save.")
        session_id = args.session or store.latest_session_id()
        if session_id is None:
            print("No previous session found in this directory - starting a new one.")
        else:
            restored = harness.resume(session_id)
            print(f"Resumed {session_id} ({restored} messages).")

    while True:
        try:
            prompt = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if prompt.strip().lower() in {"exit", "quit"}:
            if harness.session_id is not None:
                print(f"Session saved: {harness.session_id} (resume with `omega --resume`)")
            break
        if not prompt.strip():
            continue

        try:
            asyncio.run(_run_turn(harness, prompt))
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
            model=model, system=system, messages=harness.messages, tools=tools
        )
        print(f"\n  [{usage} | {tracker}]")
        print()


if __name__ == "__main__":
    main()
