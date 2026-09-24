"""Commands typed into the conversation: `/help`, `/sessions`, `!git status`.

Before this, omega recognised exactly two words — `exit` and `quit` — and sent
everything else to the model. So typing `clear` produced a paid explanation of
how to clear a *terminal*, and `--sessions` was reachable only by quitting the
conversation you wanted to look away from.

**The problem is a missing channel.** There was no way to tell the program
something rather than ask the model something. A leading `/` or `!` is that
channel, which is the answer both references reached.

## Where this differs from Pi and Tau, and why

**A registry, not an `if`-chain.** Tau keeps a `CommandRegistry` that refuses
duplicate names (`commands.py:152-204`); Pi hardcodes ~25 string comparisons in
one function and keeps a *separate* list purely to feed autocomplete
(`interactive-mode.ts:2725-2857`, `slash-commands.ts:13-17`) — two places to
update, and no error when they disagree. Tau's shape wins at six entries and
keeps winning at twenty.

**Handlers act directly.** Tau's return a `CommandResult` carrying ~22
declarative flags for a frontend to interpret, because Tau *has* two frontends
honouring different subsets — its print mode ignores most of them
(`cli.py:785-797`). omega has one frontend, so a flag object would be ceremony
with nothing on the other end.

**`/help`, `/clear` and `/sessions` exist here and in neither reference.** Both
have autocomplete and interactive pickers, so discovery is a keystroke and
listing is a modal. omega has no TUI, so each has to be a command you can type.
Tau's equivalent of `/clear` is `/new`; Pi's is also `/new`, though its handler
is still called `handleClearCommand`.

**An unknown `/foo` is an error.** Both references forward it to the model as an
ordinary prompt. With nine commands and a metered API on the other side, a typo
deserves a correction rather than an invoice.

**`!cmd` does not touch the conversation — and both references disagree.** In
Tau `!` adds output to context and `!!` hides it (`session.py:2529-2542`); Pi is
the same (`interactive-mode.ts:2861-2877`). The surprising half of that is a
shell command silently enlarging every later request, so the plain form here is
the free one. A context-adding variant can arrive later as `!!`, inverted from
both — recorded in `TIER-2.md` rather than hidden.

One thing copied exactly: a bare `!` with nothing after it goes to the model, as
in Pi (`interactive-mode.ts:2865`).

**There is no second path to the shell.** `!cmd` runs through the same
`run_shell` tool the model uses, so it meets the same approval gate, the same
refuse-outright list, the same timeout and the same output budget. A private
shell here would be a second place for a deny list to be correct, which is the
mistake `paths.py` spends a docstring warning about.
"""

from __future__ import annotations

import difflib
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Literal

from omega_agent.harness import Harness
from omega_agent.hooks import AgentHooks
from omega_agent.session import SessionStore
from omega_agent.tool_runner import execute_tool_call
from omega_agent.tools import Tool
from omega_agent.types import ToolCall
from omega_coding import auth, models, oauth
from omega_coding.compact import Compactor
from omega_coding.context import measure
from omega_coding.cost import CostTracker

#: What the REPL should do next. `None` from `dispatch` is the third case, and the
#: important one: *this was not a command, send it to the model*.
Outcome = Literal["handled", "exit"]

#: The prefix meaning "run this in a shell, do not involve the model".
SHELL_PREFIX = "!"


def _to_stdout(line: str) -> None:
    """The default sink: where commands went before there was anywhere else.

    A named function rather than `print` itself, because `print` takes `*args`
    and a handful of keyword arguments — assigning it as the default would type
    the field as that whole signature and stop `--strict` from catching a
    one-argument call site that passes two.
    """
    print(line)


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Everything a handler is allowed to touch.

    Passed rather than reached for, so the surface a command can affect is one
    readable list instead of whatever it happens to be able to import.
    """

    harness: Harness
    store: SessionStore | None
    tracker: CostTracker
    model: str
    system: str
    tools: list[Tool]
    #: Carried so `!cmd` runs the *same* path the model does. Without it the
    #: escape would call `tool.execute` directly and skip the approval gate
    #: entirely — see `run_shell_escape`, where that was a real bug.
    hooks: AgentHooks
    #: Filled by `cli.py`. None when compaction is not wired, which is every
    #: caller that builds a context without one - `/compact` says so rather than
    #: pretending to work.
    compactor: Compactor | None = None
    args: str = ""

    #: Where a handler's output goes. **This is what made `/` reachable from the
    #: TUI.** Every handler used to call `print()`, which writes to a stdout the
    #: terminal UI has taken over — so the commands worked, invisibly, and the
    #: app simply never called `dispatch`.
    #:
    #: A sink rather than a return value, because handlers emit several lines at
    #: different points (`/sessions` prints a header then a row per session) and
    #: threading that through `Outcome` would change every signature to spare one
    #: field. The REPL passes `print`; the TUI appends a notice row.
    emit: Callable[[str], None] = _to_stdout

    #: How a command asks for something secret. **The same seam the approval
    #: gate uses** (`approval.py:96`, `Asker`): the policy never knew whether it
    #: was talking to stdin or a modal, and neither does `/login`. The TUI hands
    #: over a masked `ModalScreen`; the REPL hands over `getpass`.
    #:
    #: Takes the prompt to show, returns the secret or None for "cancelled".
    #: **None when unset**, which means refusal rather than a crash — the same
    #: fail-safe shape `ApprovalPolicy` uses when it has no asker at all.
    ask_secret: Callable[[str], Awaitable[str | None]] | None = None

    #: How a command asks the user to pick from a short list. Same seam as
    #: `ask_secret`, one step earlier: `/login` with no argument has to find out
    #: *which* provider before it can ask for a key.
    #:
    #: Takes the question and the options, returns the choice or None.
    ask_choice: Callable[[str, list[str]], Awaitable[str | None]] | None = None

    #: Rebuild the provider after credentials change, returning whether the new
    #: one can actually answer. Supplied by `cli.py`, which closes over its own
    #: construction — so `/login` can take effect without a restart and without
    #: this file ever naming a provider.
    reload_provider: Callable[[], bool] | None = None

    #: Which provider is active. `/model` switches *within* one, so it has to
    #: know which list to offer — and has to refuse a name belonging to another,
    #: which would otherwise look like it worked and then fail on the next turn.
    provider: str = ""

    #: Change the model for the rest of the session. A callback rather than a
    #: field because three snapshots have to move together and only `cli.py`
    #: holds all three: `harness.model` (what is actually sent), the display
    #: model, and `compactor.window` (what compaction budgets against). Setting
    #: any one of them alone is a switch that half-happens.
    set_model: Callable[[str], None] | None = None


CommandHandler = Callable[[CommandContext], Awaitable[Outcome]]


@dataclass(frozen=True, slots=True)
class Command:
    """One command. `usage` is separate from `summary` so `/help` can align them."""

    name: str
    usage: str
    summary: str
    run: CommandHandler


# ----------------------------------------------------------------- handlers


async def _help(context: CommandContext) -> Outcome:
    width = max(len(command.usage) for command in COMMANDS)
    context.emit("\n  Commands - everything else goes to the model.\n")
    for command in COMMANDS:
        context.emit(f"    {command.usage:<{width}}  {command.summary}")
    context.emit(f"    {'!<command>':<{width}}  run a shell command; no model, no tokens")
    context.emit("")
    return "handled"


async def _sessions(context: CommandContext) -> Outcome:
    """Saved sessions for this project, newest first.

    Reads every transcript, which is the honest cost of having no index. Measured
    at 0.28 ms per session, so a hundred of them is under 30 ms. Tau keeps an
    index because its picker re-filters on every keystroke; this does not.
    """
    if context.store is None:
        context.emit("\n  Sessions are not being saved (--no-save).\n")
        return "handled"

    rows = context.store.list_sessions()
    if not rows:
        context.emit("\n  No saved sessions for this project yet.\n")
        return "handled"

    context.emit("")
    for row in rows:
        when = row.modified.astimezone().strftime("%Y-%m-%d %H:%M")
        current = "  (current)" if row.session_id == context.harness.session_id else ""
        summary = row.first_prompt or "(no prompt recorded)"
        context.emit(f"    {row.session_id}  {when}  {row.messages:>4} msgs  {summary}{current}")
    context.emit("\n  Switch with /resume <id>.\n")
    return "handled"


async def _resume(context: CommandContext) -> Outcome:
    """Swap the live conversation for a stored one, without restarting omega."""
    session_id = context.args.strip()
    if not session_id:
        context.emit("\n  Usage: /resume <id>. Run /sessions to see what is saved.\n")
        return "handled"
    if context.store is None:
        context.emit(
            "\n  Sessions are not being saved (--no-save), so there is nothing to resume.\n"
        )
        return "handled"
    if not context.store.load(session_id):
        context.emit(f"\n  No session {session_id} for this project. Try /sessions.\n")
        return "handled"

    restored = context.harness.resume(session_id)
    context.emit(f"\n  Resumed {session_id} ({restored} messages).\n")
    return "handled"


async def _clear(context: CommandContext) -> Outcome:
    """Start a fresh conversation. The old one stays on disk."""
    messages = len(context.harness.messages)
    previous = context.harness.start_new_session()

    if previous is None:
        context.emit("\n  Cleared. Nothing had been saved yet.\n")
    else:
        # Naming the id is the point: it is what makes "kept, not deleted"
        # checkable rather than a promise.
        context.emit(f"\n  Cleared. {previous} kept ({messages} messages) - reopen with /resume.\n")
    return "handled"


async def _cost(context: CommandContext) -> Outcome:
    tracker = context.tracker
    context.emit(f"\n  {tracker}   across {tracker.turns} model response(s)")

    # Reported separately from the totals rather than folded into them: cache
    # reads are billed at a different rate, and omega ships no price table, so
    # quietly discounting them would be inventing the number this module exists
    # to refuse to invent.
    if tracker.cache_read_tokens or tracker.cache_write_tokens:
        context.emit(
            f"  cache: {tracker.cache_read_tokens:,} read, "
            f"{tracker.cache_write_tokens:,} written "
            f"({tracker.cached_fraction:.0%} of input served from cache)"
        )
    else:
        context.emit(
            "  cache: nothing yet - the first turn of a session always writes before it reads."
        )

    if tracker.dollars is None:
        context.emit(
            "  No price set - export OMEGA_PRICE_INPUT and OMEGA_PRICE_OUTPUT for a total."
        )
    context.emit("")
    return "handled"


async def _context(context: CommandContext) -> Outcome:
    usage = measure(
        # **The live model, not `context.model`.** The snapshot is frozen when
        # the context is built at startup, so reading it made /context go on
        # reporting the window omega *launched* with after a /model switch —
        # a number disagreeing with the budget compaction was actually using,
        # on the very line someone reads to decide whether to compact.
        # `_model` has the same note at its own `harness.model` read.
        model=context.harness.model,
        system=context.system,
        messages=context.harness.messages,
        tools=context.tools,
    )
    context.emit(f"\n  {usage}")
    # The breakdown, because "how full" without "full of what" tells you a
    # threshold is near and nothing about which lever moves it.
    for line in usage.parts():
        context.emit(f"    {line}")
    note = usage.window_note()
    if note is not None:
        context.emit(f"\n  {note}")
    context.emit(
        "\n  An estimate - characters/4, tool schemas included. Never exact, never wildly low.\n"
    )
    return "handled"


async def _compact(context: CommandContext) -> Outcome:
    """Shrink the conversation now, instead of waiting for the ceiling.

    **Why this exists when compaction is already automatic.** The automatic pass
    fires at 80% — mid-task, whenever the next request happens to cross it. This
    is the "I am about to start something big, clear the decks first" button, and
    it takes a target: `/compact 40` aims at 40% of the window rather than 80%.

    Both references have the same pair (Pi `slash-commands.ts:38`, Tau
    `commands.py:230`), and both take an argument — theirs is free text steering a
    model-written summary. omega's compaction is mechanical, so there is nothing
    to instruct; a percentage is the parameter that means something here, and the
    divergence is deliberate.

    Unlike the automatic pass, this one edits the **transcript**, not just the
    request. Nothing is lost: the session file is append-only and still holds
    every original message.
    """
    compactor = context.compactor
    if compactor is None:
        context.emit("\n  Compaction is not configured for this session.\n")
        return "handled"

    fraction = compactor.threshold
    target = context.args.strip().rstrip("%")
    if target:
        try:
            percent = float(target)
        except ValueError:
            context.emit(f"\n  Usage: /compact [percent]. '{target}' is not a number.\n")
            return "handled"
        if not 1 <= percent <= 100:
            context.emit("\n  Usage: /compact [percent], between 1 and 100.\n")
            return "handled"
        fraction = percent / 100

    messages = context.harness.messages
    before_messages, before_tokens = len(messages), compactor.estimate(messages)

    compacted = compactor.compact_to(messages, fraction)
    after_tokens = compactor.estimate(compacted)

    if after_tokens >= before_tokens:
        # Truthful rather than encouraging. A command that always claims success
        # teaches you to stop reading it.
        context.emit(f"\n  Already at ~{before_tokens:,} tokens - nothing worth dropping.\n")
        return "handled"

    context.harness.replace_transcript(compacted)
    freed = before_tokens - after_tokens
    context.emit(
        f"\n  Compacted to {int(fraction * 100)}%: "
        f"{before_messages} messages (~{before_tokens:,} tokens) "
        f"-> {len(compacted)} (~{after_tokens:,}), {freed:,} freed."
    )
    context.emit("  The session file still has everything - /resume reopens the full history.\n")
    return "handled"


async def _rewind(context: CommandContext) -> Outcome:
    """Go back before the last question and try a different one.

    **Nothing is deleted.** The abandoned turns stay in the session file; the
    next one simply hangs off an older parent, so the old attempt is still
    loadable. That is what `parent_id` on every entry has been for since Tier 2,
    and why an append-only file can still be rewound.

    Counted in questions rather than messages, because one question can produce a
    dozen messages and nobody knows how many.
    """
    target = context.args.strip()
    questions = 1
    if target:
        try:
            questions = int(target)
        except ValueError:
            context.emit(f"\n  Usage: /rewind [questions]. '{target}' is not a number.\n")
            return "handled"
        if questions < 1:
            context.emit("\n  Usage: /rewind [questions], at least 1.\n")
            return "handled"

    removed = context.harness.rewind(questions)
    if not removed:
        context.emit("\n  Nothing to rewind - the conversation is empty.\n")
        return "handled"

    context.emit(f"\n  Rewound {questions} question(s): {removed} messages dropped.")
    context.emit("  The old branch is still in the session file - /sessions still lists it.\n")
    return "handled"


async def _login(context: CommandContext) -> Outcome:
    """Sign in to a provider, and start using it immediately.

    **Two ways in, and they are not equally clean.** An API key is a secret the
    user already owns, so pasting one asks nobody's permission. An account
    sign-in needs a client id, and Anthropic issues none to third parties — so
    the account option works by presenting Claude Code's, exactly as both
    references do (`research/tau/src/tau_coding/oauth_anthropic.py:32`, and the
    same id base64-encoded in
    `research/pi/packages/ai/src/auth/oauth/anthropic.ts:29`).

    omega offered keys only until that trade was made explicitly. It is made now;
    `omega_coding/oauth.py` carries the reasoning and what it commits every
    install to, and the menu below is the place a user meets it.
    """
    provider = context.args.strip().lower()

    if not provider:
        # **No default.** `/login` used to assume Anthropic, which is the same
        # mistake `--provider` made: picking for the user and being wrong half
        # the time. Asking costs one keypress and is always right.
        if context.ask_choice is None:
            options = "  ".join(f"/login {name}" for name in auth.PROVIDERS)
            context.emit(f"\n  Which provider?  {options}\n")
            return "handled"
        picked = await context.ask_choice("Sign in to which provider?", list(auth.PROVIDERS))
        if not picked:
            context.emit("\n  Cancelled. Nothing was stored.\n")
            return "handled"
        provider = picked

    if provider not in auth.PROVIDERS:
        known = ", ".join(auth.PROVIDERS)
        context.emit(f"\n  No provider called {provider!r}. Known: {known}.\n")
        return "handled"

    # Three shapes, and the menu has to reflect which one this provider is:
    #
    #   anthropic     both - an account sign-in or an API key
    #   openai        key only - it has no account registration
    #   openai-codex  account only - a ChatGPT subscription *is* the credential,
    #                 and there is no key that reaches the same endpoint
    #
    # Offering a key paste for `openai-codex` would store something no adapter
    # could ever use, which is the failure this whole menu exists to avoid.
    registrations = oauth.load_registrations()
    takes_a_key = provider in auth.ENV_VARS

    if provider in registrations and takes_a_key and context.ask_choice is not None:
        method = await context.ask_choice(
            f"How do you want to sign in to {provider}?",
            ["account (opens a browser)", "API key"],
        )
        if method is None:
            context.emit("\n  Cancelled. Nothing was stored.\n")
            return "handled"
        if method.startswith("account"):
            return await _login_with_account(context, registrations[provider])

    if provider in registrations and not takes_a_key:
        return await _login_with_account(context, registrations[provider])

    if not takes_a_key:
        context.emit(
            f"\n  {provider} can only be signed in to with an account, and no"
            "\n  registration for it is available.\n"
        )
        return "handled"

    if context.ask_secret is None:
        context.emit(
            "\n  /login needs somewhere to type a key, and this frontend has not"
            f"\n  provided one. Export {auth.ENV_VARS[provider]} instead.\n"
        )
        return "handled"

    key = await context.ask_secret(f"Paste your {provider} API key")
    if not key:
        context.emit("\n  Cancelled. Nothing was stored.\n")
        return "handled"

    auth.save(provider, key.strip())
    working = context.reload_provider() if context.reload_provider else False
    context.emit(
        f"\n  Signed in to {provider}. Key stored in ~/.omega/auth.json (0600)."
        + ("\n" if working else "\n  Restart omega for it to take effect.\n")
    )
    _note_if_overriding(context, provider)
    return "handled"


async def _login_with_account(
    context: CommandContext, registration: oauth.Registration
) -> Outcome:
    """The browser half. Returns once the provider has redirected back, or not.

    Failure is reported rather than raised: a closed tab, a denied consent and a
    timeout are all ordinary, and each has to read the same way a cancelled key
    paste does.
    """
    context.emit(
        f"\n  Opening your browser to sign in to {registration.provider}."
        f"\n  Listening on {registration.redirect_uri} — nothing else can reach it.\n"
    )
    tokens = await oauth.sign_in(registration)
    if not tokens or not tokens.get("access_token"):
        context.emit("  Sign-in did not complete. Nothing was stored.\n")
        return "handled"

    access = str(tokens["access_token"])

    # **A missing account id fails the sign-in rather than storing a half
    # credential.** ChatGPT rejects every request without the header, so a
    # credential lacking it would look signed in and work for nothing — the exact
    # failure the provider menu is shaped to prevent.
    account = oauth.account_id_from_token(registration, access)
    if registration.account_claim and account is None:
        context.emit("  Signed in, but the account id was missing. Nothing stored.\n")
        return "handled"

    auth.save_oauth(
        registration.provider,
        access=access,
        refresh=str(tokens.get("refresh_token") or ""),
        expires_in=int(tokens.get("expires_in") or 0),
        account_id=account,
    )
    working = context.reload_provider() if context.reload_provider else False
    context.emit(
        f"  Signed in to {registration.provider} with your account."
        + ("\n" if working else "\n  Restart omega for it to take effect.\n")
    )
    _note_if_overriding(context, registration.provider)
    return "handled"


def _note_if_overriding(context: CommandContext, provider: str) -> None:
    """Say so when this sign-in has just taken over from an exported variable.

    This began as the opposite warning — that an exported key would win and the
    sign-in would be stored and never read. Flipping the resolution order turned
    it inside out: the sign-in now wins, which is the behaviour people expect and
    still worth stating, because the exported key silently stops being used and
    nothing else on screen would say so.

    `/logout` carries the other half: it hands the provider back.
    """
    variable = auth.ENV_VARS.get(provider)
    if variable and os.environ.get(variable):
        context.emit(
            f"\n  Note: {variable} is also set. omega will use what you just"
            "\n  signed in with — /logout switches back to the variable.\n"
        )


async def _logout(context: CommandContext) -> Outcome:
    """Remove a stored key — and say plainly what happens next.

    Both references carry a caveat here because without one `/logout` looks
    broken: remove the stored key while `ANTHROPIC_API_KEY` is exported and omega
    keeps working, which reads as the command having done nothing.

    **The caveat got stronger when the resolution order flipped.** It used to say
    the environment was "left alone", which was true and understated: a stored
    credential now owns the provider, so an exported key is not merely surviving
    in the background — it was being ignored, and `/logout` is what switches to
    it. Saying "left alone" would describe a no-op where there is a handover.
    """
    provider = context.args.strip().lower()
    if not provider:
        # Only one thing signed in? Then there is nothing to ask about.
        current = auth.signed_in()
        if len(current) == 1:
            provider = current[0]
        elif not current:
            context.emit("\n  Nothing stored for any provider.\n")
            return "handled"
        else:
            options = "  ".join(f"/logout {name}" for name in current)
            context.emit(f"\n  Signed in to several. Which?  {options}\n")
            return "handled"

    if provider not in auth.PROVIDERS:
        known = ", ".join(auth.PROVIDERS)
        context.emit(f"\n  No provider called {provider!r}. Known: {known}.\n")
        return "handled"

    removed = auth.forget(provider)
    # `""` for a provider with no environment variable — `openai-codex` is a
    # subscription, so there is nothing for /logout to hand control back *to*,
    # and the note below correctly stays silent.
    variable = auth.ENV_VARS.get(provider, "")
    if not removed:
        context.emit(f"\n  Nothing stored for {provider}.")
    else:
        context.emit(f"\n  Removed the stored key for {provider}.")
        if context.reload_provider:
            context.reload_provider()

    if variable and os.environ.get(variable):
        context.emit(
            f"  {variable} is set, and omega will now use it. While you were"
            "\n  signed in it was ignored — a stored credential owns the provider."
            "\n  /logout hands it back. To use nothing at all, unset the variable"
            "\n  too; /logout only removes what /login saved.\n"
        )
    else:
        context.emit("")
    return "handled"


async def _theme(context: CommandContext) -> Outcome:
    """Listed here so `/help` and the palette know it exists; handled in the UI.

    **The one command the REPL cannot run.** Everything else in this file acts on
    the session, which both surfaces share. A theme acts on the screen, and the
    print REPL does not have one — so rather than pretend, this says where the
    command works. `tui/app.py:_switch_theme` is the real implementation.
    """
    context.emit(
        "\n  /theme changes the terminal UI's colours, and this is the print REPL."
        "\n  Run omega without --repl to use it.\n"
    )
    return "handled"


async def _config(context: CommandContext) -> Outcome:
    """Listed for `/help` and the palette, handled in the UI, like `/theme`.

    Its one setting, auto-copy, is about mouse selection, and the print REPL
    leaves selection to the terminal, which already copies. So there is nothing
    for it to configure here. `tui/app.py:_configure` is the real one.
    """
    context.emit(
        "\n  /config holds the terminal UI's settings, and this is the print REPL."
        "\n  Run omega without --repl to use it.\n"
    )
    return "handled"


async def _exit(context: CommandContext) -> Outcome:
    return "exit"


async def _model(context: CommandContext) -> Outcome:
    """Switch model without losing the conversation.

    **The conversation carries over for free**, and it is worth saying why
    rather than claiming it: the transcript lives on the harness, and the model
    is a separate attribute read at request time (`harness.py:78,413`). Measured
    — two turns either side of a switch, the provider received both names and
    the transcript grew 2 → 4. Nothing is rebuilt, so nothing can be lost.

    **What does not carry over for free is the window.** Compaction budgets
    against a number fixed when the `Compactor` was built, so switching from a
    million-token model to a 200k one would leave the transcript instantly over
    budget — and the failure would land on the *next* request, looking unrelated
    to the switch that caused it. `set_model` moves all three snapshots together.

    Tau's shape, which is the same one (`commands.py:_model_command`): an
    argument switches directly, no argument opens a picker.
    """
    if context.set_model is None:
        context.emit("\n  This frontend cannot switch models.\n")
        return "handled"

    # **The live value, not `context.model`.** The context is frozen and built
    # once at startup, so its copy is the model omega *started* with — reading it
    # would make a second /model in one session compare against the wrong name.
    current = context.harness.model
    wanted = context.args.strip()

    # **`refresh` is a reserved word here, and the collision is deliberate
    # rather than overlooked.** A provider could in principle ship a model
    # literally called "refresh"; none has, and if one does the picker
    # (`/model` with no argument) still reaches it. The alternative - a separate
    # `/refresh-models` command - puts the one thing that fixes a stale model
    # list somewhere nobody looking at models would find it.
    if wanted == "refresh":
        return await _model_refresh(context)

    catalog = models.models_for(context.provider)

    if not wanted:
        # Said before the list, not after: a file that did not load is the
        # reason a model someone just added is missing from it.
        trouble = models.overlay_problem()
        if trouble is not None:
            context.emit(f"\n  Ignored part of your model list — {trouble}")
        if not catalog:
            context.emit(
                f"\n  No model list for {context.provider or 'this provider'}."
                "\n  Name one directly:  /model <name>\n"
            )
            return "handled"
        if context.ask_choice is None:
            context.emit("\n  Models for " + context.provider + ":\n")
            for entry in catalog:
                mark = "*" if entry.name == current else " "
                note = f"  — {entry.note}" if entry.note else ""
                context.emit(f"   {mark} {entry.name}{note}")
            context.emit("\n  Switch with:  /model <name>\n")
            return "handled"

        picked = await context.ask_choice(
            f"Which {context.provider} model?",
            [_labelled(entry, current=current) for entry in catalog],
        )
        if picked is None:
            context.emit("\n  Cancelled. Still on " + current + ".\n")
            return "handled"
        wanted = picked.split()[0].lstrip("*").strip() or picked

    if wanted == current:
        context.emit(f"\n  Already on {wanted}.\n")
        return "handled"

    # **A model from another provider is refused, not switched to.** Sending
    # `gpt-5` to Anthropic is a 404 on the next turn, by which point the /model
    # that caused it is three screens back.
    owner = models.provider_of(wanted)
    if owner is not None and context.provider and owner != context.provider:
        context.emit(
            f"\n  {wanted} is a {owner} model and you are signed in to"
            f" {context.provider}."
            f"\n  Switch provider with /login {owner} first.\n"
        )
        return "handled"

    context.set_model(wanted)

    known = any(entry.name == wanted for entry in catalog)
    context.emit(f"\n  Now using {wanted}. The conversation is unchanged.")
    if not known:
        # Deliberately permissive - see `models.py`. A hard-coded list that
        # refuses a model released last week is worse than no list.
        context.emit("  Not in omega's list, so it is passed through as typed.")

    window = models.window_for(wanted)
    if models.window_is_guess(wanted):
        # **The number is a fallback, so it does not get to look like a fact.**
        # Printing a flat "400,000 tokens" for a model nothing recognises is how
        # a million-token model ends up compacting at a fifth of its capacity
        # with nothing on screen to explain why.
        # The placeholder is deliberately **not** valid JSON. Printing
        # `"window": 200000` here would invite pasting the guess back in, which
        # silences the warning without correcting anything - the same wrong
        # budget, now with nothing left to reveal it.
        context.emit(
            f"  omega does not know this model's window and is assuming"
            f" {window:,} tokens,"
            f"\n  which is what compaction budgets against. Put the real figure in"
            f"\n  {models.overlay_path()} to fix it:"
            f'\n    {{"{context.provider}": [{{"name": "{wanted}",'
            f' "window": <tokens>}}]}}\n'
        )
    else:
        context.emit(f"  Context window: {window:,} tokens.\n")
    return "handled"


async def _model_refresh(context: CommandContext) -> Outcome:
    """`/model refresh` — ask models.dev what exists and cache the answer.

    **Explicitly invoked, never automatic.** The tempting version fetches on its
    own the moment someone names a model omega does not know, which is exactly
    when the data would help. It is still wrong: that is an unannounced request
    to a third-party host in the middle of a turn the user asked for something
    else. A command is one keystroke more and no surprises.
    """
    context.emit("\n  Asking models.dev…")
    report = await models.refresh_from_models_dev()

    if report.error is not None:
        context.emit(f"  {report.error}")
        context.emit("  Your built-in list is untouched, so nothing is worse than before.\n")
        return "handled"

    for provider, count in report.counts.items():
        context.emit(f"    {provider:<14} {count:>3} models")
    for name in report.uncovered:
        context.emit(f"    {name:<14}  not on models.dev — built-ins stand")

    if report.added:
        shown = ", ".join(report.added[:6])
        more = f" (+{len(report.added) - 6} more)" if len(report.added) > 6 else ""
        context.emit(f"\n  New since omega's built-in list: {shown}{more}")
    else:
        context.emit("\n  Nothing models.dev lists is new to omega.")

    context.emit(f"  Cached in {report.written}.")
    # Said because the precedence is the one thing about this that could
    # surprise someone: a refresh does not undo their hand-written figures.
    context.emit(f"  Anything you wrote in {models.overlay_path()} still wins.\n")
    return "handled"


def _labelled(entry: models.Model, *, current: str) -> str:
    mark = "* " if entry.name == current else "  "
    return f"{mark}{entry.name}" + (f"  — {entry.note}" if entry.note else "")


COMMANDS: tuple[Command, ...] = (
    Command("help", "/help", "list these commands", _help),
    Command("sessions", "/sessions", "saved sessions for this project", _sessions),
    Command("resume", "/resume <id>", "switch to another session", _resume),
    Command("clear", "/clear", "start a fresh session; the old one is kept", _clear),
    Command("cost", "/cost", "tokens and spend so far", _cost),
    Command("context", "/context", "how full the context window is", _context),
    Command("compact", "/compact [pct]", "shrink the conversation now", _compact),
    Command("rewind", "/rewind [n]", "go back before your last question", _rewind),
    Command("theme", "/theme [name]", "change the colours (terminal UI only)", _theme),
    Command(
        "config",
        "/config [setting]",
        "settings such as auto-copy (terminal UI only)",
        _config,
    ),
    Command(
        "model",
        "/model [name|refresh]",
        "switch model, or refresh the list from models.dev",
        _model,
    ),
    Command("login", "/login [provider]", "sign in to a provider", _login),
    Command("logout", "/logout [provider]", "remove a stored credential", _logout),
    Command("exit", "/exit", "leave omega", _exit),
)

_BY_NAME: dict[str, Command] = {command.name: command for command in COMMANDS}


# ----------------------------------------------------------------- dispatch


async def run_shell_escape(command: str, context: CommandContext) -> Outcome:
    """Run `command` the way the model would, and print what came back.

    **Through `execute_tool_call`, not `tool.execute`.** That distinction was a
    real bug: the approval gate is not inside the tool, it is a `before_tool_call`
    hook consulted by `tool_runner`. Calling the tool directly ran the command
    with no gate at all — `!rm -rf /` reached the shell and was stopped only by
    the operating system, which is not a safety property omega can claim.

    Going through the runner inherits the whole path instead: the gate, the
    refuse-outright list, redaction on the way back, and the guarantee that
    nothing raises. Reaching for `subprocess` here would have meant a second
    place for a deny list to be correct, which is the mistake `paths.py` spends a
    docstring warning about.
    """
    by_name = {tool.name: tool for tool in context.tools}
    if "run_shell" not in by_name:
        context.emit("\n  No shell tool is available in this session.\n")
        return "handled"

    result = await execute_tool_call(
        ToolCall(id="escape", name="run_shell", arguments={"command": command}),
        by_name,
        context.hooks,
        context.harness.signal,
    )

    # A refusal, a deny-list block and a non-zero exit all arrive as ordinary
    # results with `is_error` set. Every one of them is something to read.
    context.emit(f"\n{result.text}\n")
    return "handled"


async def dispatch(text: str, context: CommandContext) -> Outcome | None:
    """Handle `text` if it is a command. `None` means "send it to the model".

    Three outcomes rather than a boolean, because "handled" and "not a command"
    are genuinely different from "handled, and now quit".
    """
    stripped = text.strip()

    if stripped.startswith(SHELL_PREFIX):
        command = stripped[len(SHELL_PREFIX) :].strip()
        # A bare `!` is not a shell command. Pi treats it as ordinary text and so
        # does this: passing it on is a better guess than refusing it.
        if command:
            return await run_shell_escape(command, context)
        return None

    if not stripped.startswith("/"):
        # `exit` and `quit` predate the slash commands and stay. They are what
        # people type, and breaking that for consistency helps nobody.
        if stripped.lower() in {"exit", "quit"}:
            return "exit"
        return None

    name, _, args = stripped[1:].partition(" ")
    command_entry = _BY_NAME.get(name.lower())
    if command_entry is None:
        _unknown(name, context.emit)
        return "handled"

    return await command_entry.run(replace(context, args=args))


def _unknown(name: str, emit: Callable[[str], None]) -> None:
    """Say so, and guess what was meant.

    Both references forward an unknown `/foo` to the model. With nine commands and
    a metered API on the other side, a typo is worth a correction rather than a
    charge.
    """
    close = difflib.get_close_matches(name.lower(), _BY_NAME, n=1, cutoff=0.6)
    suggestion = f" Did you mean /{close[0]}?" if close else ""
    emit(f"\n  Unknown command /{name}.{suggestion} Try /help.\n")
