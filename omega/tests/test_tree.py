"""Session branching — the bet Tier 2 placed and never collected.

`entries.py` has written `parent_id` on every entry since Tier 2, unread by
anything, on one stated argument (`anatomy.md:314`):

> "Retrofitting a tree onto a list is a rewrite."

So the field shipped early to make branching an addition rather than a migration.
**This file is where that claim gets tested**, and the test is specific: no record
shape changes, and no file already on disk becomes unreadable.

## The bug that makes this necessary rather than decorative

`store.load()` returns every entry in **file order**, ignoring `parent_id`
completely. That is indistinguishable from correct while a session is a straight
line. Write two children of one parent and it silently returns both branches
concatenated — a conversation that never happened, containing two answers to the
same question.

So branching is not "add a feature". It is "`load` has been reading the file the
wrong way and nothing noticed, because nothing had ever branched".

The compatibility claim, stated so it can fail: for a linear session the path
from the root to the only leaf **is** the whole file in order, so `load()` must
return what it always did.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omega_agent.session.entries import SessionEntry
from omega_agent.session.tree import CycleInTranscript, leaves, path_to
from omega_agent.types import UserMessage


def _entry(entry_id: str, parent: str | None, text: str) -> SessionEntry:
    return SessionEntry(id=entry_id, parent_id=parent, message=UserMessage(content=text))


def _linear() -> list[SessionEntry]:
    return [
        _entry("a", None, "first"),
        _entry("b", "a", "second"),
        _entry("c", "b", "third"),
    ]


def _forked() -> list[SessionEntry]:
    """Two children of `a`. `b` is the original, `d` the second attempt."""
    return [
        _entry("a", None, "shared opening"),
        _entry("b", "a", "original branch"),
        _entry("c", "b", "original continues"),
        _entry("d", "a", "second attempt"),
    ]


# ------------------------------------------------------------------- path_to


def test_a_linear_transcript_walks_back_to_the_root() -> None:
    assert [e.id for e in path_to(_linear(), "c")] == ["a", "b", "c"]


def test_a_branch_excludes_the_other_branch() -> None:
    """**The whole point.** Loading one attempt must not include the other.

    File order gives `a b c d`. The branch ending at `d` is `a d` — `b` and `c`
    belong to a different attempt and would be a conversation that never
    happened.
    """
    assert [e.id for e in path_to(_forked(), "d")] == ["a", "d"]
    assert [e.id for e in path_to(_forked(), "c")] == ["a", "b", "c"]


def test_the_root_alone_is_a_valid_path() -> None:
    assert [e.id for e in path_to(_linear(), "a")] == ["a"]


def test_an_unknown_id_has_no_path() -> None:
    assert path_to(_linear(), "nope") == []


def test_a_missing_parent_stops_the_walk_instead_of_failing() -> None:
    """A truncated file — killed mid-write — leaves an entry whose parent never
    landed. Returning the part that exists beats refusing to open the session."""
    orphaned = [_entry("b", "vanished", "second"), _entry("c", "b", "third")]

    assert [e.id for e in path_to(orphaned, "c")] == ["b", "c"]


def test_a_cycle_is_refused_rather_than_looped_forever() -> None:
    """Nothing omega writes can produce a cycle. A hand-edited or corrupted file
    can, and a `while parent is not None` walk over one never returns — the
    process hangs with no error, which is the worst of the three failures."""
    cyclic = [_entry("a", "b", "one"), _entry("b", "a", "two")]

    with pytest.raises(CycleInTranscript) as raised:
        path_to(cyclic, "a")

    assert "a" in str(raised.value)


# --------------------------------------------------------------------- leaves


def test_a_linear_transcript_has_exactly_one_leaf() -> None:
    assert [e.id for e in leaves(_linear())] == ["c"]


def test_a_fork_has_one_leaf_per_branch() -> None:
    assert sorted(e.id for e in leaves(_forked())) == ["c", "d"]


def test_leaves_are_returned_in_file_order() -> None:
    """So "the newest branch" is the last one, which is what a picker wants."""
    assert [e.id for e in leaves(_forked())] == ["c", "d"]


def test_an_empty_transcript_has_no_leaves() -> None:
    assert leaves([]) == []


# ------------------------------------------------- the compatibility promise


async def test_loading_a_linear_session_is_unchanged(tmp_path: Path) -> None:
    """**The claim Tier 2 made, tested.**

    If reading by tree changed what a straight-line session returns, then
    `parent_id` shipping early bought nothing and this *was* a rewrite. For a
    linear file the path to the only leaf is the whole file, so the two agree.
    """
    from omega_agent.harness import Harness
    from omega_agent.session import JsonlSessionStore
    from omega_ai.fake import FakeProvider, text_turn

    store = JsonlSessionStore(tmp_path, home=tmp_path)
    harness = Harness(
        provider=FakeProvider([text_turn("one"), text_turn("two")]),
        model="m",
        system="s",
        tools=[],
        store=store,
    )
    async for _ in harness.run("first question"):
        pass
    async for _ in harness.run("second question"):
        pass
    session = harness.session_id
    assert session is not None

    loaded = store.load(session)

    assert loaded == harness.messages, "tree-reading returns what linear reading did"
    assert len(leaves(store.entries(session))) == 1, "and it really is one line"


# ------------------------------------------------------ making a branch, for real


async def test_rewinding_forks_the_session_and_keeps_both_branches(tmp_path: Path) -> None:
    """**End to end, and the test the whole file is for.**

    Ask, rewind, ask differently. Afterwards the file holds *both* attempts and
    each loads without the other — which is the property file-order reading
    cannot give, and the one a user relies on when they go back and try again.
    """
    from omega_agent.harness import Harness
    from omega_agent.session import JsonlSessionStore
    from omega_ai.fake import FakeProvider, text_turn

    store = JsonlSessionStore(tmp_path, home=tmp_path)
    harness = Harness(
        provider=FakeProvider([text_turn("answer one"), text_turn("answer two")]),
        model="m",
        system="s",
        tools=[],
        store=store,
    )

    async for _ in harness.run("the original question"):
        pass
    session = harness.session_id
    assert session is not None
    original_tip = leaves(store.entries(session))[-1].id

    removed = harness.rewind()
    assert removed == 2, "one question and its answer"
    assert harness.messages == [], "the working transcript is back to empty"

    async for _ in harness.run("a different question"):
        pass

    entries = store.entries(session)
    tips = leaves(entries)
    assert len(tips) == 2, "the file now holds two attempts"

    original = [str(getattr(m, "content", "")) for m in store.load(session, branch=original_tip)]
    replacement = [str(getattr(m, "content", "")) for m in store.load(session)]

    assert any("the original question" in t for t in original)
    assert not any("a different question" in t for t in original), "branches do not leak"

    assert any("a different question" in t for t in replacement)
    assert not any("the original question" in t for t in replacement)


async def test_the_abandoned_branch_is_still_on_disk(tmp_path: Path) -> None:
    """Append-only is the point. A rewind that deleted the old attempt would
    make "go back and try again" a decision you cannot undo."""
    from omega_agent.harness import Harness
    from omega_agent.session import JsonlSessionStore
    from omega_ai.fake import FakeProvider, text_turn

    store = JsonlSessionStore(tmp_path, home=tmp_path)
    harness = Harness(
        provider=FakeProvider([text_turn("one"), text_turn("two")]),
        model="m",
        system="s",
        tools=[],
        store=store,
    )
    async for _ in harness.run("first"):
        pass
    session = harness.session_id
    assert session is not None
    before = len(store.entries(session))

    harness.rewind()
    async for _ in harness.run("second"):
        pass

    assert len(store.entries(session)) > before, "entries were added, never removed"
    raw = (store.directory / f"{session}.jsonl").read_text()
    assert "first" in raw, "the abandoned question is still in the file"


async def test_rewinding_without_a_store_still_works(tmp_path: Path) -> None:
    """`--no-save` has no entries to hang from. The working transcript must
    still rewind, or the feature would depend on persistence it does not need."""
    from omega_agent.harness import Harness
    from omega_ai.fake import FakeProvider, text_turn

    harness = Harness(
        provider=FakeProvider([text_turn("one")]), model="m", system="s", tools=[]
    )
    async for _ in harness.run("a question"):
        pass

    assert harness.rewind() == 2
    assert harness.messages == []


async def test_rewinding_further_than_the_start_stops_at_the_start(tmp_path: Path) -> None:
    from omega_agent.harness import Harness
    from omega_ai.fake import FakeProvider, text_turn

    harness = Harness(
        provider=FakeProvider([text_turn("one")]), model="m", system="s", tools=[]
    )
    async for _ in harness.run("only question"):
        pass

    harness.rewind(99)

    assert harness.messages == []


def test_rewinding_an_empty_transcript_is_a_no_op() -> None:
    from omega_agent.harness import Harness
    from omega_ai.fake import FakeProvider

    harness = Harness(provider=FakeProvider([]), model="m", system="s", tools=[])

    assert harness.rewind() == 0
