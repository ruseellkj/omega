# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A **study-then-build project**: Rushil is building `omega`, a terminal coding agent, from scratch
in tiers, using two MIT-licensed references as teaching material. The goal is understanding, not
shipping — he reads every line himself.

| Directory | What it is |
|---|---|
| `omega/` | **the agent.** Three packages, 8,699 lines of `src/`, 512 tests. Tier 3 complete. |
| `dev-notes/` | the study notes — teardowns of the references, architecture decisions, concepts |
| `research/pi/` | **reference 1**: Pi (TypeScript), `github.com/earendil-works/pi` |
| `research/tau/` | **reference 2**: Tau (Python), `github.com/huggingface/tau` — a port of Pi |
| `agent.py` | the original 188-line prototype, kept for comparison |
| `omega/website/` | a Next.js site. **A separate session owns this — do not touch it.** |

`research/pi` and `research/tau` are read-only reference material. Read them to answer "how do the
references do this?" — never edit them.

## Working in `omega/`

```bash
cd omega
uv sync                             # install from the lockfile
uv run pytest -q                    # 512 tests, ~6s, fully offline
uv run mypy --strict src            # must be clean
uv run ruff check .                 # must be clean
uv run python -m omega_coding.evals # smoke eval, 4/4, no network
uv run omega --fake --yes           # a full turn against scripted responses
```

Python >=3.14, pinned by `.python-version`. `asyncio_mode = "auto"`, so async tests need no marker.

**Every change must leave all four green.** Where there is a bug, this project's convention is a
red test that names the failure, then the fix.

## Architecture — three packages, one rule

The rule that decides where code goes: **what does this file know about?**

| Package | Knows about | Never knows about |
|---|---|---|
| `omega_agent` | messages, events, tools, turns, sessions | files, vendors, terminals |
| `omega_ai` | one vendor's wire format | anything above it |
| `omega_coding` | files, shells, policy, the screen | — it is the top |

`omega_agent` imports nothing from the other two. `omega_ai` imports nothing from `omega_coding`.
**`tests/test_layers.py` fails if that stops being true** — it parses imports with `ast`, so the
boundary is enforced rather than documented.

Exactly two files may name a concrete provider: `cli.py` and `evals.py` (the composition roots).

### The seams that keep the loop small

`loop.py` is capped at ~250 lines by convention and currently sits at 190. It never makes policy —
it *asks*, through `hooks.py`:

| Hook | Filled by |
|---|---|
| `before_tool_call` | `omega_coding/approval.py` — the approval gate |
| `after_tool_call` | `omega_coding/redact.py` — credential masking |
| `convert_to_llm` | `omega_coding/history.py` — drop empty failed turns |
| `transform_context` | **empty — compaction goes here at Tier 3** |
| `get_steering_messages` / `get_follow_up_messages` | queues on the harness |

If a change would grow `loop.py`, it almost certainly belongs behind a hook instead.

## Reading the code

`omega/READING-ORDER.md` gives all 46 files in dependency order with one line each. Start there,
not with `ls`. `omega/TIER-1.md` and `omega/TIER-2.md` record what each tier has, what it lacks,
and where Tier 3 puts it.

`dev-notes/` is a reference, not a book — read a section when you reach the file it explains.
`dev-notes/00-concepts/state-and-delegation.md` compares omega, Pi, Tau and Claude Code on
persistence, retry, locking, subagents and sessions.

## Three rules that apply to everything

Not style preferences. Each is here because breaking it caused real damage in this repo, during
ordinary build work rather than during explanations.

**1 · Never name something that does not exist without saying so.**
A hook called `before_record` and a tier called "Tier 2.5" were both invented mid-answer and sat
beside six real hook names and two real tier names. They read as real and cost a round of
confusion each. Before citing any symbol, flag, file or tier: **grep for it.** If it is not there,
say so. If something hypothetical needs a name, mark it as invented in the same sentence.

**2 · Run it and paste the output. Do not assert behaviour.**
Three claims made confidently here were false, and each was disproved in under a minute by
executing it:

- "always-allow cannot break the path fence" — it could
- "redaction covers tool failures" — it did not; `exit 1` leaked the key
- "outside-root grants are safe" — they were recursive, so approving `~/.zshrc` authorised `~/.ssh`

A ten-line script beats a confident paragraph. If the output contradicts the explanation, the
explanation was wrong.

**3 · Compare against the references with evidence, never from memory.**
`research/pi/` and `research/tau/` are on disk — read them.

| | What counts as evidence |
|---|---|
| Pi | file and line from `research/pi/` |
| Tau | file and line from `research/tau/` |
| Claude Code | **closed source** — its published docs, or "unknown". Never a guess |

"I cannot read Claude Code's source, so I will not guess" is a complete answer. A plausible row
invented to fill a table is not.

## Other conventions

- **Docstrings carry the argument, not just the description.** Most modules explain *why* they are
  shaped that way, including which alternatives were rejected. Match that when adding code.
- **Conventional Commits**, since `6aa9261`. Earlier subjects are prose and predate the decision.
- **Do not commit after every step, and never `git push`** — that is Rushil's call. Make the
  change, verify it, report it, and stop.
- **Never `git add -A` or `git add .`** — stage named paths only. `omega/website/` belongs to a
  different session, and one blanket add swept three of its in-progress files into an unrelated
  commit, which would have reverted merged work on push.
- **Plain language in chat, depth in files.** Explanations should be mechanical and worked through,
  not summarised; the exhaustive version belongs in `dev-notes/`. For conceptual questions the
  `explain-in-depth` skill carries the full method.

## Current state

**Tier 2 complete**, plus post-Tier-2 fixes: the path fence was removed in favour of a
location-aware approval gate, tool descriptions were rewritten against both references, the system
prompt moved to `omega_coding/system_prompt.py`, and two redaction bypasses were closed.

Since then, three additions that are **ergonomics, not scorecard progress** — none of the nine
failures moved: `omega_coding/commands.py` (seven `/` commands and a `!` shell escape, routed
through `execute_tool_call` so the escape meets the approval gate), `omega_coding/status.py` (a
working line with truthful labels), and a single REPL event loop, which removed the httpcore
traceback that four isolated reproductions had failed to trigger.

Known gaps, all recorded in `omega/TIER-2.md`:

- **compaction** (beginner failure #1) — long tasks still hit the context limit
- **prompt caching** (#9) — every turn re-bills the system prompt and tool schemas
- redaction sits on `after_tool_call` when it wants "before anything is recorded" — patched twice,
  the shape is still wrong; the real fix needs a new hook in `harness.py`
