"""The three Tier-1 tools: read a file, write a file, run a command.

Ported from the working logic in the repo-root `agent.py`, with three changes:

* **output goes through a budget** (`truncate.py`) — the failure that ends a
  session fastest
* **failures raise** rather than returning a flag, so there is one way to fail
  and the loop owns the conversion
* **a failing command carries its output with it**, because "exited with code 1"
  tells the model nothing it can act on

Deliberately absent at this tier: `edit`, path confinement, and any approval
check. Nothing here is safe to point at a directory you care about — run it in a
scratch folder. `../docs/00-concepts/security.md` explains what Tier 2 adds and why.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from omega.tools import Tool, ToolError, ToolResult
from omega.truncate import MAX_BYTES, MAX_LINES, truncate_output
from omega.types import CancellationToken

# The model is told the budget it will be subject to. Interpolated from the same
# constants the code enforces, so the description cannot drift from the behaviour.
_TRUNCATION_NOTE = (
    f"Output is truncated to the last {MAX_LINES} lines or {MAX_BYTES // 1024}KB "
    "(whichever is hit first). If truncated, the full output is saved to a temp "
    "file and its path is included in the result."
)


async def _read_file(arguments: dict[str, Any], signal: CancellationToken | None) -> ToolResult:
    path = Path(arguments["path"])
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ToolError(f"File not found: {path}") from exc
    except IsADirectoryError as exc:
        raise ToolError(f"Not a file: {path}") from exc
    except UnicodeDecodeError as exc:
        raise ToolError(f"Not a UTF-8 text file: {path}") from exc
    except OSError as exc:
        raise ToolError(f"Could not read {path}: {exc}") from exc

    body, truncation = truncate_output(raw, label="read")
    return ToolResult(
        content=body,  # type: ignore[arg-type]
        details={"path": str(path), "truncated": truncation.truncated},
    )


async def _write_file(arguments: dict[str, Any], signal: CancellationToken | None) -> ToolResult:
    path = Path(arguments["path"])
    content = arguments["content"]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"Could not write {path}: {exc}") from exc

    return ToolResult(
        content=f"Wrote {len(content)} chars to {path}",  # type: ignore[arg-type]
        details={"path": str(path), "chars": len(content)},
    )


async def _run_shell(arguments: dict[str, Any], signal: CancellationToken | None) -> ToolResult:
    command = arguments["command"]

    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
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


READ_FILE = Tool(
    name="read_file",
    description=f"Read a UTF-8 text file and return its contents. {_TRUNCATION_NOTE}",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to the file to read."}},
        "required": ["path"],
    },
    execute=_read_file,
)

WRITE_FILE = Tool(
    name="write_file",
    description=(
        "Write text to a file, replacing anything already there. "
        "Parent directories are created if needed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write."},
            "content": {"type": "string", "description": "The full contents to write."},
        },
        "required": ["path", "content"],
    },
    execute=_write_file,
)

RUN_SHELL = Tool(
    name="run_shell",
    description=(
        "Run a shell command in the current working directory and return its combined "
        "stdout and stderr. A non-zero exit is reported as an error, with the output "
        f"included. {_TRUNCATION_NOTE}"
    ),
    parameters={
        "type": "object",
        "properties": {"command": {"type": "string", "description": "The command to run."}},
        "required": ["command"],
    },
    execute=_run_shell,
)

DEFAULT_TOOLS: list[Tool] = [READ_FILE, WRITE_FILE, RUN_SHELL]
