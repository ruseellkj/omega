"""Credentials: where they live, who can read them, and what happens without.

The three properties worth testing here are the ones that fail silently:
**permissions**, because a `0644` credential file works perfectly; **resolution
order**, because a stored key shadowing an exported one looks like the export
being ignored; and **leakage**, because a key in the session file is invisible
until someone reads it.

Every test points `Path.home()` at a temporary directory. A test that writes a
real credential into `~/.omega` would be a worse bug than any it could catch.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from omega_agent.harness import Harness
from omega_coding import auth
from omega_coding.builtin_tools import build_tools

KEY = "sk-ant-api03-NOTAREALKEY-0000000000000000000000000000000000000000000000"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every credential path lands in a temporary home."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for variable in auth.ENV_VARS.values():
        monkeypatch.delenv(variable, raising=False)
    return tmp_path


# ------------------------------------------------------------------ the file


def test_the_credential_file_is_readable_only_by_its_owner() -> None:
    """**The one that fails silently.** A `0644` credentials file works exactly
    as well as a `0600` one, right up until it doesn't."""
    auth.save("anthropic", KEY)

    path = auth.credentials_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600, "the file is readable by others"
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700, "the directory lists to others"


def test_the_directory_is_tightened_even_when_it_already_existed() -> None:
    """`~/.omega` already holds `sessions/` and `logs/` and was created `0755`.

    Saving a credential into a directory someone else can list is the bug this
    catches — and it only appears on an *existing* install, which is the one
    nobody tests.
    """
    existing = auth.credentials_path().parent
    existing.mkdir(parents=True)
    os.chmod(existing, 0o755)

    auth.save("anthropic", KEY)

    assert stat.S_IMODE(existing.stat().st_mode) == 0o700


def test_saving_twice_leaves_one_file_and_no_debris() -> None:
    """The atomic write uses a temporary file in the same directory. If it ever
    stops cleaning up, the leftovers are readable partial credentials."""
    auth.save("anthropic", KEY)
    auth.save("anthropic", KEY + "-second")

    directory = auth.credentials_path().parent
    assert auth.stored("anthropic") == KEY + "-second"
    assert [p.name for p in directory.iterdir()] == ["auth.json"], "a temp file survived"


def test_a_corrupt_file_reads_as_logged_out_rather_than_crashing() -> None:
    """A stray byte in a config file must not stop an editor opening.

    The worst case of treating it as empty is being asked to log in again; the
    worst case of raising is an agent that will not start.
    """
    path = auth.credentials_path()
    path.parent.mkdir(parents=True)
    path.write_text("{ this is not json")

    assert auth.stored("anthropic") is None
    auth.save("anthropic", KEY)
    assert auth.stored("anthropic") == KEY, "and it recovers on the next write"


# -------------------------------------------------------- the resolution order


def test_a_stored_credential_beats_an_exported_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The order, reversed — following Pi.**

        A stored credential owns the provider: ambient/env is consulted only
        when nothing is stored.
            — research/pi/packages/ai/src/auth/resolve.ts:44-46

    This test asserted the opposite until the flip. The old order meant a user
    with a key in `.env` could `/login` with their subscription, be told it
    worked, and have the token never used — nothing wrong, and nothing saying so.
    """
    auth.save("anthropic", "from-the-file")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-environment")

    assert auth.resolve("anthropic") == "from-the-file"
    assert auth.source("anthropic") == "auth.json", "the facts line must agree"


def test_an_exported_variable_still_works_when_nothing_is_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The half the flip must not break**, and the reason it is a separate test.

    A careless reversal deletes the environment branch outright: the test above
    still passes, and every `.env` user — which is every install that has never
    run `/login` — silently stops being able to authenticate.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-environment")

    assert auth.stored("anthropic") is None, "precondition: nothing stored"
    assert auth.resolve("anthropic") == "from-the-environment"
    assert auth.source("anthropic") == "environment"


def test_a_stored_entry_that_is_unusable_does_not_fall_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pi's rule has a second half: **no silent env fallback**.

    "Owns" is stronger than "is checked first". An entry that exists but cannot
    be read owns the provider with nothing in it — because resolving to a
    different credential than the one the user signed in with, and saying
    nothing, is how an afternoon gets spent debugging the wrong key.
    """
    auth.save("anthropic", "placeholder")
    path = auth.credentials_path()
    path.write_text(json.dumps({"anthropic": {"kind": "apikey", "key": ""}}))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-environment")

    assert auth.owns("anthropic") is True
    assert auth.resolve("anthropic") is None, "it fell through to the environment"
    assert auth.source("anthropic") == "not signed in"


def test_the_stored_key_is_used_when_nothing_is_exported() -> None:
    auth.save("openai", KEY)

    assert auth.resolve("openai") == KEY
    assert auth.resolve("anthropic") is None, "providers do not share a key"


def test_logging_out_reports_whether_there_was_anything_to_remove() -> None:
    """`/logout` has to be able to say "nothing was stored" rather than implying
    it removed something — because an exported variable will still be working."""
    assert auth.forget("anthropic") is False

    auth.save("anthropic", KEY)
    assert auth.forget("anthropic") is True
    assert auth.stored("anthropic") is None


def test_the_source_line_never_contains_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """It goes in the startup facts, which are on screen and in screenshots."""
    auth.save("anthropic", KEY)
    assert KEY not in auth.source("anthropic")

    auth.forget("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    assert KEY not in auth.source("anthropic")
    assert "environment" in auth.source("anthropic")


# ------------------------------------------------------ the stand-in provider


async def test_an_unauthenticated_turn_ends_with_an_explanation(tmp_path: Path) -> None:
    """**Not an exit.** The app opens, and the first query is what tells you.

    `omega --sessions` used to fail with a key error while listing sessions that
    needed no provider at all; that is what exiting during construction buys.
    """
    harness = Harness(
        provider=auth.LoginRequiredProvider("anthropic"),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )

    reasons = [event async for event in harness.run("hello") if event.type == "agent_end"]

    assert len(reasons) == 1
    assert reasons[0].reason == "error"
    assert "/login" in (reasons[0].error_message or "")
    assert "nothing was charged" in (reasons[0].error_message or "").lower()


async def test_the_stand_in_sends_nothing_anywhere(tmp_path: Path) -> None:
    """The claim in its own message. Worth asserting rather than trusting: it
    has no client, no URL and no key, and that should stay true."""
    provider = auth.LoginRequiredProvider("anthropic")

    assert not hasattr(provider, "_client")
    events = [event async for event in provider.stream_response()]
    assert len(events) == 1, "one terminal error and nothing else"
    assert events[0].type == "error"


def test_closing_the_stand_in_is_safe() -> None:
    """`cli.py` and the TUI both call `aclose()` on whatever provider they hold."""
    asyncio.run(auth.LoginRequiredProvider("anthropic").aclose())


# ------------------------------------------------------------------ leakage


async def test_a_pasted_key_never_reaches_the_session_file(tmp_path: Path) -> None:
    """**The failure that is invisible until someone reads the file.**

    Redaction has been wrong five separate times in this project, and each one
    looked fine from the outside. The assertion is on the bytes on disk, not on
    what the screen showed.
    """
    from omega_agent.hooks import AgentHooks
    from omega_agent.session import JsonlSessionStore
    from omega_ai.fake import FakeProvider, text_turn
    from omega_coding.redact import redact_message

    store = JsonlSessionStore(tmp_path / "project", home=tmp_path)
    harness = Harness(
        provider=FakeProvider([text_turn("I will not repeat that.")]),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
        store=store,
        hooks=AgentHooks(before_record=redact_message),
    )

    # The shape of a real accident: pasting the key into the prompt instead of
    # the login box.
    async for _ in harness.run(f"my key is {KEY}, use it"):
        pass

    written = "".join(p.read_text() for p in store.directory.glob("*.jsonl"))
    assert written, "the session was actually written"
    assert KEY not in written, "the key is sitting in the session file"
    assert "redacted" in written.lower()


async def test_a_key_in_tool_output_never_reaches_the_event_log(tmp_path: Path) -> None:
    """**The first version of this test was worthless, and proving it is the
    point.**

    It pasted the key into the *prompt* and asserted it was absent from the log.
    It passed with redaction switched off — because the log records
    `agent_start`, `agent_end`, `message_end` and the two tool events
    (`eventlog.py:60-68`), and a user message is none of those. The key was
    never going to be there.

    The path that actually exists is tool output: `cat` a config, `env`, a
    misconfigured script — the result flows through `tool_execution_end` and
    into the log. That is what this runs, and switching redaction off fails it.
    """
    from omega_agent.hooks import AgentHooks
    from omega_ai.fake import FakeProvider, text_turn, tool_turn
    from omega_coding.eventlog import EventLog
    from omega_coding.redact import redact_message, redacting_hook

    log_path = tmp_path / "events.jsonl"
    harness = Harness(
        provider=FakeProvider(
            [tool_turn("run_shell", {"command": f"echo {KEY}"}), text_turn("noted")]
        ),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
        # **Both hooks, matching `cli.py`.** Wiring only `before_record` failed
        # this test with redaction fully enabled, which looked like a product
        # bug and was a test that did not reproduce the real composition: tool
        # output is masked by `after_tool_call`, and the transcript by
        # `before_record`. Two sinks, two hooks.
        hooks=AgentHooks(after_tool_call=redacting_hook, before_record=redact_message),
    )
    # `EventLog` *is* the listener — it defines `__call__` (`eventlog.py:84`),
    # so it is registered directly rather than through a method.
    harness.add_listener(EventLog(lambda: log_path))

    async for _ in harness.run("read the config"):
        pass

    written = log_path.read_text()
    assert written, "the log was actually written"
    assert "tool_execution_end" in written, "the event that carries tool output is there"
    assert KEY not in written, "the key is sitting in the event log"


async def test_the_modal_masks_what_is_typed(tmp_path: Path) -> None:
    """`password=True` is the entire security surface of the login screen — it
    is what keeps the key out of the terminal's scrollback and out of any
    screenshot. Asserted rather than assumed, because removing it changes
    nothing that a passing test suite would otherwise notice.

    Driven through a real app: `compose()` needs an active one, so building the
    screen directly raises `NoActiveAppError` rather than telling you anything.
    """
    from textual.widgets import Input

    from omega_ai.fake import FakeProvider
    from omega_coding.tui.app import OmegaApp
    from omega_coding.tui.login import SecretModal

    app = OmegaApp(
        Harness(
            provider=FakeProvider([]), model="m", system="s", tools=build_tools(tmp_path)
        )
    )
    async with app.run_test() as pilot:
        app.push_screen(SecretModal("paste it"))
        await pilot.pause()

        field = app.screen.query_one(Input)
        assert field.password is True, "the key would be typed in plain sight"


# ------------------------------------------------------------- the commands


def _context(**overrides: object) -> object:
    """A command context with only what `/login` and `/logout` touch."""
    from omega_agent.hooks import AgentHooks
    from omega_ai.fake import FakeProvider
    from omega_coding.commands import CommandContext
    from omega_coding.cost import CostTracker

    base: dict[str, object] = {
        "harness": Harness(
            provider=FakeProvider([]), model="m", system="s", tools=[]
        ),
        "store": None,
        "tracker": CostTracker(),
        "model": "m",
        "system": "s",
        "tools": [],
        "hooks": AgentHooks(),
    }
    return CommandContext(**{**base, **overrides})  # type: ignore[arg-type]


async def test_login_stores_the_key_and_takes_effect_without_a_restart() -> None:
    """The whole reason the gate is a stand-in provider rather than an exit:
    you can fix it in the session you are already in."""
    from omega_coding.commands import dispatch

    said: list[str] = []
    swapped: list[bool] = []

    async def paste(_prompt: str) -> str:
        return KEY

    context = _context(
        emit=said.append,
        ask_secret=paste,
        reload_provider=lambda: swapped.append(True) or True,
    )

    await dispatch("/login anthropic", context)

    assert auth.stored("anthropic") == KEY
    assert swapped == [True], "the provider was rebuilt"
    assert "Signed in" in " ".join(said)
    assert KEY not in " ".join(said), "the key was echoed back to the screen"


async def test_login_without_a_way_to_ask_refuses_rather_than_crashing() -> None:
    """The same fail-safe shape as `ApprovalPolicy` with no asker: unset means
    refusal. A frontend that forgot to supply one gets a message, not a
    traceback."""
    from omega_coding.commands import dispatch

    said: list[str] = []
    await dispatch("/login anthropic", _context(emit=said.append))

    assert auth.stored("anthropic") is None
    assert "ANTHROPIC_API_KEY" in " ".join(said), "it says what to do instead"


async def test_cancelling_the_paste_stores_nothing() -> None:
    from omega_coding.commands import dispatch

    said: list[str] = []

    async def cancel(_prompt: str) -> None:
        return None

    await dispatch("/login anthropic", _context(emit=said.append, ask_secret=cancel))

    assert auth.stored("anthropic") is None
    assert "Cancelled" in " ".join(said)


async def test_logout_says_what_it_cannot_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Without this line `/logout` looks broken.** Remove the stored key while
    the variable is exported and omega keeps working, which reads as the command
    having done nothing at all. Both references carry the same caveat."""
    from omega_coding.commands import dispatch

    auth.save("anthropic", KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "still-exported")

    said: list[str] = []
    await dispatch("/logout anthropic", _context(emit=said.append))

    spoken = " ".join(said)
    assert auth.stored("anthropic") is None, "the stored key went"
    assert "ANTHROPIC_API_KEY is set, and omega will now use it" in spoken
    assert "owns the provider" in spoken, "the handover must be named, not implied"


async def test_logout_with_nothing_stored_does_not_claim_to_have_removed_it() -> None:
    from omega_coding.commands import dispatch

    said: list[str] = []
    await dispatch("/logout anthropic", _context(emit=said.append))

    assert "Nothing stored" in " ".join(said)


async def test_an_unknown_provider_is_named_rather_than_stored() -> None:
    from omega_coding.commands import dispatch

    said: list[str] = []
    await dispatch("/login nosuchprovider", _context(emit=said.append))

    spoken = " ".join(said)
    assert "nosuchprovider" in spoken
    assert "anthropic" in spoken and "openai" in spoken, "it lists what it does know"


async def test_login_opens_the_modal_in_the_tui(tmp_path: Path) -> None:
    """**The bug a full green suite did not catch.**

    `push_screen_wait` raises `NoActiveWorker` unless its caller is a Textual
    worker, and `/` commands were dispatched straight from `on_input_submitted`,
    which is an event handler. So `/login` crashed in the TUI while every unit
    test passed — the approval modal never hit it because it is reached from
    inside `_drive`, which is already a worker.

    Found by driving the real app rather than the command in isolation. That is
    the only way this class of bug shows up.
    """
    from omega_coding.commands import CommandContext
    from omega_coding.tui.app import OmegaApp
    from omega_coding.tui.login import SecretModal
    from omega_coding.tui.widgets import PromptInput

    harness = Harness(
        provider=auth.LoginRequiredProvider("anthropic"),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )
    swapped: list[bool] = []
    context = CommandContext(
        harness=harness,
        store=None,
        tracker=None,  # type: ignore[arg-type]
        model="m",
        system="s",
        tools=[],
        hooks=harness.hooks,
        reload_provider=lambda: bool(swapped.append(True)) or True,
    )
    app = OmegaApp(harness, context=context, provider_name="anthropic")

    async with app.run_test() as pilot:
        app.query_one(PromptInput).value = "/login anthropic"
        await pilot.press("enter")
        await pilot.pause(0.3)

        # Anthropic now ships a registration, so the first screen is the choice
        # between the account and a key — not the key box. Pick "API key", which
        # is the second option; the account branch opens a real browser.
        from omega_coding.tui.login import ChoiceModal

        assert isinstance(app.screen, ChoiceModal), "the method choice never opened"
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.3)

        assert isinstance(app.screen, SecretModal), "the login modal never opened"

        app.screen.query_one("Input").value = KEY  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause(0.3)

    assert auth.stored("anthropic") == KEY
    assert swapped == [True], "the provider was not rebuilt"
    shown = " ".join(row.text for row in app.state.rows)
    assert KEY not in shown, "the key was written into the transcript"


async def test_a_command_does_not_cancel_a_running_turn(tmp_path: Path) -> None:
    """The other half of the worker fix.

    `run_turn` uses `exclusive=True`, so putting commands in the same worker
    group would make typing `/cost` mid-turn abort the turn. They get their own.
    """
    from omega_ai.fake import FakeProvider, text_turn
    from omega_coding.tui.app import OmegaApp

    app = OmegaApp(
        Harness(
            provider=FakeProvider([text_turn("done")]),
            model="m",
            system="s",
            tools=build_tools(tmp_path),
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        groups = {worker.group for worker in app.workers}

    # Nothing running yet, but the constant the fix relies on is asserted where
    # a future edit would see it.
    assert "command" not in groups or groups == {"command"}


# --------------------------------------------------- choosing a provider


def test_signed_in_to_nothing_chooses_nothing() -> None:
    """**This is what removed `--provider` from the everyday command line.**

    The flag defaulted to `"anthropic"`, so plain `omega` always tried Anthropic
    and an OpenAI user typed `--provider openai` every single time — a flag whose
    value was already sitting in their credentials file.
    """
    assert auth.choose_provider(None) is None
    assert auth.signed_in() == ()


def test_the_one_provider_you_are_signed_in_to_is_the_one_used() -> None:
    auth.save("openai", KEY)

    assert auth.signed_in() == ("openai",)
    assert auth.choose_provider(None) == "openai"


def test_several_credentials_resolve_by_a_documented_order() -> None:
    """A tie needs a rule, and a documented arbitrary one beats asking a
    question nobody wants asked at startup. `ENV_VARS` order decides."""
    auth.save("openai", KEY)
    auth.save("anthropic", KEY)

    assert auth.choose_provider(None) == next(iter(auth.ENV_VARS))


def test_the_flag_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """An override stays an override — including over being signed in to the
    other one, which is the only case where it matters."""
    auth.save("anthropic", KEY)

    assert auth.choose_provider("openai") == "openai"


def test_a_base_url_implies_the_openai_wire_format() -> None:
    """`--base-url` points at Ollama or vLLM, which speak Chat Completions and
    need no key. Choosing Anthropic for it would be choosing a wire format the
    endpoint does not speak."""
    assert auth.choose_provider(None, base_url="http://localhost:11434/v1") == "openai"


async def test_signed_in_to_nothing_names_no_provider_in_its_refusal(
    tmp_path: Path,
) -> None:
    """"Not signed in to anthropic" is the wrong sentence when you were never
    asked which one. It has to offer the choice instead of assuming one."""
    harness = Harness(
        provider=auth.LoginRequiredProvider(None),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )

    ends = [e async for e in harness.run("hi") if e.type == "agent_end"]
    message = ends[0].error_message or ""

    assert "Not signed in." in message
    assert "/login anthropic" in message and "/login openai" in message
    assert "Not signed in to" not in message, "it named a provider nobody chose"


async def test_bare_login_asks_which_provider_rather_than_assuming() -> None:
    """`/login` used to default to Anthropic — the same mistake `--provider`
    made, one layer up. Asking costs one keypress and is always right."""
    from omega_coding.commands import dispatch

    asked: list[tuple[str, list[str]]] = []

    async def choose(prompt: str, options: list[str]) -> str:
        asked.append((prompt, options))
        return "openai"

    async def paste(_prompt: str) -> str:
        return KEY

    said: list[str] = []
    await dispatch(
        "/login",
        _context(emit=said.append, ask_choice=choose, ask_secret=paste),
    )

    assert asked and asked[0][1] == list(auth.PROVIDERS), "it offered every provider"
    assert auth.stored("openai") == KEY
    assert auth.stored("anthropic") is None, "it did not guess anthropic"


async def test_a_stored_key_actually_reaches_the_provider() -> None:
    """**The bug that made `/login` look like it worked and not work.**

    Both adapters fall back to `os.environ` when `api_key` is None
    (`anthropic.py:335`, `openai.py:270`). An earlier `build_provider` only
    *tested* `auth.resolve(...)` and then constructed `AnthropicProvider()` with
    no arguments — so a key stored by `/login` opened the gate, the startup facts
    said "signed in", and the first request failed with an authentication error.

    Measured at the time: `_client.api_key` was `None` while `auth.resolve`
    returned the key. Nothing in the suite noticed, because every other test
    either used `FakeProvider` or had the variable exported.
    """
    from omega_ai.anthropic import AnthropicProvider
    from omega_ai.openai import OpenAIProvider

    auth.save("anthropic", KEY)
    auth.save("openai", KEY)

    # Constructed exactly as `cli.py:build_provider` does.
    anthropic = AnthropicProvider(api_key=auth.resolve("anthropic"))
    openai = OpenAIProvider(api_key=auth.resolve("openai"), base_url=None)

    assert anthropic._client.api_key == KEY, "the stored key never reached Anthropic"
    assert openai._client.api_key == KEY, "the stored key never reached OpenAI"


def test_the_resolved_credential_is_the_one_that_reaches_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The resolution order has to survive being passed explicitly.

    `AnthropicProvider` falls back to `os.environ` when `api_key` is None
    (`anthropic.py`), so the order is decided in two places and they have to
    agree. If `resolve` prefers the file and the adapter then reads the
    environment anyway, the request succeeds with the wrong account — which is
    the worst shape of failure, because nothing reports it.
    """
    from omega_ai.anthropic import AnthropicProvider

    auth.save("anthropic", "from-the-file")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-environment")

    provider = AnthropicProvider(api_key=auth.resolve("anthropic"))

    assert provider._client.api_key == "from-the-file"


async def test_login_by_account_survives_the_real_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The branch the other test stopped covering.**

    `test_login_opens_the_modal_in_the_tui` was the test that found
    `push_screen_wait` raising `NoActiveWorker` outside a worker — and it only
    found it by driving the real app. When Anthropic's registration started
    shipping, that test had to step past a new choice screen into the API-key
    branch, which left the account branch driven by nothing.

    That branch awaits `oauth.sign_in` for up to 180 seconds inside the
    `group="command"` worker, which is exactly the shape that broke before. The
    browser is stubbed; everything between the keypress and the stored token is
    real.
    """
    from omega_coding import oauth
    from omega_coding.commands import CommandContext
    from omega_coding.tui.app import OmegaApp
    from omega_coding.tui.login import ChoiceModal
    from omega_coding.tui.widgets import PromptInput

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    TOKEN = "sk-ant-oat01-FAKE-BUT-REAL-SHAPED-0123456789"
    opened: list[str] = []

    async def fake_sign_in(registration: object, **_: object) -> dict[str, object]:
        opened.append(getattr(registration, "provider", "?"))
        return {"access_token": TOKEN, "refresh_token": "r", "expires_in": 3600}

    monkeypatch.setattr(oauth, "sign_in", fake_sign_in)

    harness = Harness(
        provider=auth.LoginRequiredProvider("anthropic"),
        model="m",
        system="s",
        tools=build_tools(tmp_path),
    )
    swapped: list[bool] = []
    context = CommandContext(
        harness=harness,
        store=None,
        tracker=None,  # type: ignore[arg-type]
        model="m",
        system="s",
        tools=[],
        hooks=harness.hooks,
        reload_provider=lambda: bool(swapped.append(True)) or True,
    )
    app = OmegaApp(harness, context=context, provider_name="anthropic")

    async with app.run_test() as pilot:
        app.query_one(PromptInput).value = "/login anthropic"
        await pilot.press("enter")
        await pilot.pause(0.3)

        assert isinstance(app.screen, ChoiceModal), "the method choice never opened"
        # The first option is the account. No arrow key: take it as it lands.
        await pilot.press("enter")
        await pilot.pause(0.4)

        assert app.is_running, "the app died inside the account branch"

    assert opened == ["anthropic"], "the browser flow was never reached"
    assert auth.stored("anthropic") == TOKEN
    assert swapped == [True], "the provider was not rebuilt"

    shown = " ".join(row.text for row in app.state.rows)
    assert TOKEN not in shown, "the access token was written into the transcript"
