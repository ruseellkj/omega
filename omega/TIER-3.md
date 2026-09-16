# omega — Tier 3

What this tier will contain, and what it deliberately leaves to Tier 3+.

**Status: open. Written before the code, to be kept honest afterwards.**

Like `TIER-1.md` and `TIER-2.md`, this file is a *contract* written at the start of the tier — what
would have to be true when it closes — so the work can be checked against a commitment rather than
described after the fact. It will be updated in place wherever reality disagrees.

**Starting point: 6,247 lines of source across 38 files · 6,274 lines of tests · 395 tests, all
offline · `loop.py` at 190.**

| | Tier 1 | Tier 2 | Today | Tier 3 estimate |
|---|---|---|---|---|
| Source | 1,577 | 4,654 | **6,247** | see below |
| Test count | 45 | 289 | **395** | — |
| `loop.py` | 151 | 190 | **190** | **190** |

**The estimate, with the correction factor applied.** `TIER-2.md` records that three estimates in
these documents have run low, consistently, and instructs: *"Assume any estimate in these docs is
roughly 1.3-2x low."* A naive reading of Tier 3 is "~4,000 lines". Applying the factor gives
**9,000–12,000**, and the TUI alone justifies it — Tau's is 10,828 lines. Tier 3 is very likely the
tier where omega roughly doubles.

**`loop.py` must still be 190 when this closes.** Every gap below plugs into a seam that already
exists, with exactly one exception, named as such. If a change would grow the loop, the layering is
wrong and that is the thing to fix.

---

## The one-line summary

**Survives a task long enough to fill the context window, and has a face.**

---

## Part 1 — What Tier 3 adds, and the seam each plugs into

### The two remaining beginner failures

These are the scorecard. Everything else in this file is a feature; these two are *correctness*.

| Missing | What it costs today | The seam | Seam status |
|---|---|---|---|
| ~~**Compaction** — failure **#1**~~ **LANDED** | Long tasks died at the context limit; Tier 2 could only *show* the wall coming, via `/context` | `transform_context` (`hooks.py:93`), now filled by `compact.py` | **filled** |
| **Prompt caching** — failure **#9** — *markers sent, hit unconfirmed* | Every turn re-bills the system prompt and the whole tool schema block | Not a hook — a **constraint**. Cache markers need a byte-identical prefix, which is why `OMEGA.md` is prepended once at startup and never regenerated per turn | n/a |

Closing these two takes the beginner scorecard from **7 of 9** to **9 of 9**, which is the whole
point of the tier and the reason it comes before anything on the Tier 3+ list.

**#1 is now closed.** The scorecard stands at **8 of 9**.

**#9 — the first attempt was a half-measure, and the second one is the real
thing.** Worth recording both, because the mistake is instructive.

**What the first pass did.** One breakpoint, on the system prompt. That caches
the system prompt and the tool schemas — together about 3,753 characters, a few
hundred tokens, *fixed*. It ignored the conversation, which is everything else,
grows every turn, and is re-sent in full each time. It cached the rounding error
and left the bill alone.

**Why the "wait for search tools" framing was backwards.** The first pass noted
that omega's static prefix sits near Anthropic's minimum cacheable size and
concluded that adding `grep`/`find`/`ls` would push it over. True, and beside the
point: **once the conversation is cached the minimum stops mattering**, because a
conversation passes 1,024 tokens within a turn or two whatever the prompt size.
The size problem was a symptom of caching the wrong thing.

**The documented minimums, corrected.** An earlier note here said "1,024 for
Sonnet and Opus, 2,048 for Haiku". Both halves were wrong:

| Model | Minimum cacheable prefix |
|---|---|
| Opus 5, Fable 5, Mythos 5 | **512** |
| **Sonnet 5** (omega's default), Opus 4.8, Sonnet 4.6 | **1,024** |
| Opus 4.7, Haiku 3.5 | 2,048 |
| Opus 4.6, Opus 4.5, Haiku 4.5 | 4,096 |

Below the minimum the marker is ignored silently — no error — which is why the
only real check is `cache_read_input_tokens` in the response.

**What it does now**, following Tau (`tau_ai/anthropic.py:468-518`) rather than
inventing a scheme. Anthropic allows **four** breakpoints; omega spends them:

| Breakpoint | Where | Why there |
|---|---|---|
| 1 | end of `system` | caches system, and the tools behind it |
| 2 | the **last** tool | tools and the prompt change at different rates; editing `OMEGA.md` should not evict the schemas |
| 3 | the **last message** | the conversation — where the money actually is |
| 4 | the **previous request's tail** | the second lookback window |

**Why the fourth one exists.** Anthropic searches at most **20 block positions**
back from a breakpoint for a reusable prefix, then stops. A long turn can push
the previous write outside that window, and the request re-pays for the entire
conversation. Marking where the last request ended opens a second window at a
position already known to hold an entry. Tau reached this first; Pi marks only
one message position (`anthropic-messages.ts:1256-1277`) and is the weaker of the
two here.

**Cost, so the trade is explicit.** A 5-minute cache write is **1.25x** base
input and a read is **0.1x**, so the second request on a cached prefix already
pays the write back (1.25 + 0.1 = 1.35 against 2.0 uncached). The 1-hour TTL
doubles the write and is not used: it pays off only for a subscription that is
not billed per token, which is Tau's reason for choosing it and not omega's.

**Still not closed.** Markers are sent and tested; a cache *hit* is unproven,
because the key on this machine returns `credit balance is too low`. Two turns
against a live provider with `cache_read_input_tokens > 0` closes it. The
scorecard stays at **8 of 9** until then.

### Everything else Tier 3 adds

| Missing | What it costs today | The seam | Seam status |
|---|---|---|---|
| **A real TUI** | Print output cannot show a diff, a spinner, or a sidebar — and cannot accept a keystroke mid-turn | The 10 agent events (`agent_events.py:49-139`) | **exists** |
| **Search tools** — `grep`, `find`, `ls` | The model shells out to `rg`, which works but has no output budget of its own | Three more `Tool` objects. `truncate_output()` (`truncate.py`) and path resolution (`paths.py`) both exist | **exists** |
| **Session branching** | You can rewind by reading the JSONL, but not fork and navigate | `parent_id` is already written on every entry (`session/entries.py`). Tier 3 adds `tree.py` — `path_to_entry`, cycle detection | **exists** |
| **Structured logging** | Debugging means reading print output | A second listener on the same event stream. Not a new mechanism | **exists** |
| **Image reading** | Screenshots cannot be handed to the model | `types.py` content blocks are a discriminated union; an image block is an addition, not a change | **exists** |
| **Subagents *or* plan mode** | No task decomposition | `before_tool_call` plus the headless driver. A subagent **is** the headless driver, called from a tool | **exists** |
| ~~**Redaction at the transcript boundary**~~ **LANDED** | Redaction sat on `after_tool_call`, which fires only when a tool *returns a value*. Four paths went around it | `before_record` in `hooks.py`, consulted by `harness._record` | **built** |

### Compaction — landed

`omega_coding/compact.py`, filling `transform_context`. **`loop.py` gained zero
lines**, which was the test the seam was built to pass.

Measured end to end through a real turn: a 21-message transcript estimated at
**10,004 tokens went out as 4 messages and 1,054 tokens** against a 1,600 budget,
while the transcript itself *grew* from 20 messages to 22. That is the two-views
split doing exactly what it was designed for.

`/compact [pct]` is the manual half — Pi (`slash-commands.ts:38`) and Tau
(`commands.py:230`) both have the same pair. Theirs take free-text instructions
because theirs write a model-authored summary; omega's is mechanical, so a
*percentage* is the argument that means something: `/compact 40` clears the decks
before a big task rather than waiting for the 80% ceiling to be crossed mid-work.
Unlike the automatic pass it edits the transcript, not just the request — and
`harness.replace_transcript` moves the `_persisted` high-water mark, without which
every message after a compaction is silently never written to disk.

It is mechanical, not model-based: drop the oldest whole turns, then shrink what
remains — tool output first, because it is the only thing that can be recovered
by running the tool again; assistant prose second; **the user's own messages
never**. Summarising with a model is the obvious alternative and is where Pi
spends ~880 lines, but it cannot be tested offline, and omega's suite runs in
three seconds against no network. It is an addition on top of this, in the same
seam.

**Fuzzing found a real gap that reading did not.** 3,000 random transcripts
produced zero invariant violations but left 279 over budget, and all of them had
the same shape: no tool results to cut, so the first version could do nothing.
Adding the prose pass took that to 93, and every one of those remaining is one of
two deliberate refusals — the user's own words exceeding the budget (74), or the
`MIN_RESULT_TOKENS` floor multiplied out on an absurdly small window (19,
overshooting by 2 tokens). The trade is stated in the module: **validity is
absolute, size is best-effort.**

### Redaction at the transcript boundary — landed

The only row here that needed a seam that did not exist, which is why it went
early rather than mid-tier: structured logging and subagents would both have been
built against the wrong shape.

`TIER-2.md` predicted the failure exactly — *"a third way around will appear the
next time a new path to the transcript is added"*. Running it found **two** more,
neither of which involves a tool at all:

* the model repeating a key back in its own answer
* the user pasting one into a prompt

Both reached the transcript unmasked, were written to disk, and were re-sent to
the provider on every later turn — with redaction wired and working exactly as
designed. Measured before the fix:

```
model echoed a key  -> present in transcript: True
user typed a key    -> present in transcript: True
```

and after:

```
model echoed a key  -> present in transcript: False
user typed a key    -> present in transcript: False
what the model now sees: here is my key [redacted Anthropic API key], check it
```

**The fix is the attachment point, not another patch.** `before_record` is a
seventh hook consulted by `harness._record` as each message is recorded, whatever
produced it — driven by a high-water mark for the same reason persistence is, so
the loop and `repair_orphans` are both covered without knowing it exists.
`loop.py` gained zero lines. The two earlier patches stay: defence in depth, and
`after_tool_call` still masks a result before the model reads it, one step
earlier than recording.

### The ordering constraint

Read the last column. **Every row but one begins by filling a seam that already exists.** The
redaction row is the only one that needs a seam built first, which makes it the only row with an
ordering dependency: it goes early, or it goes last. Not in the middle, where it would mean
reworking whatever was built against the old shape.

That asymmetry is the single most useful thing in this table, and it is why the table has a
"seam status" column at all.

---

## Part 2 — The TUI, in more detail

It is the largest item, so it gets its own section.

### The decision: Textual + Rich

| | What it uses | Evidence |
|---|---|---|
| **Pi** | **nothing — hand-written** | `packages/tui/package.json` declares two runtime deps: `get-east-asian-width` and `marked`. `ink` and `react` appear **zero** times in every `package.json`, **zero** times in source imports, and **zero** times in `package-lock.json` — which would catch them even as transitive dependencies. `terminal.ts:12-18` writes raw ANSI (`\x1b]9;4;3\x07`) and negotiates the Kitty keyboard protocol. **14,184 lines**, with its own `tui-plan.md` design document. |
| **Tau** | **Textual 8.2.8 + Rich 13** | `pyproject.toml:20-21`. `tui/` totals **10,828 lines**; `app.py` alone is 6,808. |
| **Claude Code** | **unknown** | Closed source. Its npm package declares `"dependencies": {}` because it ships pre-bundled and minified, which conceals its internals rather than revealing them. Not guessed. |

**Ink was never a candidate.** It is a Node/React library and omega is Python. The real question was
Textual vs. hand-rolled ANSI vs. `prompt_toolkit`, and the answer is **Textual** — it is Python's
equivalent of Ink (declarative widgets, reactive state, real CSS, a `BINDINGS` table), it lists a
Python 3.14 classifier so it fits omega's pin, and it is the only option with a reference
implementation on disk to read.

### Why the bridge is cheap and the TUI is not

omega already emits exactly the events a UI needs:

```
AgentStart/End · TurnStart/End · MessageStart/Update/End · ToolExecutionStart/Update/End
```

Tau's entire agent→UI bridge is **99 lines** (`tui/adapter.py`, counted). It is a single
`apply(event)` dispatch that mutates a `TuiState`; the widgets react to that state and never see an
event. Tau's event names are near-identical to omega's, so the pattern transfers directly rather
than by analogy.

**But the 99-line figure describes the bridge, not the job.** Behind it sit `app.py` at 6,808 lines
and `widgets.py` at 2,260. The event contract means the TUI does not require changing the agent —
it does not mean the TUI is small.

The proposed shape, mirroring Tau's separation of state from widgets:

```
omega_coding/tui/
  adapter.py    the 10 agent events -> TuiState.       ~100 lines
  state.py      what the screen should show.           state, not widgets
  widgets.py    transcript, status line, editor
  app.py        the screen, keybindings, input
```

### What the TUI unblocks that is currently recorded as broken

`TIER-2.md` lists steering as wired-but-unreachable:

> *"Steering cannot actually be typed yet. The queues are wired and the loop drains them between
> turns, but a `print`/`input` REPL has no way to accept a keystroke while a turn is running."*

`harness.queue_steering()` (`harness.py:180`) exists and is tested. Tier 3's TUI is what makes it
reachable by a human rather than only by a test. That is a gap closing, not a feature arriving.

---

## Part 3 — What Tier 3 deliberately leaves out

Recorded so each absence is a decision rather than an oversight.

- **No MCP, no retrieval/RAG.** Neither reference implements them.
- **No packaging, no install script, no PyPI release.** omega runs through `uv run` at Tier 3.
  Shipping is a product concern and is the first section of `TIER-3-PLUS.md`.
- **No OAuth, no provider catalog, no extensions, no themes, no skills.** All Tier 3+, all listed
  in that file with the Tau module that proves each is a real category of work.
- **No sandboxing.** Tier 3+, and its `prepare` seam already shipped in Tier 2 (`hooks.py`,
  `builtin_tools.py`) so it lands without surgery.

---

## Is Tier 3 the last one?

No — and the question is worth answering precisely, because "layer" and "tier" are different axes
and the word "final" belongs to only one of them.

- **Layers (L1–L4)** are a *structure*: provider → agent core → coding app → UI. There have been
  four since Tier 1. L4 is currently partial: output is printed, and the 10 agent events are
  already the contract a real UI would use.
- **Tiers** are *delivery milestones*.

**Tier 3 finishes the layer diagram** — L4 stops being partial. It does not finish omega. Everything
a product needs that the diagram never described is Tier 3+, and that is a deliberate boundary
rather than a backlog that ran out of room.

For scale, at the start of Tier 3:

| | Source lines | Files |
|---|---|---|
| omega | 6,247 | 38 |
| Tau | 36,414 | 90 |
| Pi | 133,800 | — |

omega is **17% of Tau** and **4.7% of Pi**. Tier 3 narrows that. Tier 3+ is the rest of it, and
omega is not trying to close it — the goal was always to understand the shape, not to ship a rival.
