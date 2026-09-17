"""Where sessions live.

A `Protocol` plus one implementation, so the backend can change without the
harness noticing. Worth being accurate about the precedent: Pi ships a SQLite
session store, but in `packages/storage` for its *server* — `grep -rn sqlite` in
its CLI source returns nothing, and the CLI uses JSONL like everyone else. JSONL
is the right size here too, and the interface is what makes a later swap an
addition rather than surgery.

Sessions land in `~/.omega/sessions/<project-key>/<id>.jsonl`.

**That location changed at Tier 2.5, and the reason is worth recording.** Tier 2
put them in `<project>/.omega/sessions/`, beside the work they describe. That
reads well and is wrong in practice, for three reasons all three references
avoided:

1. **`git clean -xdf` deletes your history.** An untracked directory inside the
   repo is exactly what `git clean` exists to remove.
2. **It needs gitignoring in every repo**, or transcripts — which contain whatever
   you pasted into the agent — get committed.
3. **One place to look.** `ls ~/.omega/sessions/` shows every project you have
   ever used omega in; hunting for `.omega` directories does not.

Pi, Tau and Claude Code all store under the home directory and encode the project
path into a subdirectory name. omega now does the same, using Tau's scheme — a
readable slug **and** a hash:

    ~/.omega/sessions/Users-me-code-myapp-3f9c1a/20260903T142211-a3f9c1.jsonl
                      |___ slug ______||_hash_|  |___ session id _________|

The slug is for a human reading `ls`. The hash is what makes it correct: two
different projects both called `api` produce different directories, which a slug
alone cannot guarantee.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from omega_agent.session.entries import SessionEntry, SessionHeader
from omega_agent.session.jsonl import append_record, read_records
from omega_agent.session.tree import CycleInTranscript, leaves, path_to
from omega_agent.types import AgentMessage


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """One row of `--sessions`. Enough to choose, not enough to be expensive."""

    session_id: str
    modified: datetime
    messages: int
    first_prompt: str


class SessionStore(Protocol):
    """The storage seam. Seven methods, all the harness and CLI need.

    Two arrived with Tier 3 branching — `entries` and `branch_from`. Both are on
    the Protocol rather than the concrete class because the harness calls them,
    and a seam a caller has to downcast through is not a seam.
    """

    def create_session(self, *, model: str) -> str:
        """Start a session and return its id."""
        ...

    def append(self, session_id: str, message: AgentMessage) -> None:
        """Add one message to the end of a session."""
        ...

    def entries(self, session_id: str) -> list[SessionEntry]:
        """Every entry with its id and parent, in write order."""
        ...

    def branch_from(self, session_id: str, entry_id: str | None) -> None:
        """Hang the next appended entry off `entry_id` rather than the tail."""
        ...

    def load(self, session_id: str, *, branch: str | None = None) -> list[AgentMessage]:
        """One branch of a session, root first. Empty if it does not exist."""
        ...

    def latest_session_id(self) -> str | None:
        """The most recently written session, for `--continue`."""
        ...

    def list_sessions(self) -> list[SessionInfo]:
        """Every saved session for this project, newest first."""
        ...


#: How much of the path hash to keep. Six hex digits is 16.7 million buckets —
#: far more than the number of projects anyone has, and short enough that the
#: directory name stays readable.
_HASH_LENGTH = 6

#: Longest slug before it is trimmed. The hash carries correctness, so the slug
#: can be cut freely; this only stops absurd directory names.
_SLUG_LENGTH = 48


def project_key(project_root: Path) -> str:
    """A directory name for one project: readable slug, then a hash of the path.

    Both halves earn their place. **The hash is what makes it correct** — two
    checkouts of different repos can both be called `api`, and a slug alone would
    put their sessions in the same directory. **The slug is what makes it
    usable** — `ls ~/.omega/sessions/` should tell you which project is which
    without opening anything.

    Hashing the *resolved* path, so a symlinked route to the same project does
    not get a second history.
    """
    resolved = project_root.resolve()
    digest = sha256(str(resolved).encode("utf-8")).hexdigest()[:_HASH_LENGTH]

    parts = [part for part in resolved.parts if part not in (resolved.anchor, "")]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", "-".join(parts)).strip("-")[-_SLUG_LENGTH:].strip("-")

    return f"{slug}-{digest}" if slug else digest


class JsonlSessionStore:
    """One append-only JSONL file per session, under `~/.omega/sessions/`.

    `home` exists for the same reason `env.py` has one: tests must not write to
    the real home directory, and a store that could only ever use `Path.home()`
    would force them to. Production passes nothing and gets `Path.home()`.
    """

    def __init__(self, project_root: Path, *, home: Path | None = None) -> None:
        base = (home if home is not None else Path.home()) / ".omega" / "sessions"
        self.directory = base / project_key(Path(project_root))

        #: The id of the last entry written per session, so the next one can point
        #: at it. Populated lazily on append, because a resumed session was
        #: written by a different process and this store has never seen its tail.
        self._last_entry: dict[str, str | None] = {}

    def path_for(self, session_id: str) -> Path:
        return self.directory / f"{session_id}.jsonl"

    def create_session(self, *, model: str) -> str:
        # Timestamp-first so the directory sorts chronologically for a human
        # reading it with `ls`; the random suffix keeps two sessions started in
        # the same second apart.
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        session_id = f"{stamp}-{uuid.uuid4().hex[:6]}"

        append_record(
            self.path_for(session_id),
            SessionHeader(
                session_id=session_id,
                created_at=datetime.now(UTC).isoformat(),
                model=model,
            ),
        )
        self._last_entry[session_id] = None
        return session_id

    def append(self, session_id: str, message: AgentMessage) -> None:
        entry = SessionEntry(
            id=uuid.uuid4().hex[:12],
            parent_id=self._parent_for(session_id),
            message=message,
        )
        append_record(self.path_for(session_id), entry)
        self._last_entry[session_id] = entry.id

    def entries(self, session_id: str) -> list[SessionEntry]:
        """Every entry in the file, in the order it was written.

        The raw material for `tree.py`. Separate from `load` because a caller
        picking a branch needs ids and parents, and a caller resuming one needs
        only messages — handing out entries for both would push the tree shape
        into places that have no business knowing it.
        """
        return [
            record
            for record in read_records(self.path_for(session_id))
            if isinstance(record, SessionEntry)
        ]

    def load(self, session_id: str, *, branch: str | None = None) -> list[AgentMessage]:
        """The messages of one branch, root first.

        **This used to return the file in order, and that was a latent bug.**
        While every session was a straight line the two agree exactly, which is
        why it survived a tier. Once one parent has two children, file order
        returns both attempts concatenated — a conversation that never happened.

        `branch` names the leaf to load. Omitted, it takes the **last** leaf,
        which for a linear file is the only one and for a branched file is the
        most recently written — "carry on where I left off".
        """
        entries = self.entries(session_id)
        if not entries:
            return []

        tip = branch
        if tip is None:
            ends = leaves(entries)
            if not ends:
                # Only reachable if every entry is claimed as a parent, which
                # means a cycle. `path_to` reports it properly; guessing a tip
                # here would hide it.
                raise CycleInTranscript(f"session {session_id!r} has no leaf entry")
            tip = ends[-1].id

        return [entry.message for entry in path_to(entries, tip)]

    def latest_session_id(self) -> str | None:
        if not self.directory.exists():
            return None
        files = list(self.directory.glob("*.jsonl"))
        if not files:
            return None
        # Nanosecond mtime, because two sessions in the same second are common
        # and a whole-second comparison would pick arbitrarily between them.
        return max(files, key=lambda path: path.stat().st_mtime_ns).stem

    def list_sessions(self) -> list[SessionInfo]:
        """Every saved session for this project, newest first.

        **This reads every file**, which is the honest cost of having no index.
        Pi and Tau both keep an `index.jsonl` precisely so listing does not mean
        parsing transcripts; omega does not, because one `--sessions` command run
        occasionally is not the same pressure as a live session picker. If a TUI
        arrives at Tier 3 and this gets called on every keystroke, the index is
        the fix — and it is an addition, not a format change.
        """
        if not self.directory.exists():
            return []

        rows: list[SessionInfo] = []
        for path in self.directory.glob("*.jsonl"):
            records = read_records(path)
            entries = [r for r in records if isinstance(r, SessionEntry)]

            # The first thing you typed is what makes a session recognisable.
            # An id and a timestamp tell you nothing about which one this was.
            first_prompt = ""
            for entry in entries:
                if entry.message.role == "user":
                    first_prompt = " ".join(str(entry.message.content).split())[:60]
                    break

            rows.append(
                SessionInfo(
                    session_id=path.stem,
                    modified=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                    messages=len(entries),
                    first_prompt=first_prompt,
                )
            )

        return sorted(rows, key=lambda row: row.modified, reverse=True)

    def branch_from(self, session_id: str, entry_id: str | None) -> None:
        """Hang the next appended entry off `entry_id` instead of the tail.

        The **whole write side of branching**, and it is one line of state —
        which is what `parent_id` shipping in Tier 2 bought. Nothing is deleted
        and nothing is rewritten: the abandoned entries stay exactly where they
        are, and the file stays append-only, which is the property that makes a
        crash mid-turn survivable.

        `None` branches from the root, i.e. starts the conversation over while
        keeping the old one readable.
        """
        self._last_entry[session_id] = entry_id

    def _parent_for(self, session_id: str) -> str | None:
        if session_id in self._last_entry:
            return self._last_entry[session_id]

        # Never seen this session: read its tail to find where to hang the next
        # entry. Happens exactly once per resumed session.
        records = read_records(self.path_for(session_id))
        entries = [r for r in records if isinstance(r, SessionEntry)]
        last = entries[-1].id if entries else None
        self._last_entry[session_id] = last
        return last
