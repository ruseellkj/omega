# omega

A terminal coding agent, built from scratch in layers.

Named after a physics letter, following [Pi](https://github.com/earendil-works/pi) and
[Tau](https://github.com/huggingface/tau) — the two MIT-licensed agents this is studied from.
Written independently, not forked.

**Currently at Tier 1.** See [`TIER-1.md`](TIER-1.md) for exactly what that does and does not
include.

## Run it

```bash
uv sync
uv run omega --fake        # scripted responses — no API key, no network, no credits
uv run omega               # real, needs ANTHROPIC_API_KEY in ../.env
```

`--fake` is not a stub. It drives the entire agent — loop, tools, streaming — through
`FakeProvider`, so you can watch the whole thing work without spending anything.

## Check it

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```

The test suite runs completely offline. Every provider call is faked at the interface boundary,
which is why `providers/fake.py` was written before the real adapter.

## Layout

```
src/omega/
├── types.py         neutral messages and content blocks
├── events.py        the 12 stream events — Layer 1's vocabulary
├── provider.py      the interface. The core owns it; adapters conform.
├── providers/
│   ├── fake.py      scripted replay. Written first, on purpose.
│   └── anthropic.py the only file that knows a vendor exists
├── tools.py         Tool and ToolResult
├── truncate.py      output budget: 2,000 lines / 50 KB, tail-biased
├── builtin_tools.py read_file, write_file, run_shell
├── loop.py          the agent loop. Should not grow.
└── cli.py           print-based REPL. Not a TUI — that would hide bugs.
```

One check that the layering held — **which files import the vendor SDK**:

```bash
grep -rln --include='*.py' -E '^(from|import) anthropic' src/omega/
```

should print `src/omega/providers/anthropic.py` and nothing else.

Note this is deliberately narrower than "which files mention Anthropic". `cli.py` imports
`omega.providers.anthropic` — our module, not the SDK — because something has to choose a
concrete provider, and that job belongs to the composition root. Several files also mention
Anthropic in comments. Neither is a leak. The leak would be a *core* module importing the SDK,
and none does.

## Why it's shaped this way

Every design decision here traces to a document in [`../docs/`](../docs/) — in particular
`03-architecture/04-boundaries-and-layout.md` for the layer rules and
`01-teardown/` for where each pattern came from in Pi and Tau.
