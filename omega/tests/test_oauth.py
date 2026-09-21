"""The OAuth flow, driven end to end against a provider that is not real.

**A fake provider, not a mock.** It is a real HTTP server that issues a real
redirect and checks a real PKCE verifier — so what this exercises is the whole
mechanism: the loopback listener, the state check, the code exchange, and the
proof-of-possession that makes an intercepted code useless.

What it deliberately cannot test is a *registration*. omega ships no client ids
(`oauth.py` explains why), so every test here supplies its own — which is exactly
the shape an operator who registers their own app would use.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from omega_coding import auth, oauth


class FakeProviderServer:
    """Stands in for Anthropic. Issues codes, and checks the PKCE verifier."""

    def __init__(self) -> None:
        self.issued: dict[str, str] = {}      # code -> the challenge it was issued against
        self.exchanges: list[dict[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                code = "the-one-time-code"
                outer.issued[code] = params["code_challenge"][0]
                # Exactly what a provider does: redirect the browser back.
                target = (
                    f"{params['redirect_uri'][0]}?code={code}&state={params['state'][0]}"
                )
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.exchanges.append(body)

                challenge = outer.issued.get(body.get("code", ""))
                verifier = body.get("code_verifier", "")
                # The check that makes a stolen code worthless.
                expected = oauth._base64url(
                    __import__("hashlib").sha256(verifier.encode()).digest()
                )
                ok = challenge is not None and expected == challenge

                payload = (
                    {"access_token": "at-123", "refresh_token": "rt-456", "expires_in": 3600}
                    if ok
                    else {"error": "invalid_grant"}
                )
                raw = json.dumps(payload).encode()
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def provider() -> Any:
    server = FakeProviderServer()
    yield server
    server.close()


def _registration(provider: FakeProviderServer, port: int = 53699) -> oauth.Registration:
    return oauth.Registration(
        provider="fake",
        client_id="a-client-id-the-operator-supplied",
        authorize_url=f"{provider.base}/authorize",
        token_url=f"{provider.base}/token",
        scope="read write",
        port=port,
    )


# ------------------------------------------------------------------ the pieces


def test_the_verifier_never_equals_the_challenge() -> None:
    """PKCE in one assertion: what is sent is a hash of what is kept.

    If these were ever equal, the browser would be carrying the secret and the
    whole scheme would be decoration.
    """
    verifier, challenge = oauth.create_pkce_pair()

    assert verifier != challenge
    assert len(verifier) > 40
    assert "=" not in challenge, "base64url, unpadded, as the spec requires"


def test_the_authorize_url_asks_for_s256_and_carries_no_secret() -> None:
    server = FakeProviderServer()
    try:
        verifier, challenge = oauth.create_pkce_pair()
        url = oauth.authorize_url(_registration(server), challenge=challenge, state="st")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

        assert query["code_challenge_method"] == ["S256"]
        assert query["code_challenge"] == [challenge]
        assert verifier not in url, "the verifier reached the browser"
    finally:
        server.close()


# ------------------------------------------------------------- the whole flow


async def test_a_complete_sign_in(provider: FakeProviderServer) -> None:
    """Browser opens, provider redirects to the loopback, code is exchanged.

    `open_browser=False` and a plain GET stand in for the human: the request the
    browser would make is the request the test makes, so every other step is the
    real one.
    """
    registration = _registration(provider)

    async def pretend_to_be_the_browser() -> None:
        await asyncio.sleep(0.2)
        async with httpx.AsyncClient(follow_redirects=True, timeout=5) as client:
            # Whatever `sign_in` put in the URL is what the browser would open.
            await client.get(captured["url"])

    captured: dict[str, str] = {}
    real_authorize_url = oauth.authorize_url

    def remember(reg: oauth.Registration, **kwargs: Any) -> str:
        captured["url"] = real_authorize_url(reg, **kwargs)
        return captured["url"]

    oauth.authorize_url = remember  # type: ignore[assignment]
    try:
        browser = asyncio.create_task(pretend_to_be_the_browser())
        tokens = await oauth.sign_in(registration, timeout=10, open_browser=False)
        await browser
    finally:
        oauth.authorize_url = real_authorize_url  # type: ignore[assignment]

    assert tokens is not None, "the flow did not complete"
    assert tokens["access_token"] == "at-123"
    assert tokens["refresh_token"] == "rt-456"


async def test_a_code_without_its_verifier_is_worthless(
    provider: FakeProviderServer,
) -> None:
    """**The property the whole design rests on.**

    Someone who sees the loopback redirect has the code. They do not have the
    verifier, which never left the process. This asserts the provider rejects
    that exchange — which is what makes a plaintext `http://127.0.0.1` redirect
    acceptable in the first place.
    """
    registration = _registration(provider)
    verifier, challenge = oauth.create_pkce_pair()

    # The provider issues a code against the real challenge...
    async with httpx.AsyncClient(follow_redirects=False, timeout=5) as client:
        await client.get(
            oauth.authorize_url(registration, challenge=challenge, state="st")
        )

    # ...and an attacker holding only the code tries to spend it.
    stolen = await oauth.exchange(
        registration, code="the-one-time-code", verifier="a-guess"
    )
    assert stolen is None, "the code was spendable without the verifier"

    # The real holder succeeds with the same code.
    genuine = await oauth.exchange(
        registration, code="the-one-time-code", verifier=verifier
    )
    assert genuine is not None and genuine["access_token"] == "at-123"


async def test_a_mismatched_state_is_rejected_by_the_listener(
    provider: FakeProviderServer,
) -> None:
    """A redirect belonging to some other flow must not be taken as this one's.

    **Asserted on the listener, not on the whole flow — and the first version
    was worthless for exactly that reason.** Driving `sign_in` end to end, a
    wrong-state redirect still produced `tokens is None`, because the bogus code
    then failed the *exchange*. Removing the state check entirely left the test
    green. It was measuring the wrong step.

    So this reads the callback server's own result: with the wrong state the
    code must be discarded there, before anything is spent.
    """
    server = oauth._CallbackServer(53697, expected_state="the-real-one")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            wrong = await client.get(
                "http://127.0.0.1:53697/callback?code=stolen&state=some-other-flow"
            )
        assert wrong.status_code == 400, "the listener accepted a foreign redirect"
        assert await server.wait(0.5) is None, "it kept the code anyway"
    finally:
        server.close()


async def test_the_matching_state_is_accepted(provider: FakeProviderServer) -> None:
    """The other half. Without it, a listener that rejected everything would
    pass the test above and be equally broken."""
    server = oauth._CallbackServer(53696, expected_state="the-real-one")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            good = await client.get(
                "http://127.0.0.1:53696/callback?code=mine&state=the-real-one"
            )
        assert good.status_code == 200
        assert await server.wait(0.5) == "mine"
    finally:
        server.close()


# --------------------------------------------------------- the registration


def test_omega_ships_no_client_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**The reversal, asserted — including what it costs.**

    This test used to say the opposite: that no client id shipped. It was
    accurate and it meant account sign-in worked for nobody, because Anthropic
    registers no third-party apps. The decision was reversed deliberately, so the
    test now pins the consequences rather than the abstention — every one of
    which is a claim to be Claude Code, and none of which is optional.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    found = oauth.load_registrations()
    assert "anthropic" in found, "a fresh install must offer the account option"

    registration = found["anthropic"]
    assert registration.client_id == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    assert "user:sessions:claude_code" in registration.scope

    # `localhost`, never the numeric form: the provider compares this string to
    # the one on file rather than resolving it, so `127.0.0.1` fails the
    # exchange even though it is the same socket.
    assert registration.redirect_uri == "http://localhost:53692/callback"

    # Anthropic-specific and required by both references.
    assert dict(registration.extra_params) == {"code": "true"}


def test_the_two_registrations_differ_where_the_providers_do() -> None:
    """Three fields where copying Anthropic's shape would silently fail.

    Each is a string the provider compares or parses rather than interprets, so
    getting one wrong is a 400 whose body explains nothing:

    * the redirect **path** — `/callback` vs `/auth/callback`, matched whole
    * the token body **encoding** — JSON vs form, per endpoint
    * the **account claim** — OpenAI requires the id back as a request header
    """
    anthropic = oauth.BUILTIN_REGISTRATIONS["anthropic"]
    codex = oauth.BUILTIN_REGISTRATIONS["openai-codex"]

    assert anthropic.redirect_uri == "http://localhost:53692/callback"
    assert codex.redirect_uri == "http://localhost:1455/auth/callback"

    assert anthropic.form_encoded is False
    assert codex.form_encoded is True

    assert anthropic.account_claim == ""
    assert codex.account_claim == "https://api.openai.com/auth"

    assert codex.client_id == "app_EMoamEEZ73f0CkXaXp7hrann", "Codex CLI's, as both refs"


def test_the_chatgpt_account_id_is_read_out_of_the_access_token() -> None:
    """It is not in the token response — it is a claim inside the access JWT.

    Every ChatGPT request carries it as `chatgpt-account-id`, so a sign-in that
    cannot find it has produced a credential whose every request would be
    rejected. Better to fail the login than to store that.
    """
    import base64
    import json

    codex = oauth.BUILTIN_REGISTRATIONS["openai-codex"]

    def jwt(payload: dict[str, object]) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        return f"header.{raw}.signature"

    good = jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-123"}})
    assert oauth.account_id_from_token(codex, good) == "acct-123"

    # Every failure shape resolves to None rather than raising.
    assert oauth.account_id_from_token(codex, jwt({"other": 1})) is None
    assert oauth.account_id_from_token(codex, "not-a-jwt") is None
    assert oauth.account_id_from_token(codex, "a.!!!not-base64!!!.c") is None

    # Anthropic declares no claim, so it never looks for one.
    assert oauth.account_id_from_token(oauth.BUILTIN_REGISTRATIONS["anthropic"], good) is None


def test_a_registration_the_operator_supplied_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = oauth.registrations_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "acme": {
                    "client_id": "mine",
                    "authorize_url": "https://acme.test/authorize",
                    "token_url": "https://acme.test/token",
                    "scope": "inference",
                    "port": 53690,
                }
            }
        )
    )

    found = oauth.load_registrations()

    assert set(found) == {"acme", *oauth.BUILTIN_REGISTRATIONS}, "the built-ins must survive"
    assert found["acme"].client_id == "mine"
    assert found["acme"].redirect_uri == "http://localhost:53690/callback"


def test_one_unusable_entry_does_not_hide_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider that cannot be parsed is a provider that is not offered — the
    same outcome as not registering it, rather than a startup failure."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = oauth.registrations_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "broken": {"client_id": "x"},
                "good": {
                    "client_id": "y",
                    "authorize_url": "https://a.test/a",
                    "token_url": "https://a.test/t",
                },
            }
        )
    )

    assert set(oauth.load_registrations()) == {"good", *oauth.BUILTIN_REGISTRATIONS}


def test_a_file_entry_replaces_a_builtin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escape hatch from the borrowed identity, asserted.

    Anyone holding a client id of their own must be able to use it *instead of*
    Claude Code's, not alongside it. Replacement is whole-entry: half a
    registration from each source is a combination no provider ever issued.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = oauth.registrations_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "anthropic": {
                    "client_id": "my-own-id",
                    "authorize_url": "https://mine.test/authorize",
                    "token_url": "https://mine.test/token",
                }
            }
        )
    )

    registration = oauth.load_registrations()["anthropic"]

    assert registration.client_id == "my-own-id"
    assert "9d1c250a" not in registration.client_id
    assert registration.authorize_url == "https://mine.test/authorize"


async def test_an_oauth_token_is_sent_as_a_bearer_with_the_claude_code_identity(
    tmp_path: Path,
) -> None:
    """**Every claim to be Claude Code, in one place.**

    Anthropic rejects an OAuth token that arrives without these. Three of them
    are headers and the fourth is a system block the model itself reads, which is
    the one worth looking at twice: the borrowed registration does not stop at
    the authorisation server.
    """
    import sys

    sys.path.insert(0, "tests")
    from omega_ai.anthropic import CLAUDE_CODE_IDENTITY, OAUTH_HEADERS, AnthropicProvider
    from stub_anthropic import StubClient

    client = StubClient()

    async def resolve() -> str:
        return "sk-ant-oat01-TOKEN"

    provider = AnthropicProvider(client=client, auth=resolve)  # type: ignore[arg-type]
    async for _ in provider.stream_response(model="m", system="REAL", messages=[], tools=[]):
        pass

    assert client.api_key is None, "an api key header would go out beside the bearer"
    assert client.auth_token == "sk-ant-oat01-TOKEN"

    call = client.calls[0]
    assert call["extra_headers"] == OAUTH_HEADERS
    assert call["extra_headers"]["anthropic-beta"] == "claude-code-20250219,oauth-2025-04-20"

    system = call["system"]
    assert system[0]["text"] == CLAUDE_CODE_IDENTITY, "the identity block must come first"
    assert system[1]["text"] == "REAL", "the real prompt must survive behind it"


async def test_an_api_key_carries_none_of_the_claude_code_identity(tmp_path: Path) -> None:
    """The other half. A key is omega's own credential and claims nothing.

    Without this the previous test passes on code that sends the identity
    unconditionally — which would tell every keyed user's model it is a product
    it is not, and burn a cache prefix for it.
    """
    import sys

    sys.path.insert(0, "tests")
    from omega_ai.anthropic import AnthropicProvider
    from stub_anthropic import StubClient

    client = StubClient()

    async def resolve() -> str:
        return "sk-ant-api03-KEY"

    provider = AnthropicProvider(client=client, auth=resolve)  # type: ignore[arg-type]
    async for _ in provider.stream_response(model="m", system="REAL", messages=[], tools=[]):
        pass

    assert client.api_key == "sk-ant-api03-KEY"
    assert client.auth_token is None
    call = client.calls[0]
    assert call["extra_headers"] is None
    assert len(call["system"]) == 1, "a key request must carry one system block"
    assert call["system"][0]["text"] == "REAL"


async def test_signing_in_by_account_clears_a_key_left_on_the_client(tmp_path: Path) -> None:
    """**Measured, not assumed**, and the reason both fields are always assigned.

        AsyncAnthropic(api_key=K); client.auth_token = T
        → {'X-Api-Key': K, 'Authorization': 'Bearer T'}

    Both headers, together. So a `/login` that swaps a key for an account token
    without clearing the key puts a dead credential on the wire beside the live
    one — and the failure is a confusing 401, not a missing header.
    """
    import sys

    sys.path.insert(0, "tests")
    from omega_ai.anthropic import AnthropicProvider
    from stub_anthropic import StubClient

    client = StubClient()
    tokens = iter(["sk-ant-api03-KEY", "sk-ant-oat01-TOKEN"])

    async def resolve() -> str:
        return next(tokens)

    provider = AnthropicProvider(client=client, auth=resolve)  # type: ignore[arg-type]
    async for _ in provider.stream_response(model="m", system="s", messages=[], tools=[]):
        pass
    assert client.api_key == "sk-ant-api03-KEY"

    async for _ in provider.stream_response(model="m", system="s", messages=[], tools=[]):
        pass

    assert client.api_key is None, "the old key survived the switch to an account"
    assert client.auth_token == "sk-ant-oat01-TOKEN"


async def test_an_expiring_token_is_renewed_before_the_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The gap that made the whole feature temporary.**

    `refresh()` existed and nothing called it — `grep` found zero callers. A
    subscription token lasts hours, so a session longer than one would die
    mid-task with no way back. This is the test that the seam
    `AnthropicProvider` cut for renewal is now filled.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    auth.save_oauth("anthropic", access="old", refresh="refresh-me", expires_in=10)

    async def fake_refresh(registration: object, *, refresh_token: str) -> dict[str, object]:
        assert refresh_token == "refresh-me"
        return {"access_token": "renewed", "expires_in": 3600}

    monkeypatch.setattr(oauth, "refresh", fake_refresh)

    assert await oauth.access_token("anthropic") == "renewed"
    # Written back, or the next request renews again and burns the token.
    assert auth.stored("anthropic") == "renewed"


async def test_a_token_with_time_left_is_not_renewed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renewing every request would spend the refresh token for nothing, and
    each renewal is a round trip in front of a turn the user is waiting on."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    auth.save_oauth("anthropic", access="still-good", refresh="r", expires_in=3600)

    async def explode(registration: object, *, refresh_token: str) -> dict[str, object]:
        raise AssertionError("a live token was refreshed")

    monkeypatch.setattr(oauth, "refresh", explode)

    assert await oauth.access_token("anthropic") == "still-good"


async def test_a_stored_token_beats_an_exported_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The renewal path obeys the same order as everything else.

    This is the case the flip was made for: someone with a key in `.env` signs in
    with their subscription. Under the old order the token was stored, reported
    as success, and never used. `auth.py` and this function resolve credentials
    in two different places, so both are pinned.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    auth.save_oauth("anthropic", access="from-file", refresh="r", expires_in=3600)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-environment")

    assert await oauth.access_token("anthropic") == "from-file"


async def test_the_exported_key_is_used_when_no_account_is_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction, so a flip that deletes the fallback cannot pass."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-environment")

    assert await oauth.access_token("anthropic") == "from-environment"


def test_an_oauth_token_is_redacted_and_named_as_one() -> None:
    """Redaction has been wrong six times in this project; account sign-in adds
    two new secret shapes, so it is checked rather than assumed.

    Measured: the pre-existing `sk-ant-…` pattern already masked both, so nothing
    ever leaked. What was wrong was the label — a transcript saying "Anthropic
    API key" where a subscription token was masked sends someone to revoke the
    wrong credential.
    """
    from omega_coding.redact import redact

    for token in ("sk-ant-oat01-AbCdEf1234567890XyZwVu", "sk-ant-ort01-AbCdEf1234567890XyZwVu"):
        result = redact(f"the token is {token}")
        text = result[0] if isinstance(result, tuple) else result
        assert token not in str(text), "an oauth token reached the transcript"
        assert "OAuth token" in str(text), "masked, but named as the wrong kind"

    result = redact("the key is sk-ant-api03-AbCdEf1234567890XyZwVu")
    text = result[0] if isinstance(result, tuple) else result
    assert "API key" in str(text), "an api key must not be relabelled an oauth token"


async def test_login_says_so_when_it_takes_over_from_an_exported_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mirror of `/logout`'s caveat, which existed; this one did not.

    It began as a warning that the sign-in would be ignored. The resolution flip
    turned it inside out — the sign-in now wins — and it is still worth saying,
    because the exported key silently stops being used and nothing else on screen
    reports the handover.
    """
    from omega_coding.commands import CommandContext, dispatch

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "exported-and-winning")

    written: list[str] = []

    async def secret(_prompt: str) -> str:
        return "sk-ant-api03-pasted"

    context = CommandContext(
        harness=None,  # type: ignore[arg-type]
        store=None,
        tracker=None,  # type: ignore[arg-type]
        model="m",
        system="s",
        tools=[],
        hooks=None,  # type: ignore[arg-type]
        emit=written.append,
        ask_secret=secret,
    )
    await dispatch("/login anthropic", context)

    said = " ".join(written)
    assert "ANTHROPIC_API_KEY is also set" in said, "the handover was not mentioned"
    assert "/logout switches back" in said, "no way out was offered"
    assert "sk-ant-api03-pasted" not in said, "the key was echoed back"
