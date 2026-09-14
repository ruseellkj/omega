"""Session persistence and resume.

Tier 1 lost everything when the terminal closed. That is the gap this closes —
but the interesting part is not writing JSON, it is the three decisions that
make a stored transcript still usable a week later.

**Append-only.** Rewriting a file means a crash mid-write loses the whole
session, not the last line. Appending means a crash loses at most a partial final
line — which the reader is expected to survive, and there is a test for it.

**Migrate on read.** The schema will change. Refusing to load old files makes
every format change a data loss event; migrating on read makes it a function.
The mechanism ships now, with one real migration in it, because retrofitting
versioning onto files already on disk is not possible.

**`parent_id` on every entry, even though branching is Tier 3.**
`anatomy.md:314`: "Retrofitting a tree onto a list is a rewrite."

And the one that ties Step 2 to Step 5: **resume repairs orphans first.** An
interrupted session is exactly the session you want to resume, and it is exactly
the one that is permanently invalid until repaired.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omega_agent.harness import Harness
from omega_agent.session import (
    SCHEMA_VERSION,
    JsonlSessionStore,
    SessionEntry,
    project_key,
    read_records,
)
from omega_agent.tools import Tool, ToolResult
from omega_agent.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from omega_ai.fake import FakeProvider, text_turn, tool_turn


async def _ok(arguments: dict[str, Any], signal: Any) -> ToolResult:
    return ToolResult(content="ran")


OK_TOOL = Tool(name="ok", description="works", parameters={"type": "object"}, execute=_ok)


def _harness(store: JsonlSessionStore, streams: list[list[Any]], **kwargs: Any) -> Harness:
    return Harness(
        provider=FakeProvider(streams),
        model="test-model",
        system="be helpful",
        tools=[OK_TOOL],
        store=store,
        **kwargs,
    )


# ------------------------------------------------------------------- the basics


async def test_a_run_is_written_to_disk(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    harness = _harness(store, [text_turn("hello")])

    async for _event in harness.run("go"):
        pass

    assert harness.session_id is not None
    restored = store.load(harness.session_id)
    assert [m.role for m in restored] == ["user", "assistant"]


async def test_a_session_survives_a_new_process(tmp_path: Path) -> None:
    """The whole point. Two stores, two harnesses, one file on disk."""
    first = _harness(JsonlSessionStore(tmp_path, home=tmp_path), [text_turn("remembered")])
    async for _event in first.run("remember this"):
        pass
    session_id = first.session_id
    assert session_id is not None

    # Nothing shared but the directory.
    second = _harness(JsonlSessionStore(tmp_path, home=tmp_path), [text_turn("still here")])
    restored = second.resume(session_id)

    assert restored == 2
    assert [m.role for m in second.messages] == ["user", "assistant"]
    first_message = second.messages[0]
    assert isinstance(first_message, UserMessage)
    assert first_message.content == "remember this"


async def test_resuming_continues_the_same_conversation(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    first = _harness(store, [text_turn("one")])
    async for _event in first.run("first"):
        pass
    session_id = first.session_id
    assert session_id is not None

    second = _harness(JsonlSessionStore(tmp_path, home=tmp_path), [text_turn("two")])
    second.resume(session_id)
    async for _event in second.run("second"):
        pass

    assert [m.role for m in second.messages] == ["user", "assistant", "user", "assistant"]

    # And it is all in one file, not two.
    assert len(JsonlSessionStore(tmp_path, home=tmp_path).load(session_id)) == 4


async def test_tool_results_are_persisted_too(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    harness = _harness(store, [tool_turn("ok", {}), text_turn("done")])

    async for _event in harness.run("use the tool"):
        pass

    assert harness.session_id is not None
    roles = [m.role for m in store.load(harness.session_id)]
    assert roles == ["user", "assistant", "toolResult", "assistant"]


async def test_latest_finds_the_most_recent_session(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    assert store.latest_session_id() is None

    older = _harness(store, [text_turn("a")])
    async for _event in older.run("first"):
        pass

    newer = _harness(store, [text_turn("b")])
    async for _event in newer.run("second"):
        pass

    assert store.latest_session_id() == newer.session_id


# ------------------------------------------------------------------ the format


def test_every_entry_carries_a_parent_id(tmp_path: Path) -> None:
    """Branching is Tier 3; the field is here now because a list cannot grow one."""
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    session_id = store.create_session(model="m")
    store.append(session_id, UserMessage(content="one"))
    store.append(session_id, AssistantMessage(model="m", stop_reason="stop"))

    entries = [r for r in read_records(store.path_for(session_id)) if isinstance(r, SessionEntry)]

    assert entries[0].parent_id is None, "the first entry is a root"
    assert entries[1].parent_id == entries[0].id, "the second hangs off the first"
    assert len({e.id for e in entries}) == 2, "ids must be distinct"


def test_the_file_is_append_only(tmp_path: Path) -> None:
    """Existing bytes are never rewritten - a crash costs one line, not the file."""
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    session_id = store.create_session(model="m")
    store.append(session_id, UserMessage(content="one"))

    path = store.path_for(session_id)
    before = path.read_bytes()

    store.append(session_id, UserMessage(content="two"))
    after = path.read_bytes()

    assert after.startswith(before), "earlier bytes were rewritten"


def test_a_header_records_the_schema_version(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    session_id = store.create_session(model="claude-x")

    first = json.loads(store.path_for(session_id).read_text().splitlines()[0])
    assert first["kind"] == "header"
    assert first["version"] == SCHEMA_VERSION
    assert first["model"] == "claude-x"
    assert first["created_at"]


def test_all_content_block_kinds_round_trip(tmp_path: Path) -> None:
    """Thinking signatures must survive verbatim or multi-turn reasoning breaks."""
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    session_id = store.create_session(model="m")
    original = AssistantMessage(
        model="m",
        stop_reason="toolUse",
        content=[
            TextContent(text="thinking about it"),
            ToolCall(id="c1", name="ok", arguments={"deep": {"nested": [1, 2]}}),
        ],
    )
    store.append(session_id, original)
    store.append(session_id, ToolResultMessage(tool_call_id="c1", tool_name="ok", content="fine"))

    restored = store.load(session_id)
    assert restored[0] == original
    assert isinstance(restored[1], ToolResultMessage)


# ----------------------------------------------------------------- robustness


def test_a_truncated_final_line_is_survived(tmp_path: Path) -> None:
    """Exactly what a crash mid-append leaves behind.

    Refusing to load the file would mean a crash destroys the session it was
    supposed to protect.
    """
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    session_id = store.create_session(model="m")
    store.append(session_id, UserMessage(content="kept"))

    path = store.path_for(session_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "entry", "id": "half-writ')

    restored = store.load(session_id)
    assert len(restored) == 1
    assert isinstance(restored[0], UserMessage)


def test_an_unreadable_record_is_skipped_not_fatal(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    session_id = store.create_session(model="m")
    path = store.path_for(session_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            '{"kind": "entry", "id": "x", "parent_id": null, '
            '"message": {"role": "nope"}}\n'
        )
    store.append(session_id, UserMessage(content="after the bad one"))

    restored = store.load(session_id)
    assert len(restored) == 1, "the good record after the bad one must still load"


def test_an_older_record_is_migrated_on_read(tmp_path: Path) -> None:
    """The mechanism has to exist before the schema changes, not after.

    v0 had no `kind` discriminator. Files like that are readable because the
    reader migrates rather than rejecting.
    """
    path = tmp_path / "legacy.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "e1",
                "parent_id": None,
                "message": {"role": "user", "content": "written by an older omega"},
            }
        )
        + "\n"
    )

    records = read_records(path)
    entries = [r for r in records if isinstance(r, SessionEntry)]
    assert len(entries) == 1
    assert entries[0].version == SCHEMA_VERSION
    message = entries[0].message
    assert isinstance(message, UserMessage)
    assert message.content == "written by an older omega"


def test_loading_a_missing_session_is_empty_not_an_error(tmp_path: Path) -> None:
    assert JsonlSessionStore(tmp_path, home=tmp_path).load("does-not-exist") == []


# --------------------------------------------------- Step 2 meets Step 5


async def test_resume_repairs_an_interrupted_session(tmp_path: Path) -> None:
    """The case that makes persistence dangerous without Step 2.

    An interrupted session is precisely the one you want to resume, and it is
    precisely the one carrying an unanswered tool call - which providers reject
    on every future request. Persisting that without repairing it would write a
    file that can never be used.
    """
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    session_id = store.create_session(model="m")
    store.append(session_id, UserMessage(content="do it"))
    store.append(
        session_id,
        AssistantMessage(
            model="m",
            stop_reason="toolUse",
            content=[ToolCall(id="orphan", name="ok", arguments={})],
        ),
    )

    harness = _harness(JsonlSessionStore(tmp_path, home=tmp_path), [text_turn("carrying on")])
    harness.resume(session_id)

    results = [m for m in harness.messages if isinstance(m, ToolResultMessage)]
    assert [r.tool_call_id for r in results] == ["orphan"]
    assert results[0].is_error is True


async def test_the_repair_is_persisted_so_it_only_happens_once(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    session_id = store.create_session(model="m")
    store.append(session_id, UserMessage(content="do it"))
    store.append(
        session_id,
        AssistantMessage(
            model="m",
            stop_reason="toolUse",
            content=[ToolCall(id="orphan", name="ok", arguments={})],
        ),
    )

    harness = _harness(JsonlSessionStore(tmp_path, home=tmp_path), [text_turn("ok")])
    harness.resume(session_id)
    async for _event in harness.run("continue"):
        pass

    reread = JsonlSessionStore(tmp_path, home=tmp_path).load(session_id)
    roles = [m.role for m in reread]
    assert roles == ["user", "assistant", "toolResult", "user", "assistant"]


async def test_the_provider_gets_a_valid_transcript_after_resume(tmp_path: Path) -> None:
    """The assertion that matters: the resumed request would not 400."""
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    session_id = store.create_session(model="m")
    store.append(session_id, UserMessage(content="do it"))
    store.append(
        session_id,
        AssistantMessage(
            model="m",
            stop_reason="toolUse",
            content=[ToolCall(id="orphan", name="ok", arguments={})],
        ),
    )

    provider = FakeProvider([text_turn("fine")])
    harness = Harness(
        provider=provider,
        model="m",
        system="s",
        tools=[OK_TOOL],
        store=JsonlSessionStore(tmp_path, home=tmp_path),
    )
    harness.resume(session_id)
    async for _event in harness.run("continue"):
        pass

    sent = provider.calls[0].messages
    asked = {b.id for m in sent if isinstance(m, AssistantMessage) for b in m.tool_calls}
    answered = {m.tool_call_id for m in sent if isinstance(m, ToolResultMessage)}
    assert asked <= answered


# ------------------------------------------------------------------- optional


async def test_no_store_means_no_files(tmp_path: Path) -> None:
    """Persistence is opt-in; the loop and harness work without it."""
    harness = Harness(
        provider=FakeProvider([text_turn("hi")]),
        model="m",
        system="s",
        tools=[OK_TOOL],
    )

    async for _event in harness.run("go"):
        pass

    assert harness.session_id is None
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------ where sessions live (Tier 2.5)


def test_sessions_live_under_home_not_inside_the_project(tmp_path: Path) -> None:
    """Moved out of the project, for the reason `git clean` exists.

    Tier 2 wrote to `<project>/.omega/sessions/`. An untracked directory inside a
    repo is exactly what `git clean -xdf` removes, and transcripts carry whatever
    you pasted into the agent. All three references store under the home
    directory instead.
    """
    project = tmp_path / "code" / "myapp"
    project.mkdir(parents=True)
    home = tmp_path / "home"

    store = JsonlSessionStore(project, home=home)

    assert home in store.directory.parents, "must be under home"
    assert project not in store.directory.parents, "must not be inside the project"
    assert store.directory.parent == home / ".omega" / "sessions"


def test_two_projects_with_the_same_name_do_not_collide(tmp_path: Path) -> None:
    """The hash half of the key, doing the job the slug cannot.

    `~/code/api` and `~/work/api` are different projects with the same folder
    name. A slug alone would merge their histories.
    """
    home = tmp_path / "home"
    one = tmp_path / "code" / "api"
    two = tmp_path / "work" / "api"
    one.mkdir(parents=True)
    two.mkdir(parents=True)

    assert JsonlSessionStore(one, home=home).directory != JsonlSessionStore(
        two, home=home
    ).directory


def test_the_key_is_readable_and_stable(tmp_path: Path) -> None:
    """The slug half: `ls ~/.omega/sessions/` should be legible."""
    project = tmp_path / "code" / "myapp"
    project.mkdir(parents=True)

    key = project_key(project)

    assert "myapp" in key, "a human should recognise the project"
    assert key == project_key(project), "same path, same key, every time"


def test_the_key_follows_symlinks_to_one_history(tmp_path: Path) -> None:
    """Two routes to one project must not produce two separate histories."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    assert project_key(link) == project_key(real)


# ------------------------------------------------------------ listing sessions


async def test_list_sessions_is_newest_first_and_names_the_first_prompt(
    tmp_path: Path,
) -> None:
    """What `--sessions` shows. The first prompt is what makes a row recognisable.

    An id and a timestamp tell you nothing about which session was which.
    """
    store = JsonlSessionStore(tmp_path, home=tmp_path)

    first = _harness(store, [text_turn("one")])
    async for _ in first.run("older question"):
        pass
    second = _harness(store, [text_turn("two")])
    async for _ in second.run("newer question"):
        pass

    rows = store.list_sessions()

    assert len(rows) == 2
    assert rows[0].modified >= rows[1].modified, "newest first"
    assert {row.first_prompt for row in rows} == {"older question", "newer question"}
    assert all(row.messages > 0 for row in rows)


def test_listing_an_unused_project_is_empty_not_an_error(tmp_path: Path) -> None:
    assert JsonlSessionStore(tmp_path, home=tmp_path).list_sessions() == []
