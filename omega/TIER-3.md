# omega — Tier 3

What this tier will contain, and what it deliberately leaves to the product backlog.

**Status: every row filled. Written before the code, kept honest afterwards.**

**Closed at 8,699 lines of source across 46 files · 8,750 lines of tests · 512
tests, all offline · `loop.py` at 190.**

| | Tier 1 | Tier 2 | Tier 3 | |
|---|---|---|---|---|
| Source | 1,577 | 4,654 | **8,699** | +4,045 |
| Tests | 45 | 289 | **512** | +223 |
| `loop.py` | 151 | 190 | **190** | **+0** |

**The estimate was right this time, which is new.** This file predicted
9,000–12,000 after applying the 1.3–2x correction factor to a naive "~4,000".
The answer is 8,699 — just under the range, and the first estimate in this
project not to run low. The factor was the useful part, not the original guess.

**`loop.py` did not move.** Eight tiers of feature work — compaction, caching,
redaction, search, logging, a TUI, branching, subagents, images — and the loop is
the same 190 lines it was when Tier 2 closed. Every one of them filled a seam
that already existed, except `before_record`, which added one and is the reason
that row went first.

**One row is not proven, and it is not a formality.** Prompt caching is
demonstrated on OpenAI with a measured 32% of input served from cache, and
unproven on Anthropic because the key on this machine returns `credit balance is
too low`. Markers are sent and tested; a cache hit is not observed. The beginner
scorecard therefore reads **8 of 9**, not 9 of 9 — see the caching section.



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
point of the tier and the reason it comes before anything on the the product backlog list.

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

**Confirmed on OpenAI, 2026-09-16.** Two live runs settled the threshold
question empirically rather than by estimate:

| Run | Input tokens per turn | Cache read |
|---|---|---|
| a short chat | 831, then ~851 | **0** |
| read one file, then a follow-up | 1,729, then ~6,400 | **2,560** |

`cache: 2,560 read, 0 written (32% of input served from cache)`. The first run
stayed under OpenAI's 1,024-token minimum and cached nothing; the second crossed
it on the opening turn. That is the same boundary the prefix measurement
predicted, now measured instead of bounded — and it confirms the shape of the
problem: **a real coding turn clears the minimum immediately**, and the short
chat that does not is the one that costs nothing anyway. `0 written` is correct;
OpenAI reports reads only.

**Anthropic is still unproven,** and the mechanism does not transfer — OpenAI
caches server-side with nothing sent, while Anthropic needs the explicit markers
above. So #9 is **closed for OpenAI, pending for Anthropic**.

**Still not closed overall.** Markers are sent and tested; an Anthropic cache hit is unproven,
because the key on this machine returns `credit balance is too low`. Two turns
against a live provider with `cache_read_input_tokens > 0` closes it. The
scorecard stays at **8 of 9** until then.

### Everything else Tier 3 adds

| Missing | What it costs today | The seam | Seam status |
|---|---|---|---|
| ~~**A real TUI**~~ **LANDED** | Print output could not accept a keystroke mid-turn, so steering was unreachable | `omega_coding/tui/` — Textual, behind `--tui` | **filled** |
| ~~**Search tools**~~ **LANDED** | The model read whole files to find one line, and shelled out for the rest | `list_files`, `find_files`, `search_files` in `builtin_tools.py` | **filled** |
| ~~**Session branching**~~ **LANDED** | `load()` read the file linearly, so a fork would have returned both attempts at once | `session/tree.py` — `path_to`, `leaves`, cycle detection; `/rewind` | **filled** |
| ~~**Structured logging**~~ **LANDED** | Debugging meant reading print output — which a TUI removes | `eventlog.py`, a second listener on the same event stream | **filled** |
| ~~**Image reading**~~ **LANDED** | Screenshots could not be handed to the model at all | `ImageContent` in `types.py`; `read_image`; both adapters place it | **filled** |
| ~~**Subagents**~~ **LANDED** | No task decomposition, and exploration filled the parent's context | `subagent.py` — `run_headless` called from a `Tool`. Nothing below it changed | **filled** |
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

### Search tools — landed

Four tools became seven: `list_files`, `find_files`, `search_files`.

**One reference does without them, and that is the interesting part.** Tau ships
exactly four — read, write, edit, bash — and expects the model to shell out. Pi
has `find`, `grep` and `ls`. omega held Tau's position until a live run made the
cost concrete: asked how many lines `loop.py` had, the model called
`read_file(limit=1000)` to pull the entire file into context, then
`run_shell("wc -l")`. After, asking where a class is defined returns one line:

```
search_files({'pattern': 'class Tool', 'path': 'src'})
  -> src/omega_agent/tools.py:58: class Tool:
```

**Pure Python, no ripgrep.** Pi shells out to `rg` and downloads the binary when
it is missing (`grep.ts:172`). The suite runs offline against no external binary,
and buying search at the cost of that property would be a poor trade.

Three things carried real risk, and each has a test:

* **Symlinks.** A directory link inside the project pointing at a home directory
  would enumerate it with **no outside-root path ever reaching the approval
  gate** — the gate reads arguments, and that path appears in none. The walker is
  written by hand, skips links, and re-resolves every hit against the root anyway.
* **The gate's path table.** `approval.py` maps each tool name to the argument
  naming a path. A new tool missing from it loses its location check silently, so
  all three name their directory `path` and all three are registered.
* **Per-match line caps.** `truncate_output` caps the whole reply; one minified
  file is a single multi-megabyte line that would use the budget by itself.

A fourth was found only by running the tests: `fnmatch` has no notion of a path
separator, so `**/*.py` silently missed every top-level file.
`PurePosixPath.full_match` implements real glob semantics and fixed it.

### The httpcore traceback — **and the claim below it was wrong**

The per-turn traceback fixed earlier was a new event loop per prompt. A second,
shutdown-time one survived it, and the diagnosis was that the provider's HTTP
client was never closed, so its connection pool was collected at interpreter
shutdown — after the event loop was gone. Both adapters gained `aclose()`,
closing only a client they created, and `_repl` calls it on every exit path.

**That fix was real and it did not fix this.** The line that used to stand here —
"Measured after: `0 occurrences` of `athrow` in a full session" — came from a run
that happened not to trigger it, and was reported as a fix. It is not one.

Re-measured properly, against the **bare OpenAI SDK with no omega in the
picture**:

```
mode: nothing      → 4 athrow occurrences
mode: close        → 4
mode: close_sleep  → 4
```

Closing the client changes nothing, because omega is not what fails to close it.
The traceback is upstream — `openai 3.3.1` / `httpx 0.28.1` / `httpcore2 2.12.0`
on Python 3.14 — and it fires from the async-generator shutdown hook *after* the
answer has been printed. Cosmetic, and not ours.

`aclose()` stays: the client genuinely was not being closed, and closing it is
correct regardless of what it does or does not silence. The lesson is the one
CLAUDE.md rule 2 already states, applied to a case where it was skipped: a single
clean run is not a measurement.

### Structured logging — landed, and it found a fifth redaction bypass

`eventlog.py` writes `~/.omega/logs/<session>.jsonl`, on by default, swept after
seven days like the truncation spill files. **The harness gained nothing**:
`add_listener` has existed since Tier 2 carrying exactly one subscriber, and
"a second listener, not a new mechanism" was a claim until this proved it.

**Then it found the bug it was built on top of.** Listeners were notified
*before* `_record` ran, so every subscriber saw the original message while the
transcript was being masked behind it:

```
transcript holds the key : False
A LISTENER SAW THE KEY   : True
```

A log written from that would have put the key back on disk by a route
`before_record` never covered — the fifth way around, in a category `TIER-2.md`
predicted would keep reopening. Fixed by recording **before** notifying, and by
masking the event itself: an event carries its own *copy* of the message, so
reordering alone left it pointing at the original.

**One thing cannot be masked, and the design accounts for it rather than hiding
it.** Every message event carries the raw provider `stream_event` beside the
assembled message, and that holds a *delta*. A credential split across two
streamed chunks matches no pattern in either half — no redaction pass can help.
So the rule is mechanical: **the log records assembled messages, never
fragments.** Five of the ten event types are written; `message_update` is not,
and `stream_event` never is.

A second bug came from running it rather than reading it: `session_id` does not
exist until the first turn creates it, so a path bound at construction named
every session `unsaved.jsonl` and appended them all to one file. The path is now
resolved per write.

### The TUI — landed, behind `--tui`

**411 lines across three modules**, against Tau's 10,828. The difference is
listed below as decisions, not omissions.

**It was worth a dependency for one reason: steering.** `TIER-2.md` recorded
`queue_steering` as wired, tested, and unreachable by a human, because a
`print`/`input` REPL cannot take a keystroke while a turn is running. That is now
closed — type into the box mid-turn and the guidance is queued, acknowledged on
screen, and drained by the loop after the current tool result. A prettier
transcript would not have justified the dependency; a gap closing does.

```
omega_coding/tui/
  state.py    97   what the screen should show — imports no Textual
  adapter.py  98   the 10 agent events -> state changes
  app.py     203   the screen, keybindings, the input box
```

**The split is the load-bearing part.** Nothing in `state.py` or `adapter.py`
imports Textual, so **every behavioural test runs without a terminal** — 13 of
the 15 assert on `TuiState` and never start an app. Tau keeps the same separation
and gets a 99-line adapter from it; omega's is 98, which is the event vocabulary
earning its keep rather than a coincidence.

`cli.py:_render` was the specification, not a guide. Each of its branches has a
matching test, because anything it handles and the adapter does not is a
regression rather than a simplification.

**Two bugs, both found by tests rather than by reading:**

* A fast fake provider finished before the second keystroke landed, so the input
  started a *second turn* and the steering test **passed against a build with no
  steering at all**. Fixed with a provider slow enough that the turn is genuinely
  in flight — the same class of false confidence as the `!rm -rf /` test.
* Quitting mid-turn left the worker redrawing a screen that no longer existed,
  raising `NoMatches` and taking the turn down with it.

And one behaviour worth knowing, which looks like a broken queue and is not:
**steering during the final turn of a run is not seen until the next run.** The
loop drains between iterations, so a text-only turn ends with the queue
untouched.

**Deliberately not built**, so the gap is a decision:

* **an approval modal** — `_ask_in_terminal` blocks on `input()` in a thread,
  which cannot work under Textual. `--tui` therefore requires `--yes` and says so
  rather than hanging on a prompt nobody can see. First thing to add next.
* the print REPL stays the **default**: `--fake`, piped stdin, the headless
  driver and the approval prompt all work there, and piped stdin is how this
  project has been verified all along.
* themes, autocomplete, file drop, notifications, a session picker, mouse
  support, markdown rendering, and a diff view for `edit_file` — that last one
  being the most obviously missing.

### Session branching — landed, and Tier 2's bet paid

`entries.py` has written `parent_id` on every entry since Tier 2, read by
nothing, on one recorded argument (`anatomy.md:314`): *"Retrofitting a tree onto
a list is a rewrite."* That bet is now collected, and the receipt is specific:

**No record shape changed. No `SCHEMA_VERSION` bump. No file already on disk
became unreadable.** The format was right from the start; only the reader was
wrong.

**And the reader really was wrong.** `store.load()` returned every entry in file
order, ignoring `parent_id` completely. That is indistinguishable from correct
while a session is a straight line — which is why it survived a whole tier. Give
one parent two children and it silently returns both attempts concatenated: a
conversation that never happened, containing two different answers to the same
question. So this was not a feature added to working code; it was a latent bug
that nothing could observe until something branched.

The compatibility claim was written so it could fail — *for a linear session the
path to the only leaf is the whole file* — and it holds: `load()` returns exactly
what it always did for every session written before today.

```
session/tree.py     path_to(entries, id) -> root..id      pure, no file handles
                    leaves(entries)      -> branch tips   file order preserved
                    CycleInTranscript                     raised, never looped
```

**Why a cycle is raised rather than skipped.** Nothing omega writes can make one;
a hand-edited or corrupted file can. A `while parent is not None` walk over a
cycle **never returns** — the process hangs with no error and nothing to grep
for, which is a worse failure than either crashing or returning junk.

**The write half is `/rewind`**, counted in *questions* rather than messages,
because one question can produce a dozen messages and nobody knows how many.
Nothing is deleted: the abandoned turns stay exactly where they are and the next
one hangs off an older parent, so both attempts remain loadable. That is what
append-only bought, and why rewinding a log you cannot edit is possible at all.

```
Rewound 1 question(s): 4 messages dropped.
The old branch is still in the session file - /sessions still lists it.
```

**Not built:** a branch *picker*. `load(branch=...)` takes a leaf id and
`leaves()` lists them, so the mechanism is complete, but choosing between two
attempts by pasting a hex id is not a feature. That wants the TUI, and is the
obvious next thing to hang off it.

### Subagents — landed, and the Tier 2 claim held

`headless.py` has claimed since Tier 2 that *"a subagent **is** this function
called from inside a tool."* It is. `subagent.py` is a `Tool` whose `execute`
calls `run_headless`, and **nothing in the loop, the harness, or the provider
contract changed to make it work.** Had it needed a new mechanism, the layering
argument would have been wrong; this is where that would have shown.

**What it buys is failure #1 from the other side.** "Where is authentication
handled?" can mean reading twenty files. Asked in the main conversation, all
twenty land in the context and stay there — every later turn re-sends and re-pays
for them. Asked through a subagent, the parent gains one paragraph and the twenty
files are discarded with the child. Compaction makes a full context survivable;
this keeps it from filling.

So the tested property is not "it can run a nested agent" — it is **what the
parent does not inherit**. The test writes 200 lines into a file, has the child
read it, and asserts the parent's result is the one-line summary and contains
none of the file.

**Two things had to be right, and both are enforced rather than promised:**

* **Recursion is stopped by absence, not a counter.** A child holding this tool
  could spawn one forever, and the loop cannot tell — a nested agent is an
  ordinary tool call that happens to be slow. The child gets the parent's tool
  list with this tool filtered out. A counter would work and would be one more
  thing to remember to increment; a tool that is not there cannot be called.
* **The child runs under the parent's hooks.** Approval, redaction, history. A
  fresh policy for the child would be a second place for a deny list to be
  correct — the mistake `paths.py` warns about and `!cmd` made for real. A
  refusal is a refusal at any depth, and there is a test that refuses everything
  and checks the child's call arrived at the parent's gate.

**mypy found a bug two of my own tests had missed.** The turn-limit branch
compared `reason == "length"`, but the loop's cap is `max_turns` — `length` is
the *provider* running out of output tokens, a different thing. The branch could
never fire. The test passed anyway, because it asserted only that the word
"turn" appeared and the fallback message happens to contain it. Both fixed; the
test now names the limit and the advice.

**Deliberately not done:** no parallelism (tool calls are sequential and making
these the exception would be a scheduling change dressed as a feature), no
session file for the child, and **no nested cost line** — the child's tokens are
real and are not separately reported, which is worth knowing before trusting
`/cost` while using it.

### Image reading — landed, and it was the union's exam

Tier 1 made `ContentBlock` a discriminated union rather than a string, and
`TIER-2.md` used that to claim an image would be "an addition, not a change to
every caller". Mostly true — `ImageContent` slotted in, no `SCHEMA_VERSION`
bump, and every text-only session on disk still sends byte-identically. But
"addition" understates one part.

**The two providers disagree about where an image may appear**, and the layer had
to absorb it:

| | an image in a tool result |
|---|---|
| Anthropic | allowed, inside the `tool_result` block |
| OpenAI Chat Completions | **rejected** — a `tool` message takes text only |

So the Anthropic adapter nests it and the OpenAI adapter emits the tool result as
a text note *plus a following `user` message* carrying the image, that being the
one place Chat Completions accepts one. Measured:

```
anthropic -> tool_result carries: ['text', 'image']
openai    -> roles: ['user', 'tool', 'user']
             image rides on: image_url
```

The neutral `ToolResultMessage` never picked a side — the same argument the
provider layer was built on, restated on a content type that did not exist when
the argument was made.

**Detection is by magic bytes, never by extension.** Tau reaches the same
conclusion (`image_processing.py:39`). An extension is a claim; a `.png` holding
a JPEG is an ordinary mistake, and a 400 from the provider is a slow way to find
out. The `RIFF` case is the one worth having a test for — AVI and WAV share that
signature, so only `WEBP` at offset 8 counts, and guessing wrong sends audio to a
vision model:

```
png                           image/png
wav (RIFF but not an image)   None
text                          None
```

**mypy found a crash before an image ever reached it.** `redact_message` read
`.text` off every block of a tool result; widening the union made that a type
error. It would have thrown on the first screenshot. Images now pass through
redaction untouched — and not out of laziness: a credential inside a screenshot
is pixels, and a base64 payload matches no pattern, so redaction has nothing to
offer and pretending otherwise would be worse.

`.text` on both `ToolResult` and `ToolResultMessage` now skips images, because
that property is read by the cost estimate, the event log and the redaction pass
— three places that want words, not a megabyte of base64.

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
  Shipping is a product concern and is the first section of `PRODUCT-BACKLOG.md`.
- **No OAuth, no provider catalog, no extensions, no themes, no skills.** All the product backlog, all listed
  in that file with the Tau module that proves each is a real category of work.
- **No sandboxing.** the product backlog, and its `prepare` seam already shipped in Tier 2 (`hooks.py`,
  `builtin_tools.py`) so it lands without surgery.

---

## Part 4 — Two things a cancelled turn loses

Found by cancelling a real turn mid-stream and reading what survived, rather than by reasoning
about it. Both are open, both are small, and the design decision on the second one has been made
already so that it does not have to be made again under time pressure.

**What a cancel does keep**, measured:

```
harness.messages                    session file (the same four, verbatim)
  user       'do the thing'           role=user
  assistant  stop='toolUse'           role=assistant  stop=toolUse
  toolResult 'hi\n'                   role=toolResult
  assistant  stop='aborted'           role=assistant  stop=aborted

tool calls=1   tool results=1   orphans=0
```

The partial answer is kept and marked `aborted`, and the pairing holds because
`execute_tool_call` returns a result even when the call was cancelled (`loop.py:147`).
`repair_orphans` runs at the top of every `run()` (`harness.py:403`) as a second line of defence.
So the transcript stays valid, which is what makes "carry on from here" possible at all — it is a
new turn over an intact conversation, not a rewind.

### 1 · An aborted turn's tokens are billed and recorded as zero

`CostTracker` sums `usage` off `message_end` (`cost.py:73-78`). On the aborted path that usage is
`0/0`, measured — and the two providers are **not** in the same position:

| | Is partial usage available? |
|---|---|
| Anthropic | **yes** — input arrives in `message_start`, output accumulates in `message_delta`, so the partial already carries it |
| OpenAI | **no** — usage is only ever sent in a final usage-only chunk *after* generation ends (`openai.py:438-453`), and the cancel returns at `:436` before that chunk exists |

omega already asks for it: `stream_options: {"include_usage": True}` is set at `openai.py:388`. The
number is not missing because nobody requested it; it was never sent.

So OpenAI has to be estimated — `estimate_request_tokens(system, context, tools)` for input, which
already exists for compaction, and the streamed text's length for output. The alternative,
draining the stream to completion to get the true figure, is **worse than the bug**: it pays for
the whole response in order to measure a cancel that was meant to stop paying.

**The decision, and it is the load-bearing part:** an estimate goes on **its own line and is never
summed into the total.**

```
12,400 tokens · $0.04
1 interrupted turn, ~1,050 tokens (estimated, not included)
```

Folding a chars/4 guess into a figure otherwise built from provider-reported numbers makes the
*whole* figure an estimate, and leaves the reader no way to tell which part they can trust. A
separate line is less tidy and strictly more useful. `context.py:7-12` already argues that chars/4
is good enough for a threshold; money is not a threshold, which is exactly why the estimate has to
be labelled rather than absorbed.

### 2 · How long a turn took is never recorded

`status.py` computes elapsed seconds for the spinner (`ELAPSED_AFTER_SECONDS`, `render`) and throws
it away. Grep for `elapsed`, `duration`, `started_at` or `monotonic` across `omega_agent/` and
`eventlog.py` returns nothing. So "that took 90 seconds" is visible while it happens and
unanswerable afterwards, including in the session file, where it would cost one field.

The fix is a `time.monotonic()` stamp at turn start carried onto the entry. Tau does keep this —
`ChatItem.started_at` (`tui/state.py:52`) drives a live per-tool timer gated at one second so a
quick read never flashes `(0s)`. Pi keeps neither a label nor a timer.

**Both are deliberately not done.** They change a schema and an event payload in `omega_agent`,
which is a bigger call than a UI fix, and neither blocks anything today.

---

## Is Tier 3 the last one?

No — and the question is worth answering precisely, because "layer" and "tier" are different axes
and the word "final" belongs to only one of them.

- **Layers (L1–L4)** are a *structure*: provider → agent core → coding app → UI. There have been
  four since Tier 1. L4 is currently partial: output is printed, and the 10 agent events are
  already the contract a real UI would use.
- **Tiers** are *delivery milestones*.

**Tier 3 finishes the layer diagram** — L4 stops being partial. It does not finish omega. Everything
a product needs that the diagram never described is the product backlog, and that is a deliberate boundary
rather than a backlog that ran out of room.

For scale, at the start of Tier 3:

| | Source lines | Files |
|---|---|---|
| omega | 6,247 | 38 |
| Tau | 36,414 | 90 |
| Pi | 133,800 | — |

omega is **17% of Tau** and **4.7% of Pi**. Tier 3 narrows that. the product backlog is the rest of it, and
omega is not trying to close it — the goal was always to understand the shape, not to ship a rival.
