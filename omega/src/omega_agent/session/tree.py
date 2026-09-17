"""The transcript is a tree, and always was.

Tier 2 wrote `parent_id` on every entry and read it nowhere, on one argument
recorded in `anatomy.md:314`: *"Retrofitting a tree onto a list is a rewrite."*
This file collects on that bet. **No record shape changed and no file already on
disk became unreadable** — the format was right from the start; only the reader
was wrong.

## What was actually broken

`store.load()` returned every entry in **file order**. While a session is a
straight line that is indistinguishable from correct, which is why it survived a
whole tier unchallenged. Give one parent two children and it silently returns
both branches concatenated: a conversation that never happened, containing two
different answers to the same question.

So this is not a feature added on top of correct code. It is a read path that was
wrong in a way nothing could observe until something branched.

## Why a file is a tree at all

Because the file is **append-only**, and that is not negotiable — it is what
makes a crash mid-turn survivable. You cannot delete the branch you abandoned, so
"go back three turns and try again" has to mean *write new entries hanging off an
older parent*, leaving the old ones in place. The moment that is possible, the
file describes a tree and reading it linearly is a bug.

## Both functions are pure

They take a list of entries and return entries. No file handles, no store, no
session id — so every test runs on a list built in three lines, and `store.py`
stays the only thing that knows where entries come from.
"""

from __future__ import annotations

from omega_agent.session.entries import SessionEntry


class CycleInTranscript(Exception):
    """A `parent_id` chain that loops.

    Nothing omega writes can produce one: `append` always hangs a fresh entry off
    the previous tail, and ids are never reused. A hand-edited or
    truncated-then-patched file can.

    It is raised rather than silently broken out of because the alternative is
    worse than either. A `while parent is not None` walk over a cycle **never
    returns**: the process hangs with no error, no output, and nothing to grep
    for. An exception naming the entry is a bug report; a hang is a mystery.
    """


def path_to(entries: list[SessionEntry], entry_id: str) -> list[SessionEntry]:
    """The chain from the root down to `entry_id`, in order.

    Returns `[]` for an id that is not present — a caller asking for a branch
    that was never written wants an empty conversation, not an exception.

    A **missing parent stops the walk** rather than failing it. A file killed
    mid-write leaves an entry whose parent never landed, and returning the part
    that exists is worth more than refusing to open the session at all. Same
    reasoning as `jsonl.py` skipping a torn final line.
    """
    by_id = {entry.id: entry for entry in entries}
    if entry_id not in by_id:
        return []

    chain: list[SessionEntry] = []
    seen: set[str] = set()
    current: str | None = entry_id

    while current is not None:
        if current in seen:
            raise CycleInTranscript(
                f"entry {current!r} is its own ancestor; the session file is corrupt"
            )
        seen.add(current)

        entry = by_id.get(current)
        if entry is None:
            break  # the parent never landed; keep what we have
        chain.append(entry)
        current = entry.parent_id

    chain.reverse()
    return chain


def leaves(entries: list[SessionEntry]) -> list[SessionEntry]:
    """Entries that nothing claims as a parent — the tip of each branch.

    One leaf means the session is a straight line, which is every session written
    before branching existed. Several mean it forked, and each is a separate
    conversation that can be resumed on its own.

    **File order is preserved** so the newest branch is last, which is what a
    picker wants and what "continue where I was" means.
    """
    claimed = {entry.parent_id for entry in entries if entry.parent_id is not None}
    return [entry for entry in entries if entry.id not in claimed]
