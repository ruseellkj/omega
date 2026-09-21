"""Where a credential lives, and what happens when there isn't one.

## Why omega owns a file at all

Until now the only way to give omega a key was to know that it walks outward
from the working directory looking for `.env` (`env.py:47`). That works and it
is not discoverable: nothing in the product tells you, and there is nowhere for
`/login` to put anything.

## Why a file rather than the shell

`~/.zshrc` was the other candidate and it loses on three counts: omega would be
editing a file it does not own, the secret would be exported into every process
the user starts, and it cannot hold a rotating token if OAuth ever arrives.

**Neither reference does it either.** Tau writes `~/.tau/credentials.json` at
`0600` (`credentials.py:127-153`); Pi writes `~/.pi/agent/auth.json` at `0600`
inside a `0700` directory (`auth-storage.ts:21,54`). Neither uses a keyring —
`FileAuthStorageBackend` is Pi's only backend, and Tau has no keyring dependency
at all. Two independent implementations reaching the same shape is the strongest
signal available here.

## The resolution order, and why a stored credential wins

    auth.json  →  environment variable  →  nothing

**This is the reverse of what omega did until now**, and the reversal follows Pi:

    A stored credential owns the provider: ambient/env is consulted only when
    nothing is stored.
        — research/pi/packages/ai/src/auth/resolve.ts:44-46

The old order put the environment first, on the reasoning that an exported
variable is an explicit choice. It is — but so is typing `/login`, and it is the
*more recent* one. Under the old order a user with `ANTHROPIC_API_KEY` in a
`.env` could sign in with their Claude subscription, watch omega report success,
and have the token never used. Nothing was wrong and nothing said so.

"Owns" is stronger than "is checked first". A stored entry that exists but is
unusable — empty, or a `kind` nothing handles — resolves to **nothing**, not to
the environment. Pi's rule has that second half for a reason: a silent fallback
to a different credential is how you end up debugging the wrong key. `/logout`
is how you give the provider back to the environment, and it is the only way.

`.env` files are still loaded into the environment before anything reads a key
(`cli.py`, `load_environment()`), so an install that has never run `/login`
behaves exactly as it always did.

## The file is written atomically, and the reason is not theoretical

A crash between `open` and `write` leaves a zero-byte credentials file, which
reads as "logged out" on the next start — the failure is silent and looks like
the feature not working. Writing to a temporary file in the same directory and
`replace()`-ing it is one extra line and removes the state entirely.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from omega_agent.events import AssistantErrorEvent, AssistantMessageEvent
from omega_agent.types import AssistantMessage

#: Every provider omega can hold a credential for, in the order `/login` offers
#: them and `choose_provider` breaks ties.
#:
#: **Split from `ENV_VARS` when Codex arrived**, because that one dict had come
#: to mean three things at once: which providers exist, which environment
#: variable each maps to, and what `--provider` accepts. `openai-codex` is a
#: subscription sign-in with no API key and therefore no variable, so a fourth
#: entry with an empty string would have had `resolve()` calling
#: `os.environ.get("")` and the `/login` note testing a nameless variable.
PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "openai-codex")

#: The subset that an exported variable can authenticate, and the variable each
#: is conventionally exported as. A provider absent from here can only ever be
#: signed in to — which is exactly true of a ChatGPT subscription.
ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

#: Only value `kind` takes today. Stored from the first write rather than added
#: later: an OAuth entry needs somewhere to say it is one, and introducing the
#: field afterwards means migrating every file already on disk.
API_KEY = "apikey"

#: `0600` — owner read/write. `0700` on the directory, because a file mode says
#: nothing about who can list the directory it sits in.
FILE_MODE = stat.S_IRUSR | stat.S_IWUSR
DIRECTORY_MODE = stat.S_IRWXU


def credentials_path() -> Path:
    """`~/.omega/auth.json`, beside `sessions/`, `logs/` and `tui.json`."""
    return Path.home() / ".omega" / "auth.json"


def _read() -> dict[str, Any]:
    """Whatever is on disk, or an empty store.

    Every failure is the empty store: unreadable, absent, corrupt, or written by
    something that put a list where an object belongs. **None of those is worth
    refusing to start over** — the worst case is being asked to log in again,
    and the alternative is an agent that will not open because of a stray byte.
    """
    try:
        data = json.loads(credentials_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(store: dict[str, Any]) -> None:
    """Replace the file atomically, with the permissions set before the content.

    `chmod` on the temporary file rather than on the final one: between
    `replace()` and a later `chmod` there is a window where the credential is
    on disk world-readable. Narrow, and avoidable by doing it in this order.
    """
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, DIRECTORY_MODE)

    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".auth-")
    try:
        os.fchmod(descriptor, FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(store, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        # A failed write must not leave a `.auth-` file sitting in the config
        # directory — it would be readable, partial, and never cleaned up.
        Path(temporary).unlink(missing_ok=True)
        raise


def stored(provider: str) -> str | None:
    """The saved credential for one provider, ignoring the environment.

    An OAuth access token is returned the same way an API key is, because every
    caller wants the same thing: the string to authenticate with. Renewal is the
    business of whatever notices it has expired, not of the reader.
    """
    entry = _read().get(provider)
    if not isinstance(entry, dict):
        return None
    value = entry.get("key") if entry.get("kind") != OAUTH else entry.get("access")
    return value if isinstance(value, str) and value else None


def owns(provider: str) -> bool:
    """Whether `auth.json` holds an entry for this provider at all.

    Distinct from `stored()` returning a value: an entry can be present and
    unusable. That case still *owns* the provider — it just owns it with nothing
    — which is what stops a half-written credential from silently resolving to
    whatever happens to be exported.
    """
    return isinstance(_read().get(provider), dict)


def resolve(provider: str) -> str | None:
    """The key omega should use, or None if there isn't one.

    Stored first — see the module docstring. Returns None rather than raising,
    because "not logged in" is an ordinary state this whole module exists to make
    survivable.
    """
    if owns(provider):
        return stored(provider)
    variable = ENV_VARS.get(provider)
    if variable:
        return os.environ.get(variable) or None
    return None


def source(provider: str) -> str:
    """Where the key came from, for the startup facts. Never the key itself.

    Kept short because it sits in a fixed-width column: the first version
    returned `ANTHROPIC_API_KEY (environment)` and was clipped to
    `ANTHROPIC_API_KEY (enviro…`, which is longer *and* says less.
    """
    # **Mirrors `resolve` exactly, and must keep doing so.** This string goes in
    # the startup facts; a version that checked the environment first would say
    # "environment" while the request used `auth.json`, which is the same class
    # of mismatch as a gate that checks a key the provider never receives.
    if owns(provider):
        return "auth.json" if stored(provider) else "not signed in"
    variable = ENV_VARS.get(provider)
    if variable and os.environ.get(variable):
        return "environment"
    return "not signed in"


def save(provider: str, key: str) -> None:
    """Store a key, replacing any previous one for that provider."""
    if provider not in PROVIDERS:
        raise KeyError(provider)
    store = _read()
    store[provider] = {"kind": API_KEY, "key": key}
    _write(store)


#: An OAuth entry. Distinguished from an API key by `kind`, which is why that
#: field was written from the very first key — an entry that expires and an entry
#: that does not have to be told apart before anything decides whether to renew.
OAUTH = "oauth"


def save_oauth(
    provider: str,
    *,
    access: str,
    refresh: str,
    expires_in: int,
    account_id: str | None = None,
) -> None:
    """Store tokens from a completed sign-in.

    `expires_at` is absolute, not a duration: a duration is only meaningful at the
    moment it was issued, and this file outlives that moment by definition.

    `account_id` is optional because only one provider has one. A ChatGPT
    subscription request carries a `chatgpt-account-id` header alongside the
    bearer token, and the value is not returned by the token endpoint — it is a
    claim inside the access JWT. Storing it here means the adapter never has to
    decode a token to find out who it belongs to.
    """
    if provider not in PROVIDERS:
        raise KeyError(provider)
    store = _read()
    entry: dict[str, Any] = {
        "kind": OAUTH,
        "access": access,
        "refresh": refresh,
        "expires_at": (time.time() + expires_in) if expires_in else 0,
    }
    if account_id:
        entry["account_id"] = account_id
    store[provider] = entry
    _write(store)


def account_id(provider: str) -> str | None:
    """The stored `chatgpt-account-id`, when the provider has one."""
    entry = stored_oauth(provider)
    value = entry.get("account_id") if entry else None
    return value if isinstance(value, str) and value else None


def stored_oauth(provider: str) -> dict[str, Any] | None:
    """The whole OAuth entry, or None when the stored credential is a key.

    `stored()` deliberately flattens both kinds to "the string to authenticate
    with". Renewal needs what that discards — the refresh token and the expiry —
    so it gets its own reader rather than a second return value nobody else uses.
    """
    entry = _read().get(provider)
    if isinstance(entry, dict) and entry.get("kind") == OAUTH:
        return entry
    return None


def forget(provider: str) -> bool:
    """Remove a stored key. False when there was nothing to remove.

    The return value is the whole reason this is not `del`: `/logout` has to be
    able to say "there was nothing saved" rather than implying it removed
    something, because an exported variable will still be working afterwards.
    """
    store = _read()
    if provider not in store:
        return False
    del store[provider]
    _write(store)
    return True


def signed_in() -> tuple[str, ...]:
    """Providers with a usable credential, in `PROVIDERS` order."""
    return tuple(name for name in PROVIDERS if resolve(name))


def choose_provider(explicit: str | None, *, base_url: str | None = None) -> str | None:
    """Which provider to use when the user did not say.

    **This is what removed `--provider` from the everyday command line.** The
    flag used to default to `"anthropic"`, so `omega` always tried Anthropic and
    an OpenAI user had to type `--provider openai` every single time — a flag
    whose value was already sitting in their credentials file.

    The order, most explicit first:

    1. `--provider`, if given. An override stays an override.
    2. `--base-url`, which only means anything to the OpenAI wire format.
    3. The one provider that is signed in, when exactly one is.
    4. `PROVIDERS` order when several are — a tie needs a rule, and a documented
       arbitrary one beats asking a question nobody wants asked at startup.
    5. `None`, meaning signed in to nothing. Not an error: the caller shows the
       login prompt instead of guessing.
    """
    if explicit is not None:
        return explicit
    if base_url:
        return "openai"
    available = signed_in()
    if available:
        return available[0]
    return None


class LoginRequiredProvider:
    """A provider that cannot answer, and says why.

    ## Why this exists instead of an exit

    omega used to `sys.exit` on a missing key, before it had decided whether it
    was even going to need one — which is why `omega --sessions` failed with a
    key error while listing sessions that needed no provider at all.

    A UI cannot take that shape. A terminal app that refuses to open cannot tell
    you how to fix it and cannot let you fix it in place. So the app opens, the
    startup facts say you are not signed in, and the **first query** comes back
    with the instruction rather than the launch failing.

    Tau does exactly this (`tui/app.py:185-213`), and it is the direct answer to
    "block the query and tell them to log in".

    ## Why it satisfies `ModelProvider` by inheriting nothing

    `ModelProvider` is a `Protocol` (`provider.py:31`), so one method of the
    right shape is the whole contract. `FakeProvider` makes the same point.
    """

    def __init__(self, provider: str | None) -> None:
        #: None means "signed in to nothing at all", which is a different message
        #: from "signed in to something else": there is no provider to name, so
        #: the instruction has to offer the choice instead of assuming one.
        self.provider = provider

    @property
    def message(self) -> str:
        if self.provider is None:
            options = "  ".join(f"/login {name}" for name in PROVIDERS)
            return (
                "Not signed in.\n\n"
                f"Run /login to choose a provider, or one of:  {options}\n"
                "Or export an API key and restart.\n"
                "Nothing was sent, and nothing was charged."
            )
        variable = ENV_VARS.get(self.provider, "the provider's API key")
        return (
            f"Not signed in to {self.provider}.\n\n"
            f"Run /login to paste an API key, or export {variable} and restart.\n"
            "Nothing was sent, and nothing was charged."
        )

    async def stream_response(self, **_: Any) -> AsyncIterator[AssistantMessageEvent]:
        """Fail as data, not as an exception.

        The loop turns a terminal error event into a normal `agent_end` with a
        reason, so this reaches the screen through the same path every other
        failure does — no special case anywhere above.
        """
        yield AssistantErrorEvent(
            reason="error",
            error=AssistantMessage(
                model="", stop_reason="error", error_message=self.message
            ),
        )

    async def aclose(self) -> None:
        """Nothing to close. Present so callers need not special-case it."""
        return
