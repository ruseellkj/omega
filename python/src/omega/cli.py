"""A print-based REPL.

Deliberately **not** a terminal UI. At Tier 1 a TUI would hide whether the loop
works; plain `print` makes every event visible. A real UI arrives once there is
an event vocabulary designed for it.

`--fake` runs the whole agent against scripted responses — no key, no network,
no credits. It exercises the same loop, the same tools, and the same streaming
path as the real thing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from omega.builtin_tools import DEFAULT_TOOLS
from omega.loop import DEFAULT_MAX_TURNS, run_agent_loop
from omega.provider import ModelProvider
from omega.providers.anthropic import DEFAULT_MODEL, AnthropicProvider
from omega.providers.fake import FakeProvider, text_turn, tool_turn
from omega.types import AgentMessage, ToolResultMessage, UserMessage

SYSTEM_PROMPT = """You are omega, a terminal coding agent. Use the tools to inspect and edit files.

When asked to perform a coding task:
1. Inspect the codebase before changing it.
2. Make the change with write_file, or run commands with run_shell.
3. Verify your work before reporting that you are done."""


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


async def _run_turn(
    provider: ModelProvider,
    model: str,
    messages: list[AgentMessage],
    max_turns: int,
) -> None:
    """One user prompt, run to completion, printed as it happens."""
    printed_messages = len(messages)
    streaming_text = False

    async for event in run_agent_loop(
        provider=provider,
        model=model,
        system=SYSTEM_PROMPT,
        messages=messages,
        tools=DEFAULT_TOOLS,
        max_turns=max_turns,
    ):
        if event.type == "text_delta":
            print(event.delta, end="", flush=True)
            streaming_text = True

        elif event.type == "text_end":
            if streaming_text:
                print()
                streaming_text = False

        elif event.type == "toolcall_end":
            call = event.tool_call
            preview = str(call.arguments)
            if len(preview) > 80:
                preview = preview[:77] + "..."
            print(f"  → {call.name}({preview})")

        elif event.type == "error":
            if streaming_text:
                print()
                streaming_text = False
            print(f"\n[error] {event.error.error_message}", file=sys.stderr)

        # Tool results are appended to `messages` by the loop. Tier 1 has no
        # event for them — the ten agent events in Tier 2 add one — so they are
        # reported here as they appear in the transcript.
        while printed_messages < len(messages):
            message = messages[printed_messages]
            printed_messages += 1
            if isinstance(message, ToolResultMessage):
                marker = "x" if message.is_error else "<"
                first_line = message.text.splitlines()[0] if message.text else ""
                if len(first_line) > 100:
                    first_line = first_line[:97] + "..."
                print(f"  {marker} {first_line}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="omega", description="A terminal coding agent (Tier 1).")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Use scripted responses instead of a real provider. No API key needed.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model id.")
    parser.add_argument(
        "--max-turns", type=int, default=DEFAULT_MAX_TURNS, help="Loop iteration cap."
    )
    args = parser.parse_args()

    provider: ModelProvider
    if args.fake:
        provider = _fake_provider()
        print("omega (fake provider - scripted responses, nothing is sent anywhere)")
    else:
        # .env lives at the repo root, one level above this package.
        load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=True)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit(
                "ANTHROPIC_API_KEY not set - add it to .env (see .env.sample), "
                "or run `omega --fake` to try omega without a key."
            )
        provider = AnthropicProvider()
        print(f"omega ({args.model})")

    print("Type 'exit' to quit.\n")

    messages: list[AgentMessage] = []
    while True:
        try:
            prompt = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if prompt.strip().lower() in {"exit", "quit"}:
            break
        if not prompt.strip():
            continue

        messages.append(UserMessage(content=prompt))
        try:
            asyncio.run(_run_turn(provider, args.model, messages, args.max_turns))
        except KeyboardInterrupt:
            # Tier 1 has no cancellation token wired up, so an interrupt ends the
            # turn abruptly and may leave a tool call unanswered. Tier 2 fixes
            # this properly; until then, say so rather than pretending.
            print(
                "\n[interrupted - the transcript may now contain an unanswered "
                "tool call; restart if the next turn fails]",
                file=sys.stderr,
            )
        print()


if __name__ == "__main__":
    main()
