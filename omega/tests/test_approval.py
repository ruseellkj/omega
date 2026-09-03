"""The approval gate — all of beginner failure #4's destruction half since Tier 2.5.

At Tier 2 this shared the job with a fence: confinement bounded the file tools,
and the gate covered the shell, which no path check can confine. **The fence is
gone, and these tests are where its responsibility landed.**

The gate lives in `before_tool_call` — which means **the loop gains no lines from
it.** That is the test of whether Boundary B was drawn correctly in Step 1: a
whole policy subsystem plugs in through one callback.

Four behaviours worth defending:

* **Reads inside the root are not gated; reads outside it are.** A prompt per
  read trains the user to hit "yes" without looking, which is how a gate becomes
  theatre — so the exemption stayed, but narrowed to the working directory. That
  one clause is the entire replacement for the fence, and
  `test_a_read_outside_the_root_is_gated` is the test that says so.
* **Catastrophes are denied, never asked.** Prompting for a disk-wipe is a trap:
  the answer arrives reflexively. Checked above `--yes` and above always-allow.
* **No approval channel means deny.** A headless run must not silently become an
  unattended one.
* **"Always" is scoped** — to a tool inside the root, to a directory outside it.

The dangerous-looking strings in `CATASTROPHES` are test data. Every one is
asserted to be *refused*; nothing here runs a shell.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from omega_agent.types import ToolCall
from omega_coding.approval import Answer, ApprovalPolicy, ApprovalRequest

#: A stable root for the gate. Most of these tests are about the deny list
#: and the prompt, neither of which cares where the working directory is.
#: The tests that *do* care build their own root under `tmp_path`.
ROOT = Path.cwd()

CATASTROPHES = [
    'rm -rf /',
    'rm -rf /*',
    'sudo rm -rf /',
    'rm -fr ~',
    'mkfs.ext4 /dev/sda1',
    'dd if=/dev/zero of=/dev/sda',
    ':(){ :|:& };:',
    'chmod -R 777 /',
    'shutdown -h now',
]


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=arguments)


async def _yes(request: ApprovalRequest) -> str:
    return "once"


async def _no(request: ApprovalRequest) -> str:
    return "deny"


# --------------------------------------------------------------- what is gated


async def test_reading_is_never_gated() -> None:
    """Confinement already bounds reads; a prompt per read is training to ignore."""
    policy = ApprovalPolicy(ROOT, asker=_no)

    assert (await policy(_call("read_file", path="a.txt"))).allowed is True


@pytest.mark.parametrize("tool", ["write_file", "edit_file", "run_shell"])
async def test_anything_that_changes_the_world_is_gated(tool: str) -> None:
    policy = ApprovalPolicy(ROOT, asker=_no)

    decision = await policy(
        _call(tool, path="a.txt", content="x", command="ls", old_text="a", new_text="b")
    )
    assert decision.allowed is False
    assert decision.reason


async def test_an_unknown_tool_is_gated_by_default() -> None:
    """Fail closed. A tool added later must not be exempt by omission."""
    policy = ApprovalPolicy(ROOT, asker=_no)

    assert (await policy(_call("send_email", to="x"))).allowed is False


# -------------------------------------------------------------------- answers


async def test_yes_allows_this_one_call() -> None:
    policy = ApprovalPolicy(ROOT, asker=_yes)

    assert (await policy(_call("run_shell", command="ls"))).allowed is True


async def test_always_stops_asking_for_that_tool() -> None:
    asked: list[str] = []

    async def always(request: ApprovalRequest) -> str:
        asked.append(request.tool_name)
        return "always"

    policy = ApprovalPolicy(ROOT, asker=always)

    assert (await policy(_call("run_shell", command="ls"))).allowed is True
    assert (await policy(_call("run_shell", command="pwd"))).allowed is True
    assert asked == ["run_shell"], "the second call should not have prompted"


async def test_always_is_scoped_to_one_tool() -> None:
    """"Always allow shell" must not also mean "always allow writes"."""
    asked: list[str] = []

    async def always(request: ApprovalRequest) -> str:
        asked.append(request.tool_name)
        return "always"

    policy = ApprovalPolicy(ROOT, asker=always)
    await policy(_call("run_shell", command="ls"))
    await policy(_call("write_file", path="a", content="b"))

    assert asked == ["run_shell", "write_file"]


async def test_a_denial_is_not_remembered() -> None:
    """The model is told why and may legitimately ask for something else next."""
    asked: list[str] = []

    async def deny(request: ApprovalRequest) -> str:
        asked.append(request.summary)
        return "deny"

    policy = ApprovalPolicy(ROOT, asker=deny)
    await policy(_call("run_shell", command="git clean -fdx"))
    await policy(_call("run_shell", command="ls"))

    assert len(asked) == 2, "a refusal must not silently blacklist the tool"


# ------------------------------------------------------------- the deny list


@pytest.mark.parametrize("command", CATASTROPHES)
async def test_catastrophes_are_denied_without_asking(command: str) -> None:
    """Prompting here is a trap - the answer arrives reflexively."""
    asked: list[str] = []

    async def record(request: ApprovalRequest) -> str:
        asked.append(request.summary)
        return "once"

    policy = ApprovalPolicy(ROOT, asker=record)
    decision = await policy(_call("run_shell", command=command))

    assert decision.allowed is False, command
    assert asked == [], "this must never reach a human for confirmation"
    assert "refused outright" in (decision.reason or "")


async def test_auto_approve_does_not_override_the_deny_list() -> None:
    """--yes means "stop asking me", not "disable the brakes"."""
    policy = ApprovalPolicy(ROOT, auto_approve=True)

    assert (await policy(_call("run_shell", command=CATASTROPHES[0]))).allowed is False


@pytest.mark.parametrize(
    "command", ["rm -rf build", "rm -rf ./node_modules", "git clean -fdx", "docker system prune"]
)
async def test_ordinary_destructive_commands_are_asked_not_denied(command: str) -> None:
    """The deny list must stay tight, or it becomes the thing people switch off."""
    policy = ApprovalPolicy(ROOT, asker=_yes)

    assert (await policy(_call("run_shell", command=command))).allowed is True


# ------------------------------------------------------------------- defaults


async def test_no_asker_means_deny() -> None:
    """A headless run must not quietly become an unattended one."""
    policy = ApprovalPolicy(ROOT)

    decision = await policy(_call("run_shell", command="ls"))
    assert decision.allowed is False
    assert "no approval channel" in (decision.reason or "").lower()


async def test_auto_approve_allows_ordinary_work() -> None:
    policy = ApprovalPolicy(ROOT, auto_approve=True)

    assert (await policy(_call("run_shell", command="ls"))).allowed is True
    assert (await policy(_call("write_file", path="a", content="b"))).allowed is True


# ------------------------------------------------------------------ the prompt


async def test_the_request_describes_what_will_happen() -> None:
    """A prompt the user cannot evaluate is worse than no prompt at all."""
    seen: list[ApprovalRequest] = []

    async def capture(request: ApprovalRequest) -> str:
        seen.append(request)
        return "once"

    policy = ApprovalPolicy(ROOT, asker=capture)
    await policy(_call("run_shell", command="pytest -q"))
    await policy(_call("write_file", path="notes.md", content="hello world"))
    await policy(_call("edit_file", path="app.py", old_text="a", new_text="b"))

    assert "pytest -q" in seen[0].summary
    assert "notes.md" in seen[1].summary
    assert "11" in seen[1].summary, "the size of the write is the thing to judge"
    assert "app.py" in seen[2].summary


# --------------------------------------------------------- it plugs into the loop


async def test_the_gate_stops_a_tool_through_the_real_loop() -> None:
    """End to end: the policy as a before_tool_call hook, and the tool never runs."""
    from omega_agent.harness import Harness
    from omega_agent.hooks import AgentHooks
    from omega_agent.tools import Tool, ToolResult
    from omega_agent.types import ToolResultMessage
    from omega_ai.fake import FakeProvider, text_turn, tool_turn

    ran: list[str] = []

    async def dangerous(arguments: dict[str, Any], signal: Any) -> ToolResult:
        ran.append("executed")
        return ToolResult(content="done")

    tool = Tool(
        name="run_shell", description="d", parameters={"type": "object"}, execute=dangerous
    )
    harness = Harness(
        provider=FakeProvider(
            [tool_turn("run_shell", {"command": CATASTROPHES[0]}), text_turn("ok")]
        ),
        model="m",
        system="s",
        tools=[tool],
        hooks=AgentHooks(before_tool_call=ApprovalPolicy(ROOT, auto_approve=True)),
    )

    async for _event in harness.run("clean up"):
        pass

    assert ran == [], "the deny list did not reach the loop"
    result = next(m for m in harness.messages if isinstance(m, ToolResultMessage))
    assert result.is_error is True
    assert "refused outright" in result.text


async def test_an_unresolvable_home_directory_does_not_crash_the_check(
    monkeypatch: Any,
) -> None:
    """A safety check that raises inside a tool call is worse than no check.

    Path.home() raises RuntimeError when it cannot resolve a home directory -
    a bare container, a stripped environment. The other targets still apply.
    """
    from pathlib import Path as _Path

    def boom() -> _Path:
        raise RuntimeError("no home")

    monkeypatch.setattr(_Path, "home", staticmethod(boom))
    policy = ApprovalPolicy(ROOT, asker=_yes)

    assert (await policy(_call("run_shell", command=CATASTROPHES[0]))).allowed is False
    assert (await policy(_call("run_shell", command="rm -rf build"))).allowed is True


# ------------------------------------------- what replaced the fence: outside reads


async def test_a_read_inside_the_root_is_still_not_gated(tmp_path: Path) -> None:
    """The exemption that keeps the gate worth reading.

    Dozens of reads per task, none of them prompted. Remove this and every other
    prompt gets less attention, which costs more safety than it buys.
    """
    asked: list[str] = []

    async def asker(request: ApprovalRequest) -> Answer:
        asked.append(request.summary)
        return "deny"

    policy = ApprovalPolicy(tmp_path, asker=asker)
    decision = await policy(
        ToolCall(id="1", name="read_file", arguments={"path": "src/loop.py"})
    )

    assert decision.allowed
    assert asked == [], "an inside-root read must not prompt"


async def test_a_read_outside_the_root_is_gated(tmp_path: Path) -> None:
    """**The test that makes removing the fence safe rather than reckless.**

    At Tier 2 `paths.py` refused this outright. The fence is gone, so if this
    read were still exempt the way every read used to be, omega would reach
    `~/.ssh/id_rsa` with no prompt, no record and nothing to notice it — strictly
    worse than the references it was copying. The read exemption narrowing to
    *inside the root* is the whole of the compensating control.
    """
    root = tmp_path / "project"
    root.mkdir()
    asked: list[str] = []

    async def asker(request: ApprovalRequest) -> Answer:
        asked.append(request.summary)
        return "deny"

    policy = ApprovalPolicy(root, asker=asker)
    decision = await policy(
        ToolCall(id="1", name="read_file", arguments={"path": "../secret.txt"})
    )

    assert not decision.allowed
    assert len(asked) == 1, "an outside-root read must prompt"
    assert "OUTSIDE" in asked[0], "the prompt has to say the path leaves the directory"


async def test_a_relative_path_that_climbs_out_is_treated_as_outside(
    tmp_path: Path,
) -> None:
    """The gate resolves before judging — bug #2 from `paths.py`, at this layer.

    `project/../secret.txt` is inside `project` right up until you normalise it.
    A gate that compared the raw string would wave this through, and the fence
    that used to catch it afterwards is no longer there.
    """
    root = tmp_path / "project"
    root.mkdir()
    seen: list[ApprovalRequest] = []

    async def asker(request: ApprovalRequest) -> Answer:
        seen.append(request)
        return "once"

    policy = ApprovalPolicy(root, asker=asker)
    await policy(
        ToolCall(id="1", name="read_file", arguments={"path": "sub/../../outside.txt"})
    )

    assert len(seen) == 1
    assert seen[0].outside_root is not None


async def test_always_outside_the_root_grants_a_directory_not_the_disk(
    tmp_path: Path,
) -> None:
    """"Always" outside the root is scoped to where you said yes.

    Per-tool would mean one `a` on a read of `~/Downloads/notes.txt` also
    authorises reading `~/.ssh/id_rsa` — the same keystroke, a wildly different
    grant. So outside-root grants are per-directory.
    """
    root = tmp_path / "project"
    root.mkdir()
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "a.txt").write_text("a")
    (downloads / "b.txt").write_text("b")
    elsewhere = tmp_path / "private"
    elsewhere.mkdir()

    prompts = 0

    async def asker(request: ApprovalRequest) -> Answer:
        nonlocal prompts
        prompts += 1
        return "always"

    policy = ApprovalPolicy(root, asker=asker)

    first = await policy(
        ToolCall(id="1", name="read_file", arguments={"path": str(downloads / "a.txt")})
    )
    second = await policy(
        ToolCall(id="2", name="read_file", arguments={"path": str(downloads / "b.txt")})
    )

    assert first.allowed and second.allowed
    assert prompts == 1, "the second read in a granted directory must not prompt again"

    # A different directory is a different question.
    await policy(
        ToolCall(id="3", name="read_file", arguments={"path": str(elsewhere / "keys")})
    )
    assert prompts == 2, "a grant on one directory must not cover another"


async def test_always_inside_the_root_is_still_per_tool(tmp_path: Path) -> None:
    """And the inside-root grain is unchanged: one tool, not one directory."""
    prompts = 0

    async def asker(request: ApprovalRequest) -> Answer:
        nonlocal prompts
        prompts += 1
        return "always"

    policy = ApprovalPolicy(tmp_path, asker=asker)

    await policy(ToolCall(id="1", name="write_file", arguments={"path": "a.txt", "content": "x"}))
    await policy(ToolCall(id="2", name="write_file", arguments={"path": "b.txt", "content": "y"}))
    assert prompts == 1, "always-allow should cover the whole tool inside the root"

    # ...and not leak into a different tool.
    await policy(ToolCall(id="3", name="run_shell", arguments={"command": "ls"}))
    assert prompts == 2, "always on write_file must not authorise run_shell"


async def test_always_on_the_shell_does_not_authorise_a_write_outside_the_root(
    tmp_path: Path,
) -> None:
    """The claim I got wrong when explaining this, now pinned as a test.

    Always-allowing `run_shell` hands over the machine *through the shell* —
    that part is real, and `run_shell` is deliberately ungated afterwards. What
    it must not do is authorise the **file tools** to leave the root, because
    those are a separate grant. One keystroke, one meaning.
    """
    root = tmp_path / "project"
    root.mkdir()
    prompts: list[str] = []

    async def asker(request: ApprovalRequest) -> Answer:
        prompts.append(request.tool_name)
        return "always"

    policy = ApprovalPolicy(root, asker=asker)

    assert (await policy(ToolCall(id="1", name="run_shell", arguments={"command": "ls"}))).allowed
    assert (await policy(ToolCall(id="2", name="run_shell", arguments={"command": "pwd"}))).allowed
    assert prompts == ["run_shell"], "the shell should stop asking"

    await policy(
        ToolCall(id="3", name="write_file", arguments={"path": "../out.txt", "content": "x"})
    )
    assert prompts == ["run_shell", "write_file"], "an outside write is its own question"


async def test_the_deny_list_still_wins_after_always_allowing_the_shell(
    tmp_path: Path,
) -> None:
    """Always-allow does not reach the refuse-outright list.

    Structural, not incidental: `_forbidden_reason` is checked before the
    `_always` set is consulted, so there is no ordering in which "always" can
    get in front of it.
    """
    prompts = 0

    async def asker(request: ApprovalRequest) -> Answer:
        nonlocal prompts
        prompts += 1
        return "always"

    policy = ApprovalPolicy(tmp_path, asker=asker)

    assert (await policy(ToolCall(id="1", name="run_shell", arguments={"command": "ls"}))).allowed
    assert prompts == 1

    decision = await policy(
        ToolCall(id="2", name="run_shell", arguments={"command": "rm -rf ~"})
    )
    assert not decision.allowed
    assert "refused outright" in (decision.reason or "")
    assert prompts == 1, "a catastrophe must not even reach the asker"


async def test_the_prompt_says_what_always_would_grant(tmp_path: Path) -> None:
    """One keystroke means two things, so the prompt has to say which."""
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "downloads" / "notes.txt"

    captured: list[ApprovalRequest] = []

    async def asker(request: ApprovalRequest) -> Answer:
        captured.append(request)
        return "deny"

    policy = ApprovalPolicy(root, asker=asker)
    await policy(ToolCall(id="1", name="write_file", arguments={"path": "a.txt", "content": "x"}))
    await policy(ToolCall(id="2", name="read_file", arguments={"path": str(outside)}))

    assert "every future write_file call" in captured[0].scope_note
    assert "downloads" in captured[1].scope_note


async def test_an_outside_grant_does_not_reach_into_subdirectories(
    tmp_path: Path,
) -> None:
    """A grant covers a folder's own files, not its whole subtree.

    The flaw this pins was mine, found by running the thing rather than reading
    it: granting recursively means approving *any* file sitting in your home
    directory also approves `~/.ssh/id_rsa`, because `~` is its parent's parent.
    One yes about `.zshrc` should not be a yes about your keys.
    """
    root = tmp_path / "project"
    root.mkdir()
    outer = tmp_path / "outer"
    nested = outer / "nested"
    nested.mkdir(parents=True)
    (outer / "top.txt").write_text("t")
    (outer / "sibling.txt").write_text("s")
    (nested / "deep.txt").write_text("d")

    prompts = 0

    async def asker(request: ApprovalRequest) -> Answer:
        nonlocal prompts
        prompts += 1
        return "always"

    policy = ApprovalPolicy(root, asker=asker)

    await policy(ToolCall(id="1", name="read_file", arguments={"path": str(outer / "top.txt")}))
    assert prompts == 1

    # Same directory: covered.
    await policy(
        ToolCall(id="2", name="read_file", arguments={"path": str(outer / "sibling.txt")})
    )
    assert prompts == 1, "a sibling in the granted directory should not re-prompt"

    # One level down: a new question.
    await policy(
        ToolCall(id="3", name="read_file", arguments={"path": str(nested / "deep.txt")})
    )
    assert prompts == 2, "a grant must not descend into subdirectories"
