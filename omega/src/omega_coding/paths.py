"""Path resolution — and, optionally, confinement.

**This file changed shape at Tier 2.5, and the change is worth understanding
before reading the code.**

At Tier 2 this was a *fence*: every file tool refused any path outside the launch
directory. That fixed half of beginner failure **#4** — Tier 1 could read
`~/.ssh/id_rsa` — but it did so by making omega unable to do a large class of
ordinary work. Neither reference confines paths at all: Pi's `resolveToolPath`
only makes a path absolute, and Tau's `tools.py` resolves exactly once, to key a
lock. Claude Code reads outside its project too. All three rely on a **human
gate** instead.

So the fence came out, and its job moved to `approval.py`. The rule now:

* **inside the root** — reads are free, writes are asked about (unchanged)
* **outside the root** — *everything* is asked about, **reads included**

That last clause is the whole reason this is one change and not two. The fence
was the only thing standing between the model and your private keys on the read
path, because `read_file` is not gated inside the root and never was. Remove the
fence without gating outside reads and you get something strictly worse than any
reference: silent, unprompted, unlogged access to the entire disk.

`resolve_path` is therefore the default, and `resolve_within_root` survives for
`--confine`, which restores the old hard fence for anyone who wants it.

**The resolution logic below is unchanged, and is still the interesting part.**
Knowing *whether* a path is outside the root is the same problem as refusing it
was, and three plausible implementations are wrong:

1. **`str.startswith`** — `/repo-evil` starts with `/repo`.
2. **Comparing before resolving** — `project/../../etc/passwd` is inside
   `project` until you normalise it.
3. **`Path.resolve()` on the whole path** — it cannot follow a symlink it never
   reaches. `project/link/new.txt`, where `link` points outside and `new.txt`
   does not exist yet, resolves without ever traversing `link`. Since
   `write_file` exists precisely to create files that are not there, this is the
   common case, not the exotic one.

The third is why this file is longer than a one-liner. It matters *more* now, not
less: a path misjudged as inside the root is a prompt the user never sees.
"""

from __future__ import annotations

import os
from pathlib import Path

from omega_agent.tools import ToolError


class UnusablePath(ToolError):
    """The path cannot be resolved at all — a null byte, a symlink loop.

    Distinct from `PathOutsideRoot`: this is not a policy refusal but a statement
    that there is nothing here to act on. Raised in both modes.
    """


class PathOutsideRoot(ToolError):
    """Refused by the hard fence. **Only raised under `--confine`.**

    A `ToolError`, so the loop turns it into a tool result the model reads and
    adapts to. A refusal is an observation, not a crash — and one that names the
    path and the root is a refusal the model can act on instead of retrying.
    """


def resolve_path(candidate: str | Path, root: Path) -> Path:
    """Resolve `candidate` against `root` and return it, wherever it lands.

    The default. Relative paths are taken as relative to `root`; absolute paths
    are honoured. Nothing is refused for being outside — that is the gate's call
    now, and it needs the resolved path in order to make it.

    The returned path is fully resolved, which makes it the right key for
    `FileLocks` as well: two names for one file must share one lock.
    """
    resolved_root = root.resolve()

    # Checked explicitly because pathlib will not do it for us: `Path.exists()`
    # swallows the ValueError that a null byte raises and simply returns False,
    # so an embedded null would slip through the walk below unnoticed.
    if "\x00" in str(candidate):
        raise UnusablePath(f"Refused: {candidate!r} contains a null byte.")

    raw = Path(candidate)
    target = raw if raw.is_absolute() else resolved_root / raw

    try:
        return _resolve_through_existing(target)
    except (OSError, ValueError) as exc:
        # A path too long, a loop of symlinks. Unusable either way, and refusing
        # is the honest answer.
        raise UnusablePath(f"Refused: {candidate!r} is not a usable path ({exc}).") from exc


def is_inside(resolved: Path, root: Path) -> bool:
    """Is this already-resolved path within `root`?

    Takes a *resolved* path on purpose. Resolving inside a predicate invites a
    caller to test one path and then act on another, which is bug #2 in the
    module docstring wearing a different hat.
    """
    resolved_root = root.resolve()
    return resolved == resolved_root or resolved.is_relative_to(resolved_root)


def resolve_within_root(candidate: str | Path, root: Path) -> Path:
    """Resolve, and refuse anything outside `root`. **The `--confine` path.**

    Kept deliberately after the fence stopped being the default. It is the
    stricter mode for people who want it, it is where the three-wrong-ways lesson
    above stays under test, and it is what a Tier 3 sandbox profile will be
    derived from.
    """
    resolved = resolve_path(candidate, root)
    if not is_inside(resolved, root):
        raise PathOutsideRoot(
            f"Refused: {candidate} resolves to {resolved}, which is outside the working "
            f"directory {root.resolve()}. omega was started with --confine, which does not "
            "allow this even with approval."
        )
    return resolved


def _resolve_through_existing(target: Path) -> Path:
    """Resolve the deepest ancestor that exists, then re-append the rest.

    This is the part that closes the symlinked-parent hole. Walking up to
    something real forces every link along the way to be followed; the tail that
    does not exist yet is then appended to wherever that really was.

    A symlink is treated as existing even when it dangles. A link pointing
    outside the root has declared its intent, and the file it points at may be
    created a moment later.
    """
    remainder: list[str] = []
    probe = target

    while not (probe.exists() or probe.is_symlink()):
        parent = probe.parent
        if parent == probe:  # reached the filesystem root
            break
        remainder.append(probe.name)
        probe = parent

    resolved = probe.resolve()
    for name in reversed(remainder):
        resolved = resolved / name

    # The remainder can contain `..` of its own — `a/b/../../../outside.txt`
    # walks all the way up to an existing ancestor and carries every component
    # back with it. Collapsing those lexically is safe *here* precisely because
    # these components did not exist when probed, and a path that does not exist
    # cannot be a symlink. Without this the `..` survives into the comparison and
    # the path looks like it is inside the root.
    return Path(os.path.normpath(resolved))
