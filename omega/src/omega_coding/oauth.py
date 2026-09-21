"""Signing in with an account, rather than pasting a key.

## The one thing that makes this hard, and it is not the code

The browser dance below is about two hundred lines and both references show it
in full. **The part omega cannot supply is a registration.**

"Sign in with your Anthropic account" is not something a program can decide to
offer. The provider has to register the program first and issue it a *client id*;
their login page checks that id and refuses to talk to anything it does not
recognise. It is the same reason a website cannot show a "Sign in with Google"
button by writing code alone — it registers with Google first.

An API key needs none of that: it is a secret **you** already own, and handing it
over asks nobody's permission. That asymmetry is why every tool's API-key
provider list is long and its account-sign-in list is three entries.

## The client id ships in this source, and what that costs

Anthropic operates no public registration for third-party apps — there is no
form that issues a client id for "sign in with your Claude account". Both
references resolve that the same way, by shipping Claude Code's own:

    Tau   oauth_anthropic.py:32   ANTHROPIC_CLIENT_ID = 9d1c250a-…
    Pi    anthropic.ts:29         decode("OWQxYzI1MGEt…")   ← the same value

omega now does too, in `BUILTIN_REGISTRATIONS` below. **Say plainly what that
means:** every account sign-in tells Anthropic's authorisation server that omega
is Claude Code, one of the scopes it requests is named after that product, and
`omega_ai/anthropic.py` must then send Claude Code's identity headers and a
system block saying the same thing or the API refuses the token.

That was a deliberate reversal. The earlier version kept the mechanism here and
left the identity to an operator-supplied `~/.omega/oauth.json`, which was
honest and meant the feature did not work for anybody. Matching the references
was chosen over that, with the cost recorded here rather than hidden.

`~/.omega/oauth.json` still works and still wins: anyone holding a client id of
their own drops it there and overrides the borrowed one entirely.

## How the code gets back out of the browser

The awkward part of OAuth in a terminal is that the answer arrives in a browser
and is needed in a program. The shape both references use, and this one:

1. Invent a random `verifier`, keep it **in memory only**, and send its SHA-256
   hash as the `code_challenge`. This is PKCE.
2. Start an HTTP server on `127.0.0.1` at a fixed port and open the browser at
   the provider's authorise URL, with that port as `redirect_uri`.
3. The provider redirects the browser to `http://127.0.0.1:<port>/callback?code=…`
   — so the local server receives the code.
4. Exchange the code **plus the verifier** for tokens.

Step 4 is why snooping the redirect is not enough: the code alone is worthless
without the verifier, which never left the process. That is the whole point of
PKCE, and it is what makes a loopback redirect safe on a shared machine.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


#: An **override** for the built-ins below, not the only source any more. Anyone
#: holding a client id of their own writes it here and never touches the borrowed
#: one. Separate from `auth.json` because that holds secrets and this holds
#: identifiers: one is per-machine, the other is safe to copy between machines.
def registrations_path() -> Path:
    return Path.home() / ".omega" / "oauth.json"


#: What a provider's registration has to say. Everything except `client_id` has a
#: sensible published value; `client_id` is the part only the provider can issue.
@dataclass(frozen=True, slots=True)
class Registration:
    provider: str
    client_id: str
    authorize_url: str
    token_url: str
    scope: str
    #: Loopback port. Fixed rather than random because the provider matches the
    #: whole `redirect_uri` against what was registered, so it cannot vary.
    port: int
    #: **`localhost`, not `127.0.0.1`.** The two resolve to the same socket, and
    #: the provider does not resolve anything: it compares the `redirect_uri`
    #: string to the one on file. Both references send `localhost`
    #: (`oauth_anthropic.py:35`, `anthropic.ts:35`), so sending the numeric form
    #: would fail the exchange with a mismatch, not a connection error.
    redirect_host: str = "localhost"
    #: Authorise-URL parameters outside the OAuth standard. Anthropic requires
    #: `code=true`; both references send it (`oauth_anthropic.py:58`,
    #: `anthropic.ts:241`) and omitting it changes what the consent page does.
    extra_params: tuple[tuple[str, str], ...] = ()
    #: The path half of `redirect_uri`. Anthropic registered `/callback` and
    #: OpenAI registered `/auth/callback`, and the provider compares the whole
    #: string — so this is not cosmetic.
    redirect_path: str = "/callback"
    #: How the token endpoint wants its body. Anthropic takes JSON
    #: (`oauth_anthropic.py:141`); OpenAI takes `application/x-www-form-urlencoded`
    #: (`oauth.py:308-311`). Sending the wrong one is a 400 that says nothing
    #: useful, so it is a property of the registration rather than a guess.
    form_encoded: bool = False
    #: A JWT claim on the access token holding the account this credential is
    #: for. OpenAI puts the ChatGPT account id here and requires it back as a
    #: header on every request; Anthropic has no equivalent.
    account_claim: str = ""

    @property
    def redirect_uri(self) -> str:
        return f"http://{self.redirect_host}:{self.port}{self.redirect_path}"


#: Registrations omega ships with. **Read the module docstring before adding
#: one** — an entry here is a claim about who omega is, made to someone else's
#: authorisation server.
#:
#: Anthropic only. OpenAI's subscription OAuth (Codex) is deliberately absent:
#: the token it returns is not accepted by `api.openai.com`, it only opens
#: `chatgpt.com/backend-api/codex/responses`, which is a different wire format
#: needing its own adapter — 1,054 lines in Tau (`tau_ai/openai_codex.py`), a
#: comparable file in Pi. A sign-in that succeeds and then cannot serve a single
#: request is worse than no sign-in, so it is not offered.
BUILTIN_REGISTRATIONS: dict[str, Registration] = {
    "anthropic": Registration(
        provider="anthropic",
        #: Claude Code's. There is no other one to hold - see the module
        #: docstring for what using it commits every install to.
        client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        authorize_url="https://claude.ai/oauth/authorize",
        token_url="https://platform.claude.com/v1/oauth/token",
        scope=(
            "org:create_api_key user:profile user:inference "
            "user:sessions:claude_code user:mcp_servers user:file_upload"
        ),
        port=53692,
        extra_params=(("code", "true"),),
    ),
    #: A ChatGPT subscription, not an API account. **The token this returns is
    #: rejected by `api.openai.com`** — it opens `chatgpt.com/backend-api`, a
    #: different wire format, which is why it has its own adapter
    #: (`omega_ai/openai_codex.py`) rather than a header change.
    #:
    #: The id is Codex CLI's, carried identically by both references
    #: (`tau/oauth.py:33`, `pi/openai-codex.ts:26`). Same trade as Anthropic's,
    #: recorded in the module docstring.
    "openai-codex": Registration(
        provider="openai-codex",
        client_id="app_EMoamEEZ73f0CkXaXp7hrann",
        authorize_url="https://auth.openai.com/oauth/authorize",
        token_url="https://auth.openai.com/oauth/token",
        scope="openid profile email offline_access",
        port=1455,
        extra_params=(
            ("id_token_add_organizations", "true"),
            ("codex_cli_simplified_flow", "true"),
        ),
        redirect_path="/auth/callback",
        form_encoded=True,
        account_claim="https://api.openai.com/auth",
    ),
}


def load_registrations() -> dict[str, Registration]:
    """The built-ins, with `~/.omega/oauth.json` layered on top.

    Absent is the **normal** state for that file: nobody has to write one, and
    `/login` offers the account option regardless because `BUILTIN_REGISTRATIONS`
    is never empty. A file entry replaces a built-in of the same name outright
    rather than merging field by field — half a registration from each source is
    a combination neither provider ever issued.
    """
    found: dict[str, Registration] = dict(BUILTIN_REGISTRATIONS)
    found.update(_file_registrations())
    return found


def _file_registrations() -> dict[str, Registration]:
    """Whatever `oauth.json` holds. Missing or malformed reads as "none"."""
    try:
        data = json.loads(registrations_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    found: dict[str, Registration] = {}
    for provider, entry in data.items():
        if not isinstance(entry, dict):
            continue
        try:
            found[provider] = Registration(
                provider=provider,
                client_id=str(entry["client_id"]),
                authorize_url=str(entry["authorize_url"]),
                token_url=str(entry["token_url"]),
                scope=str(entry.get("scope", "")),
                port=int(entry.get("port", 53682)),
            )
        except (KeyError, TypeError, ValueError):
            # One bad entry must not hide the good ones. A provider that cannot
            # be parsed is a provider that is not offered, which is the same
            # outcome as not registering it.
            continue
    return found


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def create_pkce_pair() -> tuple[str, str]:
    """A verifier and its S256 challenge.

    The verifier never leaves this process; only its hash goes to the provider.
    Proof of possession at exchange time is what makes a code intercepted from
    the loopback redirect useless on its own.
    """
    verifier = secrets.token_urlsafe(64)
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def authorize_url(registration: Registration, *, challenge: str, state: str) -> str:
    """The URL to open in the browser."""
    query = urllib.parse.urlencode(
        {
            **dict(registration.extra_params),
            "response_type": "code",
            "client_id": registration.client_id,
            "redirect_uri": registration.redirect_uri,
            "scope": registration.scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{registration.authorize_url}?{query}"


class _CallbackServer:
    """A one-request HTTP server on the loopback interface.

    Bound to `127.0.0.1`, never `0.0.0.0`: the redirect only ever comes from the
    browser on this machine, and binding wider would accept a code from the
    network.
    """

    def __init__(self, port: int, expected_state: str) -> None:
        self._result: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._loop = asyncio.get_running_loop()
        self._expected = expected_state
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the stdlib spells it this way
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                code = next(iter(params.get("code", [])), None)
                state = next(iter(params.get("state", [])), None)

                # **State is checked here, not after.** A mismatched state means
                # this redirect belongs to a different flow, and accepting its
                # code would be accepting an authorisation nobody in this process
                # asked for.
                ok = bool(code) and state == outer._expected
                body = (
                    b"<html><body><h2>omega is signed in.</h2>"
                    b"<p>You can close this tab.</p></body></html>"
                    if ok
                    else b"<html><body><h2>omega could not sign in.</h2></body></html>"
                )
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                outer._finish(code if ok else None)

            def log_message(self, *_: Any) -> None:
                """Silence. The stdlib logs every request to stderr, and this one
                request carries an authorization code in its path."""

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _finish(self, code: str | None) -> None:
        # Called from the server thread; the future belongs to the event loop.
        self._loop.call_soon_threadsafe(
            lambda: None if self._result.done() else self._result.set_result(code)
        )

    async def wait(self, timeout: float) -> str | None:
        try:
            return await asyncio.wait_for(asyncio.shield(self._result), timeout)
        except TimeoutError:
            return None

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)


async def sign_in(
    registration: Registration,
    *,
    timeout: float = 180.0,
    open_browser: bool = True,
) -> dict[str, Any] | None:
    """Run the whole flow. Returns the token payload, or None if it did not finish.

    Returns rather than raises on the ordinary failures — a closed tab, a denied
    consent, a timeout — because none of those is exceptional and `/login` has to
    report them the same way it reports a cancelled key paste.
    """
    verifier, challenge = create_pkce_pair()
    state = secrets.token_urlsafe(24)
    server = _CallbackServer(registration.port, state)
    try:
        url = authorize_url(registration, challenge=challenge, state=state)
        if open_browser:
            webbrowser.open(url)
        code = await server.wait(timeout)
    finally:
        server.close()

    if not code:
        return None
    return await exchange(registration, code=code, verifier=verifier, state=state)


async def exchange(
    registration: Registration, *, code: str, verifier: str, state: str = ""
) -> dict[str, Any] | None:
    """Trade the code plus the verifier for tokens.

    `httpx` rather than the vendor SDKs: this is a plain OAuth endpoint, and
    reaching for `anthropic` or `openai` here would put a vendor import in a file
    that is meant to work for any provider the operator registers.

    **`state` is echoed back here**, which the OAuth standard does not ask for.
    Both references send it (`oauth_anthropic.py:112`, `anthropic.ts:203`), and
    an endpoint that validates it rejects an exchange without it — a 400 that
    this function reports as "did not complete", naming nothing. Sending it costs
    one field and removes that whole class of silent failure.
    """
    return await _post_token(
        registration,
        {
            "grant_type": "authorization_code",
            "code": code,
            "state": state,
            "redirect_uri": registration.redirect_uri,
            "client_id": registration.client_id,
            "code_verifier": verifier,
        },
    )


async def _post_token(
    registration: Registration, body: dict[str, str]
) -> dict[str, Any] | None:
    """One place decides how a token request is encoded.

    Anthropic's endpoint takes JSON and OpenAI's takes form encoding. Getting it
    wrong is a 400 whose body explains nothing, so the choice is carried on the
    registration rather than inferred — and it is made here so `exchange` and
    `refresh` cannot drift apart.
    """
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        if registration.form_encoded:
            response = await client.post(
                registration.token_url,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        else:
            response = await client.post(
                registration.token_url,
                json=body,
                headers={"Content-Type": "application/json"},
            )
    if response.status_code >= 400:
        return None
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def account_id_from_token(registration: Registration, access_token: str) -> str | None:
    """Read the account id out of an access JWT, when the provider uses one.

    **Not a signature check.** omega is the audience here, not the verifier: the
    token came straight from the provider's own token endpoint over TLS, and the
    only thing being read is which account it belongs to so the right header can
    be sent back. Tau does the same and says so (`oauth.py:222-240`).

    Every failure is None — a token that is not a JWT, a missing claim, bad
    padding. The caller reports "sign-in did not complete" rather than storing a
    credential whose requests would all be rejected for a missing header.
    """
    if not registration.account_claim:
        return None
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            return None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    claim = payload.get(registration.account_claim) if isinstance(payload, dict) else None
    if not isinstance(claim, dict):
        return None
    found = claim.get("chatgpt_account_id")
    return found.strip() if isinstance(found, str) and found.strip() else None


#: Renew this many seconds before the stated expiry. A token that is valid when
#: the request is built and expired when it arrives fails for a reason the user
#: cannot act on; both references carry the same cushion at five minutes
#: (`oauth_anthropic.py:41`, `anthropic.ts:374`).
REFRESH_SKEW = 5 * 60


async def access_token(provider: str) -> str:
    """The token to authenticate with right now, renewed if it is about to lapse.

    **This is what makes an account sign-in survive a long session.** An API key
    is a constant, so the adapter could read it once; a subscription token lasts
    hours, and a turn that begins valid can end unauthorised. `AnthropicProvider`
    re-resolves on every request and every retry for exactly this reason
    (`omega_ai/anthropic.py:498-508`) — this is the function that seam was cut
    for, and until now nothing filled it.

    A stored credential wins, as everywhere else — and it wins here even when the
    renewal fails. Pi's rule is explicit that there is **no silent env fallback
    after a failed refresh** (`resolve.ts:44-46`), because falling back would
    send the request with a different credential than the one the user signed in
    with and report nothing. Returns `""` rather than raising when there is
    nothing: a missing credential is the `LoginRequiredProvider`'s business,
    decided before this is ever called.
    """
    import time

    from omega_coding import auth

    if not auth.owns(provider):
        variable = auth.ENV_VARS.get(provider)
        return (os.environ.get(variable) if variable else None) or ""

    entry = auth.stored_oauth(provider)
    if entry is None:
        # Stored, but an API key rather than a token - or an entry too broken to
        # read. Either way the provider is owned and the environment is not
        # consulted.
        return auth.stored(provider) or ""

    access = str(entry.get("access") or "")
    expires_at = float(entry.get("expires_at") or 0)
    token = str(entry.get("refresh") or "")
    registration = load_registrations().get(provider)

    # `expires_at == 0` means the provider never said, which is treated as "does
    # not expire". Refreshing on that guess would burn the refresh token every
    # single request.
    if not (expires_at and token and registration):
        return access
    if time.time() < expires_at - REFRESH_SKEW:
        return access

    renewed = await refresh(registration, refresh_token=token)
    if not renewed or not renewed.get("access_token"):
        # The old token is returned rather than an empty string: it may still
        # have seconds on it, and a real 401 is a clearer failure than a request
        # sent with no credential at all.
        return access
    auth.save_oauth(
        provider,
        access=str(renewed["access_token"]),
        # A refresh response need not reissue the refresh token, and both
        # references keep the previous one when it does not
        # (`oauth_anthropic.py:154`).
        refresh=str(renewed.get("refresh_token") or token),
        expires_in=int(renewed.get("expires_in") or 0),
    )
    return str(renewed["access_token"])


async def refresh(registration: Registration, *, refresh_token: str) -> dict[str, Any] | None:
    """Trade a refresh token for a new access token.

    The reason `kind` was put in `auth.json` from the first write: an OAuth entry
    expires and a key does not, so something has to know which it is holding
    before it can decide whether to renew it.
    """
    return await _post_token(
        registration,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": registration.client_id,
        },
    )
