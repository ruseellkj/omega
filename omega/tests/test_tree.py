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
from typing import Any

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


# ------------------------------------ the file stays on the conversation you had


def _saving_harness(tmp_path: Path, answers: int) -> tuple[Any, Any]:
    from omega_agent.harness import Harness
    from omega_agent.session import JsonlSessionStore
    from omega_ai.fake import FakeProvider, text_turn

    store = JsonlSessionStore(tmp_path, home=tmp_path)
    harness = Harness(
        provider=FakeProvider([text_turn(f"a{n}") for n in range(1, answers + 1)]),
        model="m",
        system="s",
        tools=[],
        store=store,
    )
    return harness, store


async def _ask(harness: Any, *questions: str) -> None:
    for question in questions:
        async for _ in harness.run(question):
            pass


def _said(messages: list[Any]) -> list[str]:
    return [m.content if isinstance(m, UserMessage) else m.text for m in messages]


def test_path_is_the_conversation_the_next_append_continues(tmp_path: Path) -> None:
    """What `rewind` cuts. Not `entries()`, which is every branch in write order."""
    from omega_agent.session import JsonlSessionStore

    store = JsonlSessionStore(tmp_path, home=tmp_path)
    session = store.create_session(model="m")
    for text in ("a", "b", "c"):
        store.append(session, UserMessage(content=text))
    assert [e.message.content for e in store.path(session)] == ["a", "b", "c"]

    store.branch_from(session, store.entries(session)[0].id)
    assert [e.message.content for e in store.path(session)] == ["a"]

    store.branch_from(session, None)
    assert store.path(session) == []

    # Another process has never seen this session, and continues from its tail.
    fresh = JsonlSessionStore(tmp_path, home=tmp_path)
    assert [e.message.content for e in fresh.path(session)] == ["a", "b", "c"]


async def test_a_second_rewind_keeps_the_file_on_the_conversation_you_had(
    tmp_path: Path,
) -> None:
    """**Found by running it.** `rewind` chose the new parent by position in
    `store.entries()`, which is the whole file in write order, abandoned branches
    included. After one rewind the file holds more than the conversation, so the
    second rewind picked an entry off the abandoned branch. Memory said
    q1 q3 q5, and the file, which is what `--resume` loads, said q1 q2 q5."""
    harness, store = _saving_harness(tmp_path, 5)
    await _ask(harness, "q1", "q2")
    harness.rewind()
    await _ask(harness, "q3", "q4")
    harness.rewind()
    await _ask(harness, "q5")

    expected = ["q1", "a1", "q3", "a3", "q5", "a5"]
    assert _said(harness.messages) == expected
    assert _said(store.load(harness.session_id)) == expected, "the file went its own way"


async def test_rewinding_after_compaction_keeps_the_file_on_the_conversation_you_had(
    tmp_path: Path,
) -> None:
    """**The same bug, through compaction.** `replace_transcript` shrinks the
    working transcript and writes nothing, so positions in memory stop matching
    positions in the file. Measured before the fix: the answer after a rewind
    was hung off q2, and the file lost q3 to q6."""
    harness, store = _saving_harness(tmp_path, 8)
    await _ask(harness, "q1", "q2", "q3", "q4", "q5", "q6")
    # The shape `Compactor.compact_to` returns: the first turn, one note standing
    # in for the middle, then the newest turn.
    note = UserMessage(content="[compacted] 4 earlier turns were removed")
    harness.replace_transcript([*harness.messages[:2], note, *harness.messages[-2:]])
    await _ask(harness, "q7")

    harness.rewind()
    await _ask(harness, "q8")

    expected = [f"{kind}{n}" for n in (1, 2, 3, 4, 5, 6, 8) for kind in ("q", "a")]
    assert _said(store.load(harness.session_id)) == expected, "the file went its own way"
    assert _said(harness.messages) == expected, "and memory continues what is saved"


async def test_after_compaction_rewind_takes_back_your_question_not_the_note(
    tmp_path: Path,
) -> None:
    """Compaction keeps whole *turns*, so the newest thing kept can be an answer
    whose question was folded into the note. Rewind then counted the note as
    the last question and went back five questions, not one."""
    harness, store = _saving_harness(tmp_path, 6)
    await _ask(harness, "q1", "q2", "q3", "q4", "q5", "q6")
    note = UserMessage(content="[compacted] 5 earlier turns were removed")
    harness.replace_transcript([*harness.messages[:2], note, harness.messages[-1]])

    assert harness.rewind() == 2, "one question and its answer"

    expected = [f"{kind}{n}" for n in range(1, 6) for kind in ("q", "a")]
    assert _said(harness.messages) == expected


async def test_resuming_after_an_unanswered_rewind_saves_what_it_loaded(tmp_path: Path) -> None:
    """**Found in review, then measured.** A rewind with nothing asked after it
    moves only the store's in-memory pointer. `resume` loads the newest leaf,
    so in the same process memory said q1 a1 q2 a2 q3 while the file hung q3
    off a1. The terminal UI keeps one store for its whole life, so `/rewind`
    then `/resume` reached this. A fresh `--resume` of the same file loads the
    newest leaf, and resuming in-process now means the same."""
    harness, store = _saving_harness(tmp_path, 3)
    await _ask(harness, "q1", "q2")
    session = harness.session_id
    harness.rewind()

    harness.resume(session)
    await _ask(harness, "q3")

    expected = ["q1", "a1", "q2", "a2", "q3", "a3"]
    assert _said(harness.messages) == expected
    assert _said(store.load(session)) == expected, "the file went its own way"


async def test_what_rewind_reloads_is_masked_before_it_is_sent(tmp_path: Path) -> None:
    """Rewind now reloads the conversation from the file, and a file written
    before `before_record` existed was never masked. So the reload has to go
    back through the hook, as `resume`'s does, or a secret the working copy had
    already masked is sent to the model again."""
    from omega_agent.harness import Harness
    from omega_agent.hooks import AgentHooks
    from omega_agent.session import JsonlSessionStore
    from omega_ai.fake import FakeProvider, text_turn

    async def mask(message: Any) -> Any:
        if isinstance(message, UserMessage):
            return message.model_copy(
                update={"content": message.content.replace("sk-SECRET", "[REDACTED]")}
            )
        return message

    written, _ = _saving_harness(tmp_path, 2)
    await _ask(written, "my key is sk-SECRET", "q2")  # saved with no hook at all

    sent: list[str] = []

    class Recording:
        async def stream_response(self, **kwargs: Any) -> Any:
            sent.extend(_said(kwargs["messages"]))
            async for event in FakeProvider([text_turn("a3")]).stream_response(**kwargs):
                yield event

    harness = Harness(
        provider=Recording(),  # type: ignore[arg-type]
        model="m",
        system="s",
        tools=[],
        store=JsonlSessionStore(tmp_path, home=tmp_path),
        hooks=AgentHooks(before_record=mask),
    )
    harness.resume(written.session_id)
    harness.rewind()
    await _ask(harness, "q3")

    assert sent[0] == "my key is [REDACTED]"
    assert not any("sk-SECRET" in text for text in sent)


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
