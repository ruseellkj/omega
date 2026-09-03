"""A smoke eval: does the whole thing still work end to end?

Distinct from the test suite, and the distinction is the point. The tests check
*units* against fakes: does the loop stop on content, does confinement refuse a
symlink. This checks the *agent* against a task: told to create a file, does a
file appear.

It is deliberately tiny. A smoke eval that takes ten minutes is a smoke eval
nobody runs, and its job is only to catch the class of breakage where every unit
still passes and yet the assembled thing does nothing — a tool registered under
the wrong name, an approval gate refusing everything, a system prompt that
stopped mentioning the tools.

Run it:

    uv run python -m omega_coding.evals            # scripted, offline, free
    uv run python -m omega_coding.evals --real     # against the configured provider

`anatomy.md:400` notes this shares its driver with terminal-bench. The driver is
`headless.py`; this is one task pointed at it.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from omega_agent.provider import ModelProvider
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_coding.builtin_tools import build_tools
from omega_coding.headless import HeadlessResult, run_headless

TASK = "Create a file called hello.txt containing exactly: hi"

_EXPECTED_FILE = "hello.txt"
_EXPECTED_CONTENT = "hi"


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        return f"  [{mark}] {self.name}" + (f" - {self.detail}" if self.detail else "")


def _scripted_provider() -> FakeProvider:
    """A model that does the task correctly. Proves the machinery, not the model."""
    return FakeProvider(
        [
            tool_turn(
                "write_file",
                {"path": _EXPECTED_FILE, "content": _EXPECTED_CONTENT},
                text="Creating it now.",
            ),
            text_turn(f"Created {_EXPECTED_FILE}."),
        ]
    )


def _grade(result: HeadlessResult, root: Path) -> list[Check]:
    """Four checks, in the order they would fail."""
    target = root / _EXPECTED_FILE
    written = target.read_text(encoding="utf-8").strip() if target.is_file() else None

    return [
        Check("the run finished cleanly", result.ok, result.error_message or ""),
        Check(
            "a tool was called",
            bool(result.tool_names),
            f"tools used: {result.tool_names or 'none'}",
        ),
        Check("the file exists", target.is_file(), str(target)),
        Check(
            "the file has the right contents",
            written == _EXPECTED_CONTENT,
            f"found {written!r}",
        ),
    ]


async def run_smoke(provider: ModelProvider, *, model: str) -> list[Check]:
    """Run the task in a throwaway directory and grade the result.

    A temp directory, not the current one: an eval that writes into the repo it
    is testing is an eval that eventually breaks the repo.
    """
    with tempfile.TemporaryDirectory(prefix="omega-eval-") as raw:
        root = Path(raw)
        result = await run_headless(
            provider=provider,
            model=model,
            system=(
                "You are omega, a terminal coding agent. Use the tools to do exactly "
                "what is asked, then stop."
            ),
            prompt=TASK,
            tools=build_tools(root),
            root=root,
            approve=True,
            max_turns=6,
        )
        return _grade(result, root)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    real = "--real" in args

    if real:
        from omega_ai.anthropic import DEFAULT_MODEL, AnthropicProvider

        provider: ModelProvider = AnthropicProvider()
        model = DEFAULT_MODEL
        print(f"smoke eval against {model}")
    else:
        provider = _scripted_provider()
        model = "fake-model"
        print("smoke eval (scripted - no network, no key, no cost)")

    checks = asyncio.run(run_smoke(provider, model=model))
    for check in checks:
        print(check)

    failed = [check for check in checks if not check.passed]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
