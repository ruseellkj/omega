"""What goes on a line of a session file.

Three record kinds, discriminated by `kind`, so one append-only file can hold
the session's metadata, its messages, and where the conversation currently ends
without a second file to keep in sync.

**`parent_id` is on every entry even though Tier 2 only ever writes a straight
line.** `anatomy.md:314` gives the reason without hedging: "Retrofitting a tree
onto a list is a rewrite." Tier 3 adds branching by writing a different
`parent_id`, not by changing the format of files already on disk.

**`version` is on every record.** It is what makes `migrate on read` possible at
all. A file written today has to be loadable by an omega that has changed its
mind about the schema, and that is only true if the file says which schema it is.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from omega_agent.types import AgentMessage, WireModel

#: Bumped whenever a record shape changes. `jsonl.py` migrates anything older.
SCHEMA_VERSION = 1


class SessionHeader(WireModel):
    """The first line of a session file. Written once, never updated."""

    kind: Literal["header"] = "header"
    version: int = SCHEMA_VERSION
    session_id: str
    created_at: str
    model: str


class SessionEntry(WireModel):
    """One message in the transcript, and where it hangs from."""

    kind: Literal["entry"] = "entry"
    version: int = SCHEMA_VERSION
    id: str
    parent_id: str | None = None
    message: AgentMessage


class SessionBranch(WireModel):
    """A rewind, written down: the next entry continues from `parent_id`.

    `None` means from the start, before the first entry.

    **Without this, a rewind lived only in memory.** `branch_from` moved a
    pointer on the store, and the file could not say that anything had moved.
    Ask q1 and q2, rewind one question, quit, and the next `--resume` loaded q1
    a1 q2 a2 again. An entry cannot carry this, because a rewind with nothing
    asked after it has no entry to write. So it is a record of its own.

    **No `SCHEMA_VERSION` bump**, because no existing shape changed. This is a
    new kind beside the other two, not a new version of either. A file written
    before it existed has no branch lines, and reads as it always did. An older
    omega reading a newer file fails to validate the unknown `kind` and skips the
    line (`jsonl.py:92-97`), so it loses only the rewind and continues from the
    newest leaf, which is exactly what it did before this record existed.
    """

    kind: Literal["branch"] = "branch"
    version: int = SCHEMA_VERSION
    parent_id: str | None = None


SessionRecord = Annotated[
    SessionHeader | SessionEntry | SessionBranch, Field(discriminator="kind")
]
