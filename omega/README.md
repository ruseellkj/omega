# omega

A terminal coding agent, built from scratch in layers.

Named after a physics letter, following [Pi](https://github.com/earendil-works/pi) and
[Tau](https://github.com/huggingface/tau) — the two MIT-licensed agents this is studied from.
Written independently, not forked.

**Currently at Tier 2 — "it is usable."** 4,654 lines of source, 289 tests, all offline.

* **[`READING-ORDER.md`](READING-ORDER.md) — start here.** All 30 files in the order to read them,
  one line each, plus the questions to hold while reading.
* [`TIER-1.md`](TIER-1.md) — what the first tier does, and what it deliberately left out
* [`TIER-2.md`](TIER-2.md) — what this tier adds, and where each remaining gap plugs in at Tier 3

Tier 1 proved the loop terminates. Tier 2 makes it safe to point at a real repository: it can be
interrupted without corrupting the conversation, it remembers across restarts, it asks before it
destroys anything, and its provider abstraction is no longer a claim but a measured result.

## Run it

```bash
uv sync

uv run omega --fake                 # scripted responses — no key, no network, no credits
uv run omega                        # Anthropic; needs ANTHROPIC_API_KEY (see below)
uv run omega --provider openai      # OpenAI Chat Completions
uv run omega --resume               # continue the most recent session here
```

`--fake` is not a stub. It drives the entire agent — loop, harness, tools, streaming, approvals —
through `FakeProvider`, so you can watch the whole thing work without spending anything.

Useful flags: `--yes` approves tool calls automatically (it does **not** disable the
refuse-outright list), `--no-save` skips writing a session, `--session ID` resumes a specific one,
and `--base-url` points the OpenAI adapter at a local server:

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

## Check it

```bash
uv run pytest -q                    # 289 tests, ~1.7s, fully offline
uv run mypy --strict src
uv run ruff check .
uv run python -m omega.evals        # smoke eval: does the assembled agent still work?
```

The test suite never touches the network. Every provider call is faked at the interface boundary —
which is why `providers/fake.py` was written before the real adapter — and the two vendor SDKs are
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
