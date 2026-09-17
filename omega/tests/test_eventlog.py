"""The structured log — Tier 3's second listener.

Two things are being checked, and only one is about logging.

The first is that `add_listener` really was a seam rather than a claim. It has
carried exactly one subscriber since Tier 2, so "a second listener, not a new
mechanism" was untested. Adding one without touching the harness is the test.

The second matters more. Listeners are handed events, and events carry the raw
provider `stream_event` beside the assembled message. That raw field holds
*deltas*, and a credential split across two streamed chunks matches no pattern in
either half — so it cannot be masked by `before_record` or by anything else. A
logger that dumped whole events would therefore put on disk exactly what the
redaction work exists to keep off it. **The test that matters here is the one
proving the log never writes a delta.**
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from omega_agent.harness import Harness
from omega_agent.hooks import AgentHooks
from omega_ai.fake import FakeProvider, text_turn, tool_turn
from omega_coding.builtin_tools import build_tools
from omega_coding.eventlog import EventLog, sweep_old_logs
from omega_coding.redact import redact_message, redacting_hook

#: Correctly shaped so the patterns match it, and not a real credential.
KEY = "sk-ant-" + "C" * 40


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def _run(tmp_path: Path, provider: FakeProvider, prompt: str = "go") -> Path:
    log = tmp_path / "logs" / "session.jsonl"
    harness = Harness(
        provider=provider,
        model="m",
        system="s",
        tools=build_tools(tmp_path),
        hooks=AgentHooks(after_tool_call=redacting_hook, before_record=redact_message),
    )
    harness.add_listener(EventLog(log))
    async for _ in harness.run(prompt):
        pass
    return log


# ------------------------------------------------------------- it records work


async def test_a_turn_is_written_as_json_lines(tmp_path: Path) -> None:
    log = await _run(tmp_path, FakeProvider([text_turn("done")]))

    records = _lines(log)
    assert records, "something was written"
    assert all("at" in r and "event" in r for r in records)
    assert {r["event"] for r in records} >= {"agent_start", "message_end", "agent_end"}


async def test_a_tool_call_records_its_name_and_arguments(tmp_path: Path) -> None:
    """The question a log exists to answer is "what did it actually run"."""
    (tmp_path / "a.txt").write_text("hello\n")
    log = await _run(
        tmp_path,
        FakeProvider([tool_turn("read_file", {"path": "a.txt"}), text_turn("read it")]),
    )

    calls = [r for r in _lines(log) if r["event"] == "tool_execution_start"]
    assert calls, "the tool call was recorded"
    assert calls[0]["tool"] == "read_file"
    assert calls[0]["arguments"] == {"path": "a.txt"}


async def test_token_usage_is_recorded(tmp_path: Path) -> None:
    log = await _run(tmp_path, FakeProvider([text_turn("done")]))

    ends = [r for r in _lines(log) if r["event"] == "message_end"]
    usage = ends[0]["usage"]
    assert isinstance(usage, dict)
    assert set(usage) == {"input", "output", "cache_write", "cache_read"}


# ------------------------------------------------- the property that matters


async def test_the_event_log_never_writes_a_delta(tmp_path: Path) -> None:
    """**The reason this module filters rather than dumps.**

    `message_update` fires once per streamed chunk and carries the raw provider
    event. Writing those would mean writing text that no redaction pass can mask,
    because a key split across two chunks is not a key in either half.
    """
    log = await _run(tmp_path, FakeProvider([text_turn("hello there")]))

    kinds = {r["event"] for r in _lines(log)}
    assert "message_update" not in kinds
    assert "stream_event" not in log.read_text()


async def test_a_key_in_the_models_answer_never_reaches_the_log(tmp_path: Path) -> None:
    """End to end: the model says a key out loud, and the file on disk is clean."""
    log = await _run(tmp_path, FakeProvider([text_turn(f"your key is {KEY}")]))

    assert KEY not in log.read_text()
    assert "[redacted Anthropic API key]" in log.read_text()


async def test_a_key_in_tool_output_never_reaches_the_log(tmp_path: Path) -> None:
    """The other direction: reading a dotenv file is one ordinary turn away."""
    (tmp_path / ".env").write_text(f"ANTHROPIC_API_KEY={KEY}\n")
    log = await _run(
        tmp_path,
        FakeProvider([tool_turn("read_file", {"path": ".env"}), text_turn("read it")]),
    )

    assert KEY not in log.read_text()


# --------------------------------------------------------- it stays out of the way


async def test_an_unwritable_path_does_not_end_the_session(tmp_path: Path) -> None:
    """Losing a log line is never worth losing the turn that produced it.

    `AgentListener` returns nothing and cannot report failure, so the only safe
    behaviour is to swallow the error and carry on — recorded on the object so a
    caller can mention it once instead of per event.
    """
    blocker = tmp_path / "blocked"
    blocker.write_text("I am a file, not a directory")
    log = EventLog(blocker / "nested" / "session.jsonl")

    harness = Harness(provider=FakeProvider([text_turn("ok")]), model="m", system="s", tools=[])
    harness.add_listener(log)

    async for _ in harness.run("go"):
        pass

    assert log.failed is True
    assert log.written == 0
    assert harness.messages, "the turn completed anyway"


def test_old_logs_are_swept(tmp_path: Path) -> None:
    """Swept at startup, like the truncation spill files: the run that leaves one
    behind is the run that crashed."""
    old = tmp_path / "old.jsonl"
    current = tmp_path / "new.jsonl"
    old.write_text("{}\n")
    current.write_text("{}\n")
    stale = time.time() - 30 * 86_400
    os.utime(old, (stale, stale))

    assert sweep_old_logs(tmp_path, max_age_days=7) == 1
    assert not old.exists()
    assert current.exists(), "a current log is left alone"


def test_sweeping_a_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    assert sweep_old_logs(tmp_path / "never-created") == 0


async def test_the_log_is_named_after_the_session(tmp_path: Path) -> None:
    """**Found by running it, not by reading it.**

    `session_id` is None until the first turn creates it, so a path bound at
    construction named every session `unsaved.jsonl` — and every session on the
    machine appended to that one file. Resolving per write fixes it.
    """
    from omega_agent.session import JsonlSessionStore

    logs = tmp_path / "logs"
    store = JsonlSessionStore(tmp_path, home=tmp_path)
    harness = Harness(
        provider=FakeProvider([text_turn("ok")]), model="m", system="s", tools=[], store=store
    )
    harness.add_listener(EventLog(lambda: logs / f"{harness.session_id or 'unsaved'}.jsonl"))

    async for _ in harness.run("go"):
        pass

    assert harness.session_id is not None
    written = sorted(p.name for p in logs.glob("*.jsonl"))
    assert written == [f"{harness.session_id}.jsonl"]
    assert "unsaved.jsonl" not in written
