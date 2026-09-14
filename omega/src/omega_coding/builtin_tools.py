"""The four file/shell tools: read, write, edit, run.

Tier 1 had three of these and no fence around any of them. Tier 2 adds `edit`
and puts all of them inside one:

* **paths go through `paths.py`** — one resolution point, not four (failure #4)
* **writes go through `file_lock.py`** — one lock per resolved file (failure #8)
* **file I/O happens in a thread** — a synchronous `read_text` on a large file
  blocks the whole event loop, and it is also what makes the lock load-bearing
  rather than decorative
* **`run_shell` has a timeout** and honours cancellation, so a hung command is
  survivable

Tools are built by a **factory**, not declared as module constants. The root has
to be decided by the caller: baking `Path.cwd()` in at import time makes it
impossible to test two roots in one process, and gives `--fake` in a temp
directory the wrong root.

Still true, and stated rather than implied: **`run_shell` is not confined by any
of this.** `cd .. && cat ~/.ssh/id_rsa` walks straight out, and parsing shell
commands to prevent that is a game you lose. The shell is covered by the
approval gate instead, and real containment is Tier 3+ sandboxing — for which
`prepare_shell` below is the seam.

**Tier 2.5 removed the fence from the file tools too**, so the paragraph above
now describes all four rather than one. `paths.py` carries the argument; the
short version is that both references and Claude Code work this way, and the gate
in `approval.py` picked up the job — including asking about *reads* outside the
root, which the fence used to cover for free. `--confine` restores the old
behaviour.

**The descriptions were rewritten at the same time**, against Pi's
`packages/agent/src/harness/tools/*.ts` and Tau's `tau_coding/tools.py`. The
lesson taken from both: a description says what the tool does, what its limits
are, and how to drive its parameters. It does **not** say "whenever you are asked
to read a file, use this tool" — steering between tools goes in the system
prompt, via `Tool.guidelines`, which is Tau's `prompt_guidelines` under another
name. Cramming it into the description was the immature version, and it is also
the weaker one: a description is read *after* the tool has been chosen.

One further borrowing: `read_file` gained `offset`/`limit`, which both references
have and Tier 2 lacked. Without them a file past the truncation budget is simply
unreadable beyond its tail, and the model's only recourse is `sed` through the
shell — precisely the escape the new guidelines tell it not to take.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from omega_agent.tools import Tool, ToolError, ToolResult
from omega_agent.types import CancellationToken
from omega_coding.file_lock import FILE_LOCKS, FileLocks
from omega_coding.paths import resolve_path, resolve_within_root
from omega_coding.truncate import MAX_BYTES, MAX_LINES, truncate_output

#: How long a command may run before it is killed. A hung command used to hang
#: the agent forever, which is the kind of bug that makes a tool untrustworthy.
DEFAULT_TIMEOUT_SECONDS = 120.0

#: How often to check in on a running command. Small enough that Ctrl-C feels
#: immediate, large enough not to spin.
_POLL_SECONDS = 0.1

#: Rewrites a command before it runs. **The sandboxing seam.** A Tier 3+ sandbox
#: wraps the command here (`sandbox-exec -f profile ...`) without any tool,
#: hook, or loop needing to change.
ShellPrepare = Callable[[str], str]

# The model is told the budget it will be subject to. Interpolated from the same
# constants the code enforces, so the description cannot drift from the behaviour.
_TRUNCATION_NOTE = (
    f"Output is truncated to the last {MAX_LINES} lines or {MAX_BYTES // 1024}KB "
    "(whichever is hit first). If truncated, the full output is saved to a temp "
    "file and its path is included in the result."
)

#: Said once, in the words the model needs: relative to *what*. Tier 2's version
#: of this note announced a fence that no longer exists, and a description that
#: describes absent behaviour is worse than no description.
_PATH_NOTE = (
    "Paths may be relative (resolved against the working directory) or absolute. "
    "Paths outside the working directory are allowed but require the user's "
    "approval, so prefer paths inside it when either would do."
)


def _write_text(path: Path, content: str) -> None:
    """Create the parents, then write. Runs in a thread.

    The parents are created *after* the path has been resolved and approved —
    Tier 1 did the `mkdir` first, which meant a refused write had still created
    directories somewhere it had no business being.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _as_tool_error(path: Path, exc: OSError | UnicodeDecodeError) -> ToolError:
    """One place for "why couldn't you read that", in words a model can act on."""
    if isinstance(exc, FileNotFoundError):
        return ToolError(f"File not found: {path}")
    if isinstance(exc, IsADirectoryError):
        return ToolError(f"Not a file: {path}")
    if isinstance(exc, UnicodeDecodeError):
        return ToolError(f"Not a UTF-8 text file: {path}")
    return ToolError(f"Could not read {path}: {exc}")


def _apply_window(
    text: str, offset: Any, limit: Any
) -> tuple[str, dict[str, Any]]:
    """Take a line window out of `text`, the way both references do.

    `offset` is 1-based because that is what an editor, a stack trace and a
    `file:line` reference all use, and a model that has just been shown
    `loop.py:144` should be able to pass 144.

    Returns the window plus what to report in `details`, so a caller knows it
    received part of a file without that costing tokens in `content`.
    """
    if offset is None and limit is None:
        return text, {}

    lines = text.splitlines(keepends=True)
    start = max(int(offset) - 1, 0) if offset is not None else 0
    stop = start + int(limit) if limit is not None else len(lines)
    window = lines[start:stop]

    return "".join(window), {
        "offset": start + 1,
        "lines_returned": len(window),
        "total_lines": len(lines),
        # The model's cue to continue rather than assume it saw everything.
        "next_offset": start + len(window) + 1 if stop < len(lines) else None,
    }


def _occurrence_lines(text: str, needle: str) -> list[int]:
    """1-based line numbers where `needle` starts. For the ambiguity message."""
    lines: list[int] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index == -1:
            return lines
        lines.append(text.count("\n", 0, index) + 1)
        start = index + 1


async def _terminate(process: asyncio.subprocess.Process, pending: asyncio.Task[Any]) -> None:
    """Kill a command and reap it, leaving no zombie and no orphaned task."""
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    pending.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await pending
    with contextlib.suppress(ProcessLookupError):
        await process.wait()


def build_tools(
    root: Path | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    locks: FileLocks | None = None,
    prepare_shell: ShellPrepare | None = None,
    confine: bool = False,
) -> list[Tool]:
    """The four tools, rooted at `root` (the current directory by default).

    A factory rather than four constants, because the root is a property of the
    session and not of the module. The closures below capture it, so no tool can
    be handed the wrong one by accident.

    `confine=True` restores the Tier 2 hard fence: outside-root paths raise
    instead of being referred to the gate. One flag, one swapped function — not a
    second policy path, which would be two behaviours to keep correct.
    """
    base = (root if root is not None else Path.cwd()).resolve()
    table = locks if locks is not None else FILE_LOCKS
    resolve = resolve_within_root if confine else resolve_path

    async def read_file(
        arguments: dict[str, Any], signal: CancellationToken | None
    ) -> ToolResult:
        path = resolve(arguments["path"], base)
        try:
            raw = await asyncio.to_thread(_read_text, path)
        except (OSError, UnicodeDecodeError) as exc:
            raise _as_tool_error(path, exc) from exc

        raw, window = _apply_window(raw, arguments.get("offset"), arguments.get("limit"))
        body, truncation = truncate_output(raw, label="read")
        return ToolResult(
            content=body,  # type: ignore[arg-type]
            details={"path": str(path), "truncated": truncation.truncated, **window},
        )

    async def write_file(
        arguments: dict[str, Any], signal: CancellationToken | None
    ) -> ToolResult:
        path = resolve(arguments["path"], base)
        content = arguments["content"]

        async with table.for_path(path):
            try:
                await asyncio.to_thread(_write_text, path, content)
            except OSError as exc:
                raise ToolError(f"Could not write {path}: {exc}") from exc

        return ToolResult(
            content=f"Wrote {len(content)} chars to {path}",  # type: ignore[arg-type]
            details={"path": str(path), "chars": len(content)},
        )

    async def edit_file(
        arguments: dict[str, Any], signal: CancellationToken | None
    ) -> ToolResult:
        """Replace one exact, unique run of text.

        Uniqueness is required rather than "replace the first match". A model
        that meant the second occurrence and silently got the first has made a
        wrong edit that looks like a successful one — the worst outcome
        available. Refusing costs one turn and asks for more context.
        """
        path = resolve(arguments["path"], base)
        old = arguments["old_text"]
        new = arguments["new_text"]

        if not old:
            raise ToolError(
                "old_text must not be empty. Use write_file to create a file or "
                "replace its whole contents."
            )

        async with table.for_path(path):
            try:
                text = await asyncio.to_thread(_read_text, path)
            except (OSError, UnicodeDecodeError) as exc:
                raise _as_tool_error(path, exc) from exc

            occurrences = text.count(old)
            if occurrences == 0:
                raise ToolError(
                    f"old_text was not found in {path}. It must match the file exactly, "
                    "including whitespace and indentation. Read the file again and copy "
                    "the text you want to replace."
                )
            if occurrences > 1:
                where = ", ".join(str(line) for line in _occurrence_lines(text, old))
                detail, _ = truncate_output(where, label="edit-matches")
                raise ToolError(
                    f"old_text appears {occurrences} times in {path} (lines {detail}). "
                    "It must match exactly once - include more surrounding context to "
                    "make it unique."
                )

            updated = text.replace(old, new, 1)
            try:
                await asyncio.to_thread(path.write_text, updated, encoding="utf-8")
            except OSError as exc:
                raise ToolError(f"Could not write {path}: {exc}") from exc

        # The content/details split, finally doing something. The model gets one
        # line; a renderer gets the whole diff and it costs no tokens.
        diff = "".join(
            difflib.unified_diff(
                text.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{path.name}",
                tofile=f"b/{path.name}",
            )
        )
        removed = old.count("\n") + 1
        added = new.count("\n") + 1
        return ToolResult(
            content=f"Edited {path}: replaced {removed} line(s) with {added}.",  # type: ignore[arg-type]
            details={
                "path": str(path),
                "diff": diff,
                "lines_removed": removed,
                "lines_added": added,
            },
        )

    async def run_shell(
        arguments: dict[str, Any], signal: CancellationToken | None
    ) -> ToolResult:
        command = arguments["command"]
        if prepare_shell is not None:
            command = prepare_shell(command)

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=base,
        )

        # A task rather than `wait_for(process.communicate(), ...)`: a timeout on
        # wait_for cancels communicate() mid-read, which can lose output and
        # leave the pipes in a bad state. Polling a task lets us check the
        # cancellation signal without ever interrupting the read.
        reading: asyncio.Task[tuple[bytes, bytes]] = asyncio.ensure_future(
            process.communicate()
        )
        waited = 0.0
        while True:
            done, _ = await asyncio.wait({reading}, timeout=_POLL_SECONDS)
            if reading in done:
                stdout, _ = reading.result()
                break

            waited += _POLL_SECONDS
            if signal is not None and signal.is_cancelled():
                await _terminate(process, reading)
                raise ToolError(f"Cancelled after {waited:.1f}s: {command}")
            if waited >= timeout:
                await _terminate(process, reading)
                raise ToolError(
                    f"Command timed out after {timeout:.0f}s and was killed: {command}"
                )

        raw = stdout.decode("utf-8", errors="replace") or "(no output)"
        body, truncation = truncate_output(raw, label="shell")

        if process.returncode != 0:
            # The output is the whole point of the error. A bare "exited with code 1"
            # leaves the model guessing and it will retry the same thing.
            raise ToolError(f"{body}\n\nCommand exited with code {process.returncode}")

        return ToolResult(
            content=body,  # type: ignore[arg-type]
            details={"exit_code": process.returncode, "truncated": truncation.truncated},
        )

    return [
        Tool(
            name="read_file",
            description=(
                "Read the contents of a UTF-8 text file. "
                f"{_TRUNCATION_NOTE} Use offset/limit for large files; when you need the "
                "whole file, continue with offset until you have it. "
                f"{_PATH_NOTE}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to read"},
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-indexed)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read",
                    },
                },
                "required": ["path"],
            },
            guidelines=(
                # Tau's line, in intent: this is what stops a model shelling out
                # to `cat`, and it only works from the system prompt.
                "Use read_file to examine files instead of cat, head or sed",
                "Read a file before editing it; never edit from memory of its contents",
            ),
            execute=read_file,
        ),
        Tool(
            name="write_file",
            description=(
                "Write content to a file. Creates the file if it does not exist, overwrites "
                "it entirely if it does. Parent directories are created automatically. "
                f"{_PATH_NOTE}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to write"},
                    "content": {"type": "string", "description": "Content to write to the file"},
                },
                "required": ["path", "content"],
            },
            guidelines=("Use write_file only for new files or complete rewrites",),
            execute=write_file,
        ),
        Tool(
            name="edit_file",
            description=(
                "Edit a file by exact text replacement. old_text must match a unique region "
                "of the file exactly, including whitespace and indentation - copy it verbatim "
                "from a read_file result rather than retyping it, and include enough "
                "surrounding context to be unique. Returns the number of lines changed. "
                f"{_PATH_NOTE}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to edit"},
                    "old_text": {
                        "type": "string",
                        "description": (
                            "The exact text to replace. Must occur exactly once in the file."
                        ),
                    },
                    "new_text": {
                        "type": "string",
                        "description": (
                            "Replacement text. May be empty to delete the matched region."
                        ),
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
            guidelines=(
                "Use edit_file to change part of an existing file, write_file only to "
                "create one or replace it entirely",
                "If old_text is reported as ambiguous, read more of the file and include "
                "more context - do not guess",
            ),
            execute=edit_file,
        ),
        Tool(
            name="run_shell",
            description=(
                "Execute a shell command in the working directory. Returns combined stdout "
                "and stderr. A non-zero exit is reported as an error with the output "
                f"included. {_TRUNCATION_NOTE} "
                f"Commands are killed after {DEFAULT_TIMEOUT_SECONDS:.0f} seconds. "
                "The command itself is not restricted to the working directory."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"}
                },
                "required": ["command"],
            },
            guidelines=(
                "Use run_shell for builds, tests and version control - not to read or write "
                "files, which the file tools do under the user's oversight",
                "Prefer the project's documented commands and package manager",
            ),
            execute=run_shell,
        ),
    ]
