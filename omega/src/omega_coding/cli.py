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
import getpass
import importlib.util
import io
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TextIO

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
from omega_coding import auth, models, oauth
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
from omega_coding.status import StatusLine, describe
from omega_coding.subagent import build_subagent_tool
from omega_coding.system_prompt import PROJECT_INSTRUCTIONS_FILE, build_system_prompt
from omega_coding.truncate import sweep_old_spills
from omega_coding.version import omega_version

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


#: How omega presents itself. Three surfaces over the same harness — the choice
#: changes nothing below `cli.py`.
Mode = Literal["tui", "repl", "print"]


def textual_is_available() -> bool:
    """Whether the terminal UI can be imported at all.

    `find_spec` rather than a `try: import`, because this runs on every startup
    and importing Textual costs ~160ms — the whole reason the real import is
    deferred. Looking for the module is microseconds.
    """
    return importlib.util.find_spec("textual") is not None


def choose_mode(
    *,
    print_prompt: str | None,
    repl: bool,
    stdin_tty: bool,
    stdout_tty: bool,
    textual_available: bool = True,
) -> Mode:
    """Which surface to run, decided from the flags and the terminal.

    **The tty check is not a nicety.** Textual takes over the screen, reads raw
    keys and writes escape sequences; pointed at a pipe it produces control
    codes where output was expected, and reads EOF where a keystroke was. So a
    non-interactive stream falls back to the REPL rather than failing oddly
    later. `omega < script.txt` and `omega | tee log` both keep working.

    `--tui` is deliberately not a parameter. It was the only way in before the
    UI became the default, and it now asks for what already happens — keeping it
    as a no-op alias means every command line anyone has written still runs.
    """
    if print_prompt is not None:
        return "print"
    if repl:
        return "repl"
    if not (stdin_tty and stdout_tty):
        return "repl"
    if not textual_available:
        # **Decided here, not at the import.** The first version caught the
        # `ModuleNotFoundError` down where `run_tui` is imported — which works,
        # but by then the banner has already printed "Ctrl+Q quits", and the
        # thing it is about to start has no Ctrl+Q. A mode that can still change
        # after the screen has been described to the user is not a mode.
        return "repl"
    return "tui"


async def _run_print(harness: Harness, prompt: str) -> int:
    """One prompt, answer on stdout, everything else on stderr.

    **The split is what makes this pipeable.** `omega -p "..." > answer.txt`
    should contain the answer and not a progress log, so tool activity goes to
    stderr where a redirect leaves it alone. This is the same division the status
    line already makes for the REPL (`status.py` writes to stderr, and only when
    it is looking at a terminal).

    Returns a process exit code rather than printing one, because a script
    running omega in a pipeline checks `$?` and not the wording of a message.
    """
    interrupt = _install_interrupt_handler(harness)
    reason = "error"
    wrote = False

    try:
        async for event in harness.run(prompt):
            if event.type == "message_update":
                raw = event.stream_event
                if raw.type == "text_delta":
                    sys.stdout.write(raw.delta)
                    sys.stdout.flush()
                    wrote = True
            elif event.type == "message_end":
                # A turn that called a tool speaks twice, and without this the
                # two answers are concatenated into one run-on line. Closing on
                # `message_end` rather than after each delta means a single
                # answer still arrives as one paragraph.
                if wrote:
                    sys.stdout.write("\n")
                    wrote = False
            elif event.type == "tool_execution_start":
                print(f"  {describe(event.tool_call)}", file=sys.stderr)
            elif event.type == "agent_end":
                reason = event.reason
                if event.error_message:
                    print(f"  {event.reason}: {event.error_message}", file=sys.stderr)
    finally:
        interrupt()

    if wrote:
        sys.stdout.write("\n")
    sys.stdout.flush()
    return 0 if reason == "stop" else 1


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


async def _ask_secret_in_terminal(prompt: str) -> str | None:
    """Read a secret from the terminal without echoing it.

    `getpass` rather than `input`, so the key never reaches the screen or the
    shell's scrollback. On a thread for the same reason `_ask_in_terminal` is:
    it blocks, and the event loop has a turn to run.

    EOF or an empty line is a cancel — the rule the approval prompt already
    follows, that the easiest answer is the one that changes nothing.
    """
    try:
        secret = await asyncio.to_thread(getpass.getpass, f"  {prompt}: ")
    except (EOFError, KeyboardInterrupt):
        return None
    return secret.strip() or None


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
            "turn is running to steer it."
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
        default=None,
        help=(
            "Force a provider. Normally unnecessary: omega uses whichever one you "
            "are signed in to. `openai` also reaches Groq, Together, Ollama and "
            "vLLM - see --base-url."
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
    parser.add_argument(
        "--repl",
        action="store_true",
        help=(
            "Use the plain print/input REPL instead of the terminal UI. The fallback "
            "when a terminal cannot run Textual, and what omega did by default before "
            "the UI became the default."
        ),
    )
    parser.add_argument(
        "-p",
        "--print",
        dest="print_prompt",
        metavar="PROMPT",
        nargs="?",
        const="-",
        help=(
            "Run one prompt to completion, print the answer to stdout and exit. "
            "Reads the prompt from stdin when given no argument, so it pipes. "
            "Tool activity goes to stderr, so redirecting stdout captures the answer "
            "and nothing else."
        ),
    )
    parser.add_argument(
        "--context-window",
        type=int,
        default=None,
        metavar="TOKENS",
        help=(
            "Override the model's context window for compaction. Defaults to the "
            "model table: 200,000, or 1,000,000 for a [1m] model."
        ),
    )
    parser.add_argument(
        "--compact-threshold",
        type=float,
        default=None,
        metavar="FRACTION",
        help=(
            "Fraction of the window to compact down to, between 0 and 1. Defaults to "
            "0.8. Compaction rewrites the request when the estimate crosses it."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"omega {omega_version()}",
        help="Print the version and exit.",
    )
    args = parser.parse_args()

    if args.compact_threshold is not None and not 0 < args.compact_threshold <= 1:
        parser.error("--compact-threshold must be greater than 0 and at most 1.")

    have_textual = textual_is_available()
    chooser = {
        "print_prompt": args.print_prompt,
        "repl": args.repl,
        "stdin_tty": sys.stdin.isatty(),
        "stdout_tty": sys.stdout.isatty(),
    }
    mode = choose_mode(**chooser, textual_available=have_textual)

    if not have_textual and choose_mode(**chooser, textual_available=True) == "tui":
        # A stale install produces exactly this: `textual` became a dependency at
        # Tier 3, and an `omega` installed before that keeps running the current
        # source against its old environment. Said before the banner, so the two
        # cannot contradict each other.
        print(
            "The terminal UI needs 'textual', which is not installed here.\n"
            "Falling back to the print REPL. To get the UI back:\n"
            "    uv tool install --reinstall --editable <path-to>/omega\n"
            "or run it from the project with `uv run omega`.\n"
            "Pass --repl to choose this deliberately and skip this notice.\n",
            file=sys.stderr,
        )

    #: Where the orientation text goes, and it differs per surface.
    #:
    #: **print** — stdout belongs to the answer and nothing else may touch it.
    #: `omega -p "..." > out.txt` that captures a banner is not pipeable, which
    #: was the whole point of the flag. Found by running it, not by reading it:
    #: the first `-p` redirect collected seven lines of banner above the answer.
    #:
    #: **tui** — discarded. The splash shows the model, the path, the approval
    #: mode and more, so printing the same facts first only means finding them
    #: again in the scrollback after quitting. Textual switches to the alternate
    #: screen immediately, so they were never visible during the session anyway.
    banner: TextIO
    if mode == "print":
        banner = sys.stderr
    elif mode == "tui":
        banner = io.StringIO()
    else:
        banner = sys.stdout

    # Choosing a provider is the *only* thing in this file that knows two of them
    # exist. Everything below - the harness, the hooks, the tools, the renderer -
    # is written against the interface and cannot tell which one it got.
    # Loaded before anything else, and searched outward from where you are
    # rather than from wherever omega happens to be installed. That difference
    # is the whole point: a globally installed omega has no idea where its own
    # source tree lives, and should not need to.
    env_files = load_environment()

    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL")

    def build_provider() -> tuple[ModelProvider, str, str | None]:
        """The provider, its default model, and which provider that was.

        **A closure rather than inline code**, because `/login` has to be able to
        run it again mid-session: the harness is holding a `LoginRequiredProvider`
        at that point and needs the real one without a restart. Keeping it here
        also keeps the two-file rule — this is still the only place besides
        `evals.py` that names a concrete provider.

        `auth.choose_provider` decides *which*, and it is re-asked on every call
        rather than captured once: after `/login openai` the answer changes, and
        a value read at startup would still say "signed in to nothing".
        """
        if args.fake:
            return _fake_provider(), "fake-model", None

        chosen = auth.choose_provider(args.provider, base_url=base_url)

        # **The key is passed, not merely checked.** Both adapters fall back to
        # `os.environ` when `api_key` is None (`anthropic.py:335`,
        # `openai.py:270`), so an earlier version that only *tested*
        # `auth.resolve(...)` and then constructed `AnthropicProvider()` opened
        # the gate for a key the provider never received — `/login` stored it,
        # the startup facts said "signed in", and the first request failed with
        # an authentication error. Measured: `_client.api_key` was None.
        if chosen == "openai":
            # A `--base-url` server (Ollama, vLLM) needs no key, so the gate does
            # not apply to it — but a key still wins if there is one.
            key = auth.resolve("openai")
            if base_url or key:
                return OpenAIProvider(api_key=key, base_url=base_url), OPENAI_MODEL, "openai"
            return auth.LoginRequiredProvider("openai"), OPENAI_MODEL, "openai"
        if chosen == "openai-codex":
            # No `api_key=` fallback here, deliberately: there is no environment
            # variable that authenticates a ChatGPT subscription, so a missing
            # credential is always "not signed in" rather than "try the ambient
            # one". The account id is stored beside the token at sign-in.
            # Imported here, not at module scope: this adapter is the only one
            # that reaches for `httpx` directly, and a startup-time import turns
            # a missing HTTP library into "omega will not open" instead of
            # "Codex is unavailable". Everything else still works without it.
            from omega_ai.openai_codex import CodexProvider

            account = auth.account_id("openai-codex")
            if auth.resolve("openai-codex") and account:
                return (
                    CodexProvider(
                        auth=lambda: oauth.access_token("openai-codex"),
                        account_id=account,
                    ),
                    models.DEFAULTS["openai-codex"],
                    "openai-codex",
                )
            codex_model = models.DEFAULTS["openai-codex"]
            return auth.LoginRequiredProvider("openai-codex"), codex_model, "openai-codex"
        if chosen == "anthropic":
            key = auth.resolve("anthropic")
            if key:
                # **The resolver, not just the key.** `api_key=` is still passed
                # so a construction-time failure is visible, but `auth=` is what
                # the adapter actually calls — once per request and once per
                # retry — and it is where an expiring subscription token gets
                # renewed. A static string cannot do that, and a session longer
                # than the token would die mid-task with no way to recover.
                return (
                    AnthropicProvider(
                        api_key=key,
                        auth=lambda: oauth.access_token("anthropic"),
                    ),
                    ANTHROPIC_MODEL,
                    "anthropic",
                )
            return auth.LoginRequiredProvider("anthropic"), ANTHROPIC_MODEL, "anthropic"

        # Signed in to nothing. The model name is still needed for the facts
        # block, so the Anthropic default stands in as a label — it is never
        # sent anywhere, because the provider cannot send.
        return auth.LoginRequiredProvider(None), ANTHROPIC_MODEL, None

    provider, default_model, chosen_provider = build_provider()
    model = args.model or default_model

    if args.fake:
        print("omega (fake provider - scripted responses, nothing is sent anywhere)", file=banner)
    elif chosen_provider is None:
        print("omega (not signed in - run /login)", file=banner)
    elif chosen_provider == "openai":
        print(f"omega ({model} via openai{f' at {base_url}' if base_url else ''})", file=banner)
    else:
        print(f"omega ({model})", file=banner)

    # **Print mode still refuses.** A script piping into omega must not receive a
    # friendly "run /login" on stdout and an exit code of 0 — there is nobody
    # there to run it, and a pipeline that silently produced an instruction
    # instead of an answer is worse than one that failed.
    if mode == "print" and isinstance(provider, auth.LoginRequiredProvider):
        sys.exit(
            _missing_key_message(
                auth.ENV_VARS[chosen_provider]
                if chosen_provider in auth.ENV_VARS
                else " or ".join(auth.ENV_VARS.values())
            )
        )

    # Each surface quits differently, and printing the REPL's answer under the
    # TUI is how someone ends up stuck in an app telling them to type `exit`.
    quit_hint = {
        "tui": "Ctrl+Q quits. Ctrl+C stops the current turn.",
        "repl": "Type 'exit' to quit.",
        "print": "",
    }[mode]
    if quit_hint:
        print(f"{quit_hint}\n", file=banner)

    root = Path.cwd()
    print(f"Working directory: {root}", file=banner)
    if args.confine:
        print("Paths outside it are refused outright (--confine).", file=banner)
    else:
        print("Paths outside it need your approval, reads included.", file=banner)
    if args.yes:
        # Said plainly, because --yes now means more than it used to. With no
        # fence on the file tools, this is unprompted read and write access to
        # the whole disk - the refuse-outright list is all that is left.
        print(
            "Tool calls are approved automatically (--yes): no prompts, anywhere on "
            "this machine. Only the refuse-outright list still applies.",
            file=banner,
        )

    for path in env_files:
        print(f"Loaded environment from {path}", file=banner)

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
    compactor_options: dict[str, Any] = {}
    if args.context_window is not None:
        compactor_options["window"] = args.context_window
    if args.compact_threshold is not None:
        compactor_options["threshold"] = args.compact_threshold
    compactor = Compactor(model=model, system=system, tools=tools, **compactor_options)

    # Policy arrives as hooks, so the loop knows nothing about approvals or
    # secrets. Swapping either is a change to this composition, nothing else.
    # Named rather than inlined, because the TUI needs a reference to hand it a
    # screen-based asker once a screen exists. See `ApprovalPolicy.use_asker`.
    policy = ApprovalPolicy(
        root,
        asker=None if args.yes else _ask_in_terminal,
        auto_approve=args.yes,
        confine=args.confine,
    )

    hooks = AgentHooks(
        before_tool_call=policy,
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

    # After `hooks`, deliberately: the child runs under the parent's gate and
    # redaction, so the tool cannot be built before they exist. And appended to
    # `tools` after the system prompt was built from it, so the prompt does not
    # advertise a tool the subagent's own child will not have.
    tools.append(
        build_subagent_tool(
            provider=provider,
            model=model,
            tools=tools,
            root=root,
            hooks=hooks,
            approve=args.yes,
        )
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

    if mode == "print":
        # `-p` with no argument means "the prompt is on stdin", which is what
        # makes `echo "..." | omega -p` work. Reading it here rather than in
        # `_run_print` keeps that decision next to the flag that caused it.
        prompt = sys.stdin.read().strip() if args.print_prompt == "-" else args.print_prompt
        if not prompt:
            print("-p was given an empty prompt; nothing to do.", file=sys.stderr)
            raise SystemExit(2)
        # Approvals in print mode have nobody to ask: `_ask_in_terminal` reads
        # EOF and declines, which is the right answer for an unattended run. Say
        # so up front rather than letting the model discover it one denial at a
        # time.
        if not args.yes:
            print(
                "note: running without --yes, so tools that change anything will be "
                "declined - there is no one to ask.",
                file=sys.stderr,
            )
        raise SystemExit(asyncio.run(_run_print(harness, prompt)))

    # Built once for both surfaces. The TUI needs it so `/` commands work there
    # too — they were REPL-only, and not for any reason that survived looking at.
    def reload_provider() -> bool:
        """Swap in a real provider after `/login`. True if it can now answer.

        The harness holds whatever `build_provider` returned at startup — a
        `LoginRequiredProvider` when there were no credentials. Rebuilding and
        assigning is what lets `/login` take effect in the session you are
        already in, rather than telling you to restart.
        """
        replacement, _, _ = build_provider()
        harness.provider = replacement
        return not isinstance(replacement, auth.LoginRequiredProvider)

    def switch_model(name: str) -> None:
        """Move every copy of the model at once.

        Two live copies exist and they are not interchangeable: `harness.model`
        is what reaches the provider, and `compactor.window` is what compaction
        budgets against. A switch that moves one is a switch that
        half-happened — and the window is the half whose failure lands on the
        *next* request, looking unrelated to the `/model` that caused it.

        There is deliberately **no third copy**. `CommandContext` is frozen, so
        anything reading `context.model` after a switch would be reading the
        startup value; `/model` reads `context.harness.model` instead, which is
        the one the provider actually sees.

        An explicit `--context-window` still wins, because someone who pinned a
        smaller window than the model's meant it.
        """
        harness.model = name
        if compactor is not None and args.context_window is None:
            compactor.window = models.window_for(name)

    command_context = CommandContext(
        harness=harness,
        store=store,
        tracker=tracker,
        model=model,
        system=system,
        tools=tools,
        hooks=hooks,
        compactor=compactor,
        # The REPL's asker. The TUI replaces this with its own on mount, the
        # same way the approval gate gets a screen-based one.
        ask_secret=_ask_secret_in_terminal,
        reload_provider=reload_provider,
        provider=chosen_provider or "",
        set_model=switch_model,
    )

    if mode == "tui":
        # Imported here, not at module scope, for two reasons. Textual costs
        # ~160ms of a ~920ms startup and `-p` in a script pays it for a UI it
        # will never draw (measured with `python -X importtime`). And an import
        # that can fail belongs where the failure can be answered — which is
        # here, not at the top of a module nobody chose to load.
        try:
            from omega_coding.tui import run_tui
        except ImportError as broken:
            # `find_spec` above already answered "is Textual installed". This
            # catches what it cannot: installed but unusable — a half-written
            # package, a missing transitive dependency, a version whose API moved.
            # Exiting rather than falling back, because the banner has by now
            # promised a UI, and a broken install is worth fixing rather than
            # working around.
            raise SystemExit(
                f"The terminal UI failed to load: {broken}\n"
                "Run with --repl to use omega while you sort it out."
            ) from broken
        else:
            # `--yes` used to be mandatory here: `_ask_in_terminal` blocks on
            # `input()` in a thread, which Textual paints over, so the first tool
            # call hung on a prompt nobody could see. `tui/approval.py` replaced
            # that with a screen, and the policy takes it on mount — so the gate
            # now works unattended and `--yes` is back to an ordinary opt-out.
            asyncio.run(
                run_tui(
                    harness,
                    policy=None if args.yes else policy,
                    context=command_context,
                    # For the startup facts. `cli.py` is one of the only two
                    # files allowed to know a provider's name, so it tells the
                    # screen rather than the screen asking.
                    provider_name=(
                        "fake" if args.fake else (chosen_provider or "none")
                    ),
                    auto_approve=args.yes,
                    confine=args.confine,
                )
            )
            return

    asyncio.run(
        _repl(
            harness=harness,
            context=command_context,
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
        # `harness.model`, not the local `model`: /model moves the harness and
        # the compactor, and this line is printed after every turn. The local
        # name still holds the startup value, so using it would report the
        # launch model's window for the rest of the session.
        usage = measure(
            model=harness.model,
            system=system,
            messages=harness.messages,
            tools=context.tools,
        )
        print(f"\n  [{usage} | {context.tracker}]")
        print()


if __name__ == "__main__":
    main()
