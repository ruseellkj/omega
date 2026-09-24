# omega

A terminal coding agent, built from scratch in layers.

Named after a physics letter, following [Pi](https://github.com/earendil-works/pi) and
[Tau](https://github.com/huggingface/tau) — the two MIT-licensed agents this is studied from.
Written independently, not forked.

**Currently at Tier 3 — "it survives a long task, and has a face."** 15,105 lines of source,
758 tests, all offline.

* **[`READING-ORDER.md`](READING-ORDER.md) — start here.** All 53 files in the order to read them,
  one line each, plus the questions to hold while reading.
* [`TIER-1.md`](TIER-1.md) — what the first tier does, and what it deliberately left out
* [`TIER-2.md`](TIER-2.md) — what this tier adds, and where each remaining gap plugs in at Tier 3
* [`TIER-3.md`](TIER-3.md) — every row filled: compaction, caching, a Textual TUI, branching,
  search tools, structured logging, subagents, images
* [`PRODUCT-BACKLOG.md`](PRODUCT-BACKLOG.md) — packaging, OAuth, extensions: what a product needs and a
  study project does not

Tier 1 proved the loop terminates. Tier 2 makes it safe to point at a real repository: it can be
interrupted without corrupting the conversation, it remembers across restarts, it asks before it
destroys anything, and its provider abstraction is no longer a claim but a measured result.

## Install it

```bash
curl -fsSL https://omega-coding-agent.vercel.app/install.sh | sh
omega
```

The script installs `uv` if it is missing, puts omega in an isolated environment,
verifies the command it just created, and tells you if the bin directory is not on your `PATH`. It
**never edits a shell rc file** — uv owns `PATH`, and a tool that appends to `~/.zshrc` is one you
cannot cleanly uninstall.

The distribution is **`omega-coding`** (`omega` is taken on PyPI by an unrelated games library); the
command stays `omega`. Until that name is published the installer pulls from git, which is one line
to change and means it can be tested today rather than after a release.

`omega --version` reports which one you have, and needs no credentials — that is what the installer
checks with.

## Run it from a clone

```bash
uv sync

uv run omega --fake                 # scripted responses — no key, no network, no credits
uv run omega                        # Anthropic; needs ANTHROPIC_API_KEY (see below)
uv run omega --provider openai      # OpenAI Chat Completions
uv run omega -c                     # continue the most recent session here
uv run omega --sessions             # list saved sessions and exit
```

**`omega` opens a terminal UI.** It starts on a wordmark and six facts — model, path, git branch,
approval mode, session and version — which scroll away as soon as you ask something.

**Leaving:** `ctrl+c` twice, or `ctrl+d` once. A single `ctrl+c` stops the turn in progress; on an
idle prompt it arms and says so, because `ctrl+c` is the reflex for "stop that" and a closed session
has no undo. `ctrl+q` is deliberately not an exit — Textual provides it by default, and a third way
out with no confirmation would undo the point of the other two.

**Copying:** selecting text copies it, and the status line says how much; `/config auto-copy off`
stops that. With something selected, `ctrl+c` copies it instead of stopping anything. `ctrl+v`
pastes from the system clipboard — `pbcopy` first, OSC 52 over SSH — and a long paste folds to
`[paste #1 +N lines]` in the prompt, while the model still gets every line.

`/` opens the command list, `↑`/`↓` walk back through what you typed, `ctrl+o` expands every tool
call at once (and shows the startup facts again when there are none yet), and a finished tool call
collapses to one line you can click open. While a turn runs, the line above the prompt spins and
says what is actually happening — `reading loop.py`, `running npm test`, `thinking` — not just
"working".

**A message sent mid-turn can be taken back.** Typing while the model works queues a correction for
the next request; `↑` on an empty prompt pulls the last one back into the box to edit. The queue was
write-only before, so a typo was final short of cancelling the whole turn.

**The splash shrinks.** Six facts in a bordered box are worth their rows before you have asked
anything and worth nothing afterwards, so the first question replaces them with a three-line header —
mascot, version, model, directory. `ctrl+o` brings the rest back.

Pasting more than one line collapses to `[pasted #1 +57 lines]` and expands again on send, so a
traceback keeps its newlines instead of losing everything after the first.

**Signing in.** `/login` stores a credential in `~/.omega/auth.json` (mode `0600`, in a `0700`
directory); `/logout` removes it. The order is **`auth.json` → exported variable → not signed in**,
following Pi: *"a stored credential owns the provider; ambient/env is consulted only when nothing is
stored"* (`resolve.ts:44-46`).

That order was the other way round until recently, and the reversal fixes a real trap: with a key in
`.env`, you could sign in with your Claude subscription, be told it worked, and have the token never
used. Nothing was wrong and nothing said so. "Owns" is the strong form — a stored entry that exists
but is unreadable resolves to *nothing*, never to the environment, because falling through silently
is how you debug the wrong key for an afternoon.

An install that has never run `/login` is unaffected: `.env` files and exported variables work
exactly as they always did. `/login` says when it has taken over from an exported variable, and
`/logout` says when it is handing control back.

With no credentials omega still starts: the startup facts say `not signed in`, and the first query
comes back telling you to run `/login` rather than the program refusing to open. `omega --sessions`
and `--resume` need no credentials at all. `omega -p` does, and exits non-zero without them.

**Signing in with an account.** `/login anthropic` offers two ways in: paste an API key, or open a
browser and sign in with a Claude Pro/Max subscription. The browser flow is authorization-code +
PKCE — a loopback server on `localhost:53692` catches the redirect, and the verifier never leaves
the process.

**It signs in as Claude Code, and you should know that before you use it.** Anthropic registers no
third-party OAuth apps, so the account option works by presenting Claude Code's client id — the
same one both references ship (`oauth_anthropic.py:32`, `anthropic.ts:29`). The cost is not only at
the login screen: Anthropic rejects the token unless the request also carries Claude Code's identity
headers *and* a system block reading `You are Claude Code, Anthropic's official CLI for Claude`, so
every OAuth turn opens by telling the model it is a different product. `src/omega_coding/oauth.py`
carries the full reasoning.

Holding a client id of your own? Put it in `~/.omega/oauth.json` and it replaces the built-in
entirely. The API-key path claims nothing and sends none of the above.

**OpenAI has no account option**, deliberately. Its subscription OAuth returns a token
`api.openai.com` rejects — it opens `chatgpt.com/backend-api/codex/responses`, a different wire
format needing its own adapter (1,054 lines in Tau's `openai_codex.py`). A sign-in that succeeds and
then cannot serve a request is worse than no sign-in.

`/theme` switches between `slate` (the default — greys, so a failed tool is the only saturated
thing on screen), `oxblood-dark`, `oxblood-light` and `high-contrast`.

Two other surfaces, both behind a flag:

```bash
uv run omega -p "what does loop.py do?"   # one shot: answer on stdout, exit — pipeable
echo "fix the failing test" | uv run omega -p
uv run omega --repl                       # the plain print/input prompt
```

`-p` keeps stdout for the answer and puts tool activity on stderr, so `omega -p "..." > out.txt`
captures the answer and nothing else. A non-interactive stream falls back to the REPL on its own —
Textual cannot run on a pipe, so `omega < script.txt` keeps working.

`--fake` is not a stub. It drives the entire agent — loop, harness, tools, streaming, approvals —
through `FakeProvider`, so you can watch the whole thing work without spending anything.

Useful flags: `--yes` approves tool calls automatically (it does **not** disable the
refuse-outright list), `--confine` refuses any path outside the working directory instead of
asking, `--no-save` skips writing a session, `--resume ID` reopens a specific one,
`--context-window` and `--compact-threshold` move the point compaction fires, and
`--base-url` points the OpenAI adapter at a local server:

```bash
uv run omega --provider openai --base-url http://localhost:11434/v1   # Ollama, free
```

Drop an `OMEGA.md` in the working directory and its contents are appended to the system prompt,
so project conventions stop being something you retype.

### Installing it, and where the key goes

```bash
uv tool install --editable /path/to/omega     # `omega` now works from any directory
```

`--editable` so the installed command always runs the current source — otherwise you are running a
snapshot and wondering why your changes did nothing.

The API key is searched for **outward from wherever you run omega**, nearest first:

```
./.env                  this project
../.env                 …and its parents, up to your home directory
~/.config/omega/.env    set it once, every project sees it
```

An exported variable beats every file, so `ANTHROPIC_API_KEY=… omega` is a one-off override. The
nearest file wins per variable, and further files fill in the rest — a project `.env` can override
just the key while inheriting everything else from your global one.

If the key is missing, omega prints the exact list of paths it searched rather than only saying it
is unset.

## In the conversation

Everything above is chosen before the conversation starts. Inside it, a leading `/` addresses the
program rather than the model, and a leading `!` addresses the shell:

```
/help                  list these commands
/login [provider]      sign in — a subscription in the browser, or an API key
/logout [provider]     remove a stored credential
/sessions              saved sessions for this project
/resume <id>           switch to another session; the terminal UI shows its conversation
/clear                 start a fresh session; the old one is kept on disk
/rewind [n]            go back before your last question; the old branch is kept
/compact [pct]         shrink the conversation now
/model [name|refresh]  switch model, keeping the conversation, or refresh the list
/cost                  tokens and spend so far
/context               how full the context window is
/theme [name]          change the colours (terminal UI only)
/config [setting]      settings such as auto-copy (terminal UI only)
/exit                  leave omega
!<command>             run a shell command — no model, no tokens
```

`/context` shows the total, then the breakdown — and says so when the window is a guess:

```
  ~1,695/1,000,000 tokens (0%)
    tools         1,220        ← widest slice first, because that is what you would trim
    messages        280
    system          195
    free        998,305
```

On a model omega has no entry for, the same command adds a line rather than presenting its
fallback as a fact:

```
  The window is a fallback, not this model's real figure - omega has no entry for it.
  Put the number in ~/.omega/models.json to fix it.
```

Tau shows the same three buckets in `/status` (`commands.py:443`) and Pi shows no breakdown at all.
Claude Code's seven buckets include skills, custom agents and memory files, none of which omega
has — four permanent zeroes are noise, so omega prints the three it can actually measure.

`/model` with no name lists what the active provider offers and marks the current one; with a name
it switches and the conversation carries straight over — the transcript lives on the harness and the
model is read at request time, so nothing is rebuilt. The **context window moves with it**, which is
the half that would otherwise fail on the *next* request rather than on the switch. A model
belonging to another provider is refused rather than sent.

### A model shipped today and omega's list predates it

You are not stuck, and you never were: `/model <name>` takes any name and sends it as typed. But
being *allowed* to type it is only half. omega also has to know how big it is, because that number
is what compaction budgets against — and a million-token model assumed to be 200k compacts at a
fifth of its capacity and throws away context nobody needed to lose.

So tell it. `~/.omega/models.json`:

```json
{
  "anthropic": [
    {"name": "claude-opus-6", "window": 1000000, "note": "shipped this morning"}
  ]
}
```

A provider key omega has an adapter for (`anthropic`, `openai`, `openai-codex`), a list of models,
`name` and `window` required, `note` optional. `window` is in **tokens**.

**Where the built-in windows come from.** Every figure in `models.py` was read from
[models.dev](https://models.dev)'s JSON API (`curl https://models.dev/api.json`) and parsed out of
the raw response, not summarised — the audit table with the exact key for each model is in that
file's docstring. Two rows were wrong before that audit: `claude-sonnet-5` and `claude-opus-5` both
shipped at 200,000 and are 1,000,000, so compaction had been firing at a fifth of the real
capacity.

**And you should not have to type it in at all.** `/model refresh` asks models.dev directly:

```
> /model refresh
  Asking models.dev…
    anthropic       14 models
    openai          43 models
    openai-codex    not on models.dev — built-ins stand

  New since omega's built-in list: claude-fable-5, claude-haiku-4-5, claude-opus-4-6 (+43 more)
  Cached in ~/.omega/models-cache.json.
  Anything you wrote in ~/.omega/models.json still wins.
```

So the model list has **three layers**, lowest precedence first:

| layer | where | who writes it |
|---|---|---|
| built-ins | `models.py` | compiled in — works offline, always |
| refreshed | `~/.omega/models-cache.json` | `/model refresh` |
| yours | `~/.omega/models.json` | you, by hand — **wins** |

Your hand-written figures beat a refresh, because a later refresh silently overruling a correction
you typed would undo your work without saying so. A model models.dev has never heard of is still
yours to describe.

The fetch is a command, never automatic. omega does not reach a third-party host during a turn you
asked for something else, and it never blocks startup — a fresh clone with no network has the full
built-in list immediately, which is the thing Pi's build-time generator gives up
(`pi/.gitignore:11` means a fresh Pi clone knows no models at all).

Four things worth knowing:

- **Your entries go on top of the built-ins, not instead of them.** Adding one Claude keeps the
  other five and puts yours first in the picker. Same name as a built-in means yours wins, once —
  which is how you correct a window omega ships wrongly.
- **The file is read on every call**, so an edit lands without restarting omega.
- **It adds models, not providers.** A provider needs an adapter in code; a model needs a name and
  a number. An unrecognised provider key is reported rather than honoured, because models in the
  picker for something nothing can send a request to is worse than the mistake being pointed out.
- **A malformed file is reported, not fatal and not silent.** One bad entry is skipped and named in
  `/model`'s output; the good entries still load, and the built-ins are never at risk.

If you skip the file entirely, `/model` says so plainly rather than printing its fallback as a
fact:

```
> /model claude-opus-7
  Now using claude-opus-7. The conversation is unchanged.
  Not in omega's list, so it is passed through as typed.
  omega does not know this model's window and is assuming 200,000 tokens,
  which is what compaction budgets against. Put the real figure in
  /Users/you/.omega/models.json to fix it:
    {"anthropic": [{"name": "claude-opus-7", "window": <tokens>}]}
```

This is Tau's design (`catalog_loader.py:229` merges the user's list over the built-in one as a
union, overlay first) rather than Pi's, which generates its catalog from `models.dev` into a
`.gitignore`d directory (`pi/.gitignore:11`) — a better pipeline for 721 models, and for omega's
twelve a Node build and a network dependency that leave a fresh clone unable to name a single one.

`exit` and `quit` still work without a slash. An unknown `/foo` is an error naming the nearest
match rather than a prompt forwarded to a paid API, which is where both references differ.

**`!cmd` has no private path to the shell.** It runs through the same `run_shell` tool the model
uses, so it meets the same approval gate, the same refuse-outright list, the same timeout and the
same output budget. Its output is *not* added to the conversation — Pi and Tau both add it, and
the argument for diverging is in `src/omega_coding/commands.py`.

## Check it

```bash
uv run pytest -q                    # 758 tests, ~45s, fully offline
uv run mypy --strict src
uv run ruff check .
uv run python -m omega_coding.evals # smoke eval: does the assembled agent still work?
```

The test suite never touches the network. Every provider call is faked at the interface boundary —
which is why `omega_ai/fake.py` was written before the real adapter — and the two vendor SDKs are
faked one layer lower, in `tests/stub_anthropic.py` and `tests/stub_openai.py`, so the adapters'
own retry and auth behaviour is testable too.

The **smoke eval** is deliberately not a test. The tests check units; the eval checks the
assembled agent against a task. It catches the class of breakage where every unit passes and the
whole thing still does nothing.

## Layout

Three packages, following Tau. The rule that decides which one a file belongs to is a single
question: **what does this thing know about?**

```
omega/src/
├── omega_ai/          ── L1 · one vendor's wire format, and nothing else ──
│   ├── provider.py       a 5-line re-export. The contract lives one layer down.
│   ├── fake.py           scripted replay. Written before the real adapter.
│   ├── retry.py          backoff — invisible above this layer
│   ├── anthropic.py      one wire format
│   └── openai.py         a different one. Also Groq, Together, Ollama, vLLM.
│
├── omega_agent/       ── L2 · messages, events, tools, turns ──
│   ├── types.py          the neutral message model
│   ├── events.py         the 12 stream events
│   ├── agent_events.py   the 10 agent events
│   ├── provider.py       THE CONTRACT. The consumer owns the interface.
│   ├── tools.py          Tool and ToolResult
│   ├── hooks.py          the six seams
│   ├── loop.py           190 lines, and it should not grow
│   ├── tool_runner.py    one tool call → one tool result
│   ├── harness.py        owns the transcript, the queues, cancellation
│   ├── cancellation.py   a token you can actually set
│   └── session/          append-only JSONL, parent_id on every entry
│
└── omega_coding/      ── L3 + L4 · files, shells, policy, the screen ──
    ├── paths.py          path resolution. ONE place, not per-tool.
    ├── file_lock.py      one lock per resolved path
    ├── truncate.py       2,000 lines / 50 KB, tail-biased
    ├── builtin_tools.py  read, write, edit, run
    ├── approval.py       the gate — fills before_tool_call
    ├── redact.py         keeps credentials out — fills after_tool_call
    ├── history.py        what is kept vs what is sent
    ├── context.py        how full the window is
    ├── cost.py           tokens; dollars only if you supply a price
    ├── system_prompt.py  the standing instructions, and OMEGA.md
    ├── commands.py       /help, /sessions, /clear … and the ! shell escape
    ├── status.py         the working line, with truthful labels
    ├── env.py            finds .env by walking outward from where you are
    ├── headless.py       prompt in, transcript out. No keyboard.
    ├── evals.py          the smoke eval
    └── cli.py            the composition root. Reads last.
```

`omega_agent/session/` is the only subfolder any of them earned — which is also true of Tau, whose
entire agent core has exactly one, and it is this one. Folders follow subsystems.

**Where a file goes, when it is ambiguous:** `hooks.py` is core because the loop *declares* the
callbacks it will consult. Everything that *fills* one — `approval.py`, `redact.py`, `history.py` —
is application, because each is a decision, and the loop asks rather than decides.

### The check that the layering held

It used to be a `grep` in this README. It is now a test:

```bash
uv run pytest tests/test_layers.py -q
```

which asserts that `omega_agent` imports nothing above it, that `omega_ai` does not know the app
exists, that only a composition root names a concrete provider, and that exactly two files import a
vendor SDK. It reads imports with `ast` rather than text, because all three packages *mention* each
other in their docstrings while importing none of them.

The stronger version of the same check is in the git history. Adding the second provider — a
genuinely different wire format, with the opposite rule about how tool results are sent — required
changes to the adapter package, its tests, and a few lines of provider selection in `cli.py`.
Nothing else moved: not the loop, not the interface, not either event vocabulary. `git show ac0e370`
has the full accounting.

## Why it's shaped this way

Every design decision traces to a document in [`../docs/`](../docs/) — in particular
`03-architecture/04-boundaries-and-layout.md` for the layer rules,
`03-architecture/02-beginner.md` for the nine failures each layer exists to fix, and
`01-teardown/` for where each pattern came from in Pi and Tau.

The nine-failure scorecard is the quickest way to see which tier this is:

| Failure | Fixed in |
|---|---|
| one big output kills the session · nothing appears until it finishes · no turn limit | Tier 1 |
| Ctrl-C corrupts the conversation · it deletes something you wanted · one rate limit ends the run · two edits lose data · no persistence | **Tier 2** |
| switching providers means a rewrite | Tier 1 built the seam · **Tier 2 proved it** |
| context fills up and dies · it costs more than it should | Tier 3 |
