"""Secret redaction — anatomy.md #32.

The agent reads files and runs commands. `cat .env` and `env` both produce
credentials, and Tier 1 put whatever came back straight into the transcript —
which goes to the provider, and from Step 5 onto disk.

This is not containment; a determined model can encode its way past any pattern
list. It is the difference between *casually* leaking a key into a session log
you later paste into a bug report, and not doing that.

Filling `after_tool_call` means the loop gains nothing from it, same as the gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omega_coding.redact import redact


@pytest.mark.parametrize(
    ("label", "secret"),
    [
        ("anthropic", "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
        ("openai", "sk-proj-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"),
        ("github", "ghp_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"),
        ("aws", "AKIAIOSFODNN7EXAMPLE"),
        ("slack", "xoxb-123456789012-ABCDEFGHIJKLMNOPQRSTUVWX"),
        ("google", "AIzaSyDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD"),
    ],
)
def test_known_key_shapes_are_masked(label: str, secret: str) -> None:
    cleaned, found = redact(f"the key is {secret} ok")

    assert secret not in cleaned
    assert "[redacted" in cleaned
    assert found, f"{label} was not reported"


def test_a_private_key_block_is_masked() -> None:
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQ\n-----END RSA PRIVATE KEY-----"
    cleaned, found = redact(text)

    assert "MIIEowIBAAKCAQ" not in cleaned
    assert found


def test_an_env_assignment_keeps_the_name_and_hides_the_value() -> None:
    """The name is the useful part: the model should know the variable is set."""
    cleaned, found = redact("ANTHROPIC_API_KEY=sk-ant-xyzxyzxyzxyzxyzxyz\nPATH=/usr/bin")

    assert "ANTHROPIC_API_KEY" in cleaned, "the model still needs to know it exists"
    assert "sk-ant-xyzxyzxyzxyzxyzxyz" not in cleaned
    assert "PATH=/usr/bin" in cleaned, "ordinary variables must survive"
    assert found


def test_a_bearer_header_is_masked() -> None:
    cleaned, _found = redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")

    assert "abcdefghijklmnopqrstuvwxyz123456" not in cleaned
    assert "Bearer" in cleaned


def test_ordinary_output_is_returned_untouched() -> None:
    """False positives cost real work: a mangled diff is a broken tool."""
    text = "def add(a, b):\n    return a + b\n\n5 passed in 0.42s\n"
    cleaned, found = redact(text)

    assert cleaned == text
    assert found == []


def test_it_says_what_it_hid_without_repeating_the_secret() -> None:
    cleaned, found = redact("token: ghp_EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE")

    report = " ".join(found)
    assert "GitHub" in report
    assert "ghp_E" not in report, "the report must not leak what it redacted"
    assert "ghp_E" not in cleaned


def test_several_secrets_in_one_blob_are_all_masked() -> None:
    text = (
        "AWS_SECRET=AKIAIOSFODNN7EXAMPLE\n"
        "GH=ghp_FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF\n"
        "harmless=yes\n"
    )
    cleaned, found = redact(text)

    assert "AKIAIOSFODNN7EXAMPLE" not in cleaned
    assert "ghp_F" not in cleaned
    assert "harmless=yes" in cleaned
    assert len(found) >= 2


# ------------------------------------------------------ it plugs into the loop


async def test_a_leaked_key_never_reaches_the_transcript() -> None:
    """End to end through the hook, which is the only thing that matters."""
    from typing import Any

    from omega_agent.harness import Harness
    from omega_agent.hooks import AgentHooks
    from omega_agent.tools import Tool, ToolResult
    from omega_agent.types import ToolResultMessage
    from omega_ai.fake import FakeProvider, text_turn, tool_turn
    from omega_coding.redact import redacting_hook

    async def leaky(arguments: dict[str, Any], signal: Any) -> ToolResult:
        return ToolResult(content="ANTHROPIC_API_KEY=sk-ant-api03-LEAKEDLEAKEDLEAKEDLEAKED")

    tool = Tool(name="env", description="d", parameters={"type": "object"}, execute=leaky)
    harness = Harness(
        provider=FakeProvider([tool_turn("env", {}), text_turn("ok")]),
        model="m",
        system="s",
        tools=[tool],
        hooks=AgentHooks(after_tool_call=redacting_hook),
    )

    async for _event in harness.run("show me the env"):
        pass

    result = next(m for m in harness.messages if isinstance(m, ToolResultMessage))
    assert "sk-ant-api03-LEAKED" not in result.text
    assert "ANTHROPIC_API_KEY" in result.text


def test_an_indented_secret_is_masked_and_keeps_its_indentation() -> None:
    """A key inside a YAML block. `^NAME=` alone would miss it entirely.

    The indentation has to survive: silently reformatting a file the model is
    reading is its own kind of damage.
    """
    cleaned, found = redact("config:\n    api_key: supersecretvalue\n")

    assert "supersecretvalue" not in cleaned
    assert "    api_key: [redacted]" in cleaned
    assert found


# --------------------------------------------------- the paths that went around it
#
# Both of these were open until Tier 2.5, and both were found by *running* the
# thing rather than reading it. The lesson is in the test above:
# `test_a_leaked_key_never_reaches_the_transcript` goes end to end through the
# real harness, the real loop and the real hook — using a fake tool that
# `return`s. A tool that cannot fail can only exercise the half of the code that
# succeeds, so that test read as full coverage while covering one of two exits.


async def test_a_tool_that_raises_is_masked_too() -> None:
    """The first way around: an exception leaves before the hook is reached.

    `tool_runner` returned from its `except` block, so everything after it —
    `after_tool_call` included — was skipped. Which matters because a failing
    command's output is exactly what ends up pasted into a bug report.
    """
    from typing import Any

    from omega_agent.hooks import AgentHooks
    from omega_agent.tool_runner import execute_tool_call
    from omega_agent.tools import Tool, ToolError, ToolResult
    from omega_agent.types import ToolCall
    from omega_coding.redact import redacting_hook

    async def explodes(arguments: dict[str, Any], signal: Any) -> ToolResult:
        raise ToolError("failed while reading ANTHROPIC_API_KEY=sk-ant-api03-LEAKEDLEAKEDLEAK")

    tool = Tool(name="boom", description="d", parameters={"type": "object"}, execute=explodes)
    message = await execute_tool_call(
        ToolCall(id="1", name="boom", arguments={}),
        {"boom": tool},
        AgentHooks(after_tool_call=redacting_hook),
        None,
    )

    assert message.is_error, "still an error - masking must not disguise that"
    assert "sk-ant-api03-LEAKED" not in message.text
    assert "ANTHROPIC_API_KEY" in message.text, "the name survives; only the value goes"


async def test_a_shell_command_that_exits_non_zero_is_masked(tmp_path: Path) -> None:
    """The same hole, through the tool that actually reaches it in practice.

    `run_shell` treats any non-zero exit as a failure and raises, carrying the
    command's whole output in the message. So the only difference between a
    masked result and a leaked one used to be the exit code.
    """
    from omega_agent.hooks import AgentHooks
    from omega_agent.tool_runner import execute_tool_call
    from omega_agent.types import ToolCall
    from omega_coding.builtin_tools import build_tools
    from omega_coding.redact import redacting_hook

    secret = "sk-ant-" + "C" * 24
    tools = {tool.name: tool for tool in build_tools(tmp_path)}
    hooks = AgentHooks(after_tool_call=redacting_hook)

    async def run(command: str) -> str:
        message = await execute_tool_call(
            ToolCall(id="1", name="run_shell", arguments={"command": command}),
            tools,
            hooks,
            None,
        )
        return message.text

    assert secret not in await run(f"echo {secret}"), "the success path was already fine"
    assert secret not in await run(f"echo {secret}; exit 1"), "the failure path was not"


async def test_the_spilled_output_file_is_masked_on_disk() -> None:
    """The second way around, and the one that outlives the session.

    `truncate_output` writes the full output to a temp file so the model can read
    past the budget — and it does that *inside* the tool, before the hook runs at
    all. So a perfectly masked result could still leave a plain copy of the key
    on disk with its path handed to the model, and nothing tidies that file away.
    """
    from omega_coding.truncate import MAX_LINES, truncate_output

    secret = "sk-ant-" + "D" * 24
    padded = "\n".join([secret] + [f"line {n}" for n in range(MAX_LINES + 50)])

    body, truncation = truncate_output(padded, label="test")

    assert truncation.truncated and truncation.full_output_path is not None
    assert secret not in body, "the model's copy"
    on_disk = Path(truncation.full_output_path).read_text(encoding="utf-8")
    assert secret not in on_disk, "the copy left behind on disk"
    assert "[redacted" in on_disk, "masked, not merely dropped by truncation"
