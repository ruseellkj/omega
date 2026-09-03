"""The approval gate — the whole of failure #4's *destruction* half since Tier 2.5.

At Tier 2 this file shared the job with a fence in `paths.py`: confinement bounded
*where* the file tools could go, and the gate covered the shell, which no amount
of path checking can confine. The fence is gone (see `paths.py` for why — neither
reference has one, and it made ordinary work impossible), so **this file inherited
its responsibility.**

That inheritance is the important part, and it is not a formality:

**Reads inside the root are not gated. Reads outside it are.** Tier 2 could leave
every read ungated because the fence had already bounded them. Take the fence
away and that same short-circuit becomes unprompted, unlogged access to
`~/.ssh/id_rsa`. So the read exemption narrowed from "reads" to "reads inside the
working directory" — one clause, and it is the only thing standing where the
fence used to.

**A prompt on every read would be worse than no prompt.** It would fire dozens of
times per task, and a prompt that fires constantly is a prompt people answer
without looking — which converts the gate into theatre and makes the *dangerous*
prompt less likely to be read. That is why the exemption narrowed rather than
disappearing.

**Catastrophes are denied, never asked.** Asking "are you sure?" about wiping a
disk is a trap, because by then the user is in the rhythm of saying yes. A short
list of things that are simply refused is worth more than a scarier prompt. The
list stays *tight* on purpose: a deny list that blocks ordinary work becomes the
feature people switch off. It is checked **above** both `--yes` and always-allow,
so neither can reach past it.

**No approval channel means deny.** If nothing can ask, the answer is no. The
alternative — treating "nobody is watching" as consent — is how a headless run
becomes an unattended one.

**"Always" is scoped, and scoped differently inside and out.** Inside the root it
means *this tool, this session* — you stop being asked about `write_file`.
Outside the root it means *the files directly in this directory, this session* —
approving a read of `~/Downloads/notes.txt` grants `~/Downloads`, not the disk,
and **not `~/Downloads/anything/deeper`** either. That last exclusion is load
bearing: a recursive grant on a file sitting in `~` would authorise `~/.ssh` in
the same keystroke. The asymmetry is
deliberate: per-tool is the right grain for "I trust you inside my project", and
much too coarse for "you may leave it". It will still surprise someone who
expects one keystroke to mean one thing, which is why `ApprovalRequest` carries
a `scope_note` saying which grant is on offer.

**It fills `before_tool_call`, which means the loop gains nothing from it.** A
whole policy subsystem — a deny list, a prompt, per-session memory — plugs in
through one callback. That is Boundary B doing the job it was drawn for in
Step 1, and it is the concrete answer to "why doesn't the loop grow?"
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from omega_agent.hooks import ALLOW, ToolCallDecision
from omega_agent.types import ToolCall
from omega_coding.paths import is_inside, resolve_path

#: What a human can answer. `always` is scoped — to one tool inside the root, to
#: one directory outside it. Never to everything: "yes, you may run commands"
#: must not also mean "yes, you may rewrite files".
Answer = Literal["once", "always", "deny"]


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """What the user is being asked to approve.

    `summary` is the whole value of this type. A prompt that says "allow
    run_shell?" cannot be evaluated; one that quotes the actual command can.

    `outside_root` exists so the prompt can say so. Writing to
    `project/notes.txt` and writing to `~/.ssh/authorized_keys` are not the same
    question, and they should not look the same either.
    """

    tool_name: str
    summary: str
    arguments: dict[str, Any]
    outside_root: Path | None = None

    @property
    def scope_note(self) -> str:
        """What "always" would grant here, in words, for whoever draws the prompt."""
        if self.outside_root is None:
            return f"always = every future {self.tool_name} call this session"
        return f"always = files directly in {self.outside_root} this session"


Asker = Callable[[ApprovalRequest], Awaitable[Answer]]

#: Tools that only observe. Gating these inside the root costs attention and
#: buys nothing. **Outside the root they are gated like anything else** — see the
#: module docstring; that narrowing is what replaced the fence.
READ_ONLY_TOOLS = frozenset({"read_file"})

#: Arguments that name a filesystem path, per tool. Used to work out whether a
#: call leaves the root. `run_shell` is absent on purpose: its `command` is not a
#: path, and deciding where a shell command will reach means parsing a shell.
#: It is gated unconditionally instead, which is the honest answer.
_PATH_ARGUMENTS: dict[str, str] = {
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
}


def _deletes_a_root(command: str) -> bool:
    """A recursive delete aimed at a filesystem or home root.

    Written as code rather than one heroic regex because the distinction is
    genuinely two-part — a recursive flag *and* a target that is a root — and a
    single pattern expressing both is unreadable, therefore unreviewable.

    The point of the target check is to let ordinary work through. Deleting a
    build directory recursively is a normal thing for a coding agent to want.
    """
    try:
        home = str(Path.home())
    except RuntimeError:
        # No home directory resolvable (a bare container, a stripped env). The
        # other targets still apply; crashing inside a safety check would be a
        # poor trade.
        home = ""
    for match in re.finditer(r"\brm\b(?P<rest>[^;&|\n]*)", command):
        rest = match.group("rest")
        flags = "".join(re.findall(r"(?<!\S)-(\w+)", rest)).lower()
        if "r" not in flags:
            continue
        for target in re.findall(r"(?<!\S)(?!-)(\S+)", rest):
            # Trailing slashes and globs do not change what is being deleted.
            bare = target.rstrip("/*")
            if bare in {"", "~", "$HOME", "/"} or (home and bare == home):
                return True
    return False


def _matches(pattern: str, flags: int = 0) -> Callable[[str], bool]:
    compiled = re.compile(pattern, flags)
    return lambda command: compiled.search(command) is not None


#: Refused outright, never prompted. Deliberately short — see the module
#: docstring on why a broad deny list is worse than a narrow one.
_FORBIDDEN: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("a recursive delete of a filesystem or home root", _deletes_a_root),
    ("formatting a filesystem", _matches(r"\bmkfs(\.\w+)?\b")),
    ("a raw write to a block device", _matches(r"\bdd\b[^\n]*\bof=/dev/")),
    ("a fork bomb", _matches(r":\s*\(\s*\)\s*\{.*\|.*&.*\}\s*;\s*:")),
    (
        "a recursive permission change on the filesystem root",
        _matches(r"\bchmod\s+(-\S+\s+)*777\s+/\s*$"),
    ),
    (
        "shutting down or rebooting the machine",
        _matches(r"\b(shutdown|reboot|halt|poweroff)\b"),
    ),
)


def _forbidden_reason(call: ToolCall) -> str | None:
    """Why this call is on the deny list, or None if it is not."""
    if call.name != "run_shell":
        return None
    command = str(call.arguments.get("command", ""))
    for reason, matches in _FORBIDDEN:
        if matches(command):
            return reason
    return None


def _summarise(call: ToolCall, outside: Path | None) -> str:
    """One line a human can judge in the time they will actually spend on it."""
    arguments = call.arguments
    if call.name == "run_shell":
        return str(arguments.get("command", ""))

    marker = "  [OUTSIDE the working directory]" if outside is not None else ""
    if call.name == "write_file":
        content = str(arguments.get("content", ""))
        return f"write {len(content)} chars to {arguments.get('path', '?')}{marker}"
    if call.name == "edit_file":
        return f"edit {arguments.get('path', '?')}{marker}"
    if call.name == "read_file":
        return f"read {arguments.get('path', '?')}{marker}"
    return f"{call.name}({arguments})"


class ApprovalPolicy:
    """A `before_tool_call` hook that asks before anything changes.

    Callable, so it *is* the hook — no adapter, no registration step. `AgentHooks`
    accepts any callable of the right shape, which is what a `Callable` alias
    buys over a base class.

    `root` is required rather than defaulted. The policy cannot tell an
    inside-root read from an outside-root one without it, and one that silently
    defaulted to `Path.cwd()` would be right in the CLI and wrong everywhere else
    — tests, evals, headless runs — in a way nothing would catch.
    """

    def __init__(
        self,
        root: Path,
        *,
        asker: Asker | None = None,
        auto_approve: bool = False,
        read_only: frozenset[str] = READ_ONLY_TOOLS,
    ) -> None:
        self._root = root.resolve()
        self._asker = asker
        self._auto_approve = auto_approve
        self._read_only = read_only

        #: Tools the user said "always" to, for work inside the root. Per session,
        #: in memory. A persisted trust store is a config concern and arrives
        #: with config — which is exactly what Claude Code's on-disk rules are,
        #: and why it can remember an answer omega has to ask for again.
        self._always: set[str] = set()

        #: Directories outside the root the user said "always" to. Separate from
        #: `_always` because the grain differs — see the module docstring.
        self._always_dirs: set[Path] = set()

    def _outside_path(self, call: ToolCall) -> Path | None:
        """The resolved path this call touches, if it lands outside the root.

        None when the call stays inside, names no path, or names one that cannot
        be resolved — the last because refusing an unusable path is the *tool's*
        job, and it will do it a moment later with a better message than the gate
        could write.
        """
        argument = _PATH_ARGUMENTS.get(call.name)
        if argument is None:
            return None
        raw = call.arguments.get(argument)
        if not isinstance(raw, str):
            return None
        try:
            resolved = resolve_path(raw, self._root)
        except Exception:  # noqa: BLE001 - unresolvable is the tool's error to raise
            return None
        return None if is_inside(resolved, self._root) else resolved

    def _already_granted(self, outside: Path) -> bool:
        """Has some earlier "always" already covered this outside-root path?

        **Direct children only — a grant is not recursive.** This looked like a
        detail and is not. Approving one file grants its directory, so a
        recursive grant on `~/.zshrc` would hand over `~/.ssh` in the same
        keystroke: everything under home, from one yes about a shell config.
        Non-recursive keeps "always" useful for the case it exists for — several
        files in one folder — while making each new folder its own question.
        """
        return outside.parent in self._always_dirs

    async def __call__(self, call: ToolCall) -> ToolCallDecision:
        # First, above everything. Neither --yes nor always-allow can reach past
        # this, which is the property that makes the deny list worth having.
        forbidden = _forbidden_reason(call)
        if forbidden is not None:
            return ToolCallDecision(
                allowed=False,
                reason=(
                    f"This is refused outright ({forbidden}) and was not run. "
                    "It is not something the user can approve. Achieve the goal a "
                    "narrower way, or explain what you were trying to do."
                ),
            )

        outside = self._outside_path(call)

        # The narrowed read exemption. Inside the root only — this one clause is
        # what the fence used to be.
        if outside is None and call.name in self._read_only:
            return ALLOW

        if self._auto_approve:
            return ALLOW

        if outside is None:
            if call.name in self._always:
                return ALLOW
        elif self._already_granted(outside):
            return ALLOW

        if self._asker is None:
            where = " outside the working directory" if outside is not None else ""
            return ToolCallDecision(
                allowed=False,
                reason=(
                    f"Denied: no approval channel is available, so {call.name}{where} cannot "
                    "be confirmed. Run omega interactively, or start it with --yes to approve "
                    "automatically."
                ),
            )

        answer = await self._asker(
            ApprovalRequest(
                tool_name=call.name,
                summary=_summarise(call, outside),
                arguments=dict(call.arguments),
                outside_root=outside.parent if outside is not None else None,
            )
        )

        if answer == "always":
            if outside is None:
                self._always.add(call.name)
            else:
                # The directory, not the file. Granting one file at a time would
                # make "always" indistinguishable from "once" for any real task.
                self._always_dirs.add(outside.parent)
            return ALLOW
        if answer == "once":
            return ALLOW

        # Not remembered. The model is told why and may legitimately ask for
        # something different next; a remembered refusal would block that too.
        return ToolCallDecision(
            allowed=False,
            reason=(
                f"The user declined this {call.name} call. Do not retry it as-is - "
                "ask what they would prefer, or take a different approach."
            ),
        )
