# omega — Tier 2

What this tier contains, and what it deliberately leaves to Tier 3.

**Status: closed. Written before the code, kept honest afterwards.**

This file was written as a *contract* at the start of Tier 2 — what would have to be true when
the tier closed — so the work could be checked against a commitment rather than described after
the fact. It has been updated in place as things landed, including the places where reality
disagreed with the plan.

**4,654 lines of source across 32 files · 4,257 lines of tests · 289 tests, all offline.**

| | Tier 1 | Tier 2 | |
|---|---|---|---|
| Source | 1,577 | **4,654** | +3,077 |
| Tests | 499 | **4,257** | +3,758 |
| Test count | 45 | **289** | x6.4 |
| `loop.py` | 151 | **190** | +39 |

**The line estimate was wrong again, in the same direction.** This file predicted
~3,400-3,800 source lines; the answer is 4,654, about 25% over the top of that range. That is the third time an
estimate here has run low: `04-folder-trees.md` predicted ~700 for Tier 1, which came in at
1,577. The pattern is consistent enough to use. **Assume any estimate in these docs is roughly
1.3-2x low**, and read Tier 3's "~4,000" in `docs/06-product-roadmap.md` as nearer 9,000-12,000.

Where the extra went is not mysterious: tests grew faster than source, x8.5 against x3.0. Steps
3, 4 and 8 are mostly *adversarial* tests — symlinked parents, `..` smuggled back through a
rebuilt path, indented secrets, a failure arriving after output — and each is three lines of fix
behind twenty lines of test proving the fix matters.

Companion to `TIER-1.md`. Where that file's Part 2 said *"here is a gap and here is the seam it
plugs into"*, this file's Part 2 does the same thing one tier further out.

---

## Part 0 — Tier 1 → Tier 2, the change in one page

Tier 1's verdict was: *a working agent with real layers — not safe, not persistent, not
interruptible.* Tier 2 addresses exactly those three words, and proves the layers were real.

| | **Tier 1** | **Tier 2** |
|---|---|---|
| **Verdict** | the loop works | it is usable |
| Scope | a scratch directory | a real repository |
| Who owns the conversation | `cli.py` holds a bare `list` | `harness.py`, an object |
| Event vocabulary the app sees | the **12** provider events, passed straight through | the **10** agent events, emitted by the loop |
| Ctrl-C | kills the process, may corrupt the transcript | cancels the turn; transcript stays valid |
| Closing the terminal | everything is lost | session is on disk, `--resume` picks it up |
| Editing a file | whole-file rewrite only | exact-match `edit`, one lock per path |
| `rm -rf` | runs | asks first |
| Reading `~/.ssh` | allowed | refused, in one place |
| A 429 | ends the run | retried below the event boundary |
| Providers | one, **unproven** | two, and the second one is the proof |
| Auth | a static `api_key` string | a resolver callback, resolved per request |
| Tokens | captured, unused | summed, priced, and shown |
| Loop hooks | none | six of the nine declared, six wired |

### The nine failures, tier by tier

From `../docs/03-architecture/02-beginner.md`. This is the honest scorecard.

| Failure | Fixed in |
|---|---|
| **#2** one big output kills the session | Tier 1 — truncation |
| **#6** nothing appears until it finishes | Tier 1 — streaming |
| *no turn limit* | Tier 1 — `max_turns` |
| **#3** Ctrl-C corrupts the conversation permanently | **Tier 2** |
| **#4** it deletes something you wanted | **Tier 2** |
| **#5** one rate limit throws away the run | **Tier 2** |
| **#8** two edits to one file lose data | **Tier 2** |
| *no persistence* | **Tier 2** |
| **#7** switching providers means a rewrite | Tier 1 built the seam · **Tier 2 proves it** |
| **#1** context fills up and dies | Tier 3 — compaction |
| **#9** it costs more than it should | Tier 3 — prompt caching |

Tier 2 adds the *instruments* for #1 and #9 — a context gauge and a cost total — without the
fixes. You will be able to watch both problems approach before Tier 3 solves them.

---

## Part 1 — What Tier 2 has

### Layer 1 · Provider

| Capability | File | Why it exists |
|---|---|---|
| **A second adapter** | `providers/openai.py` | Not a feature — the exam. Chat Completions is a genuinely different wire format |
| Retry with backoff | `providers/retry.py` | **Below the event boundary**, so the loop never learns a retry happened — failure **#5** |
| Auth resolver callback | `provider.py`, both adapters | Resolved immediately before each request, so a token can refresh mid-session |
| One wire format per adapter | `providers/` | `openai.py` also reaches Groq, Together, Ollama and vLLM — the format is the unit, not the vendor |

**The provider interface itself does not change.** If it had to, Tier 1 was wrong.

### Layer 2 · Agent core

| Capability | File | Why it exists |
|---|---|---|
| **The 10 agent events** | `agent_events.py` | Coarse progress — `turn_start`, `tool_execution_start`. A **separate file** from the 12 |
| **The harness** | `harness.py` | Something has to own `messages`, or persistence, cancellation and the queues have nowhere to live |
| **Hook bundle** | `hooks.py` | `before_tool_call` · `after_tool_call` · `convert_to_llm` · `transform_context` · the two queue sources. **Not** `get_api_key` — auth is a provider-level resolver, one layer lower |
| Concrete cancellation | `harness.py` | Tier 1 threaded the `signal` everywhere and created nothing. This creates one |
| **Orphaned tool-result repair** | `harness.py` | An unanswered tool call is a **permanent** API error. Runs on interrupt *and* on resume |
| Steering queue | `harness.py` | Type while it works; picked up between turns |
| Follow-up queue | `harness.py` | Queue the next task instead of waiting |
| Two views of history | `hooks.py`, `history.py` | What you see vs what gets sent. `convert_to_llm` does the small version now; `transform_context` is where compaction lands at Tier 3 |
| Context gauge | `context.py` | chars/4, counts tool schemas. Measures failure **#1**; does not fix it |
| Cost accounting | `cost.py` | Sums the `Usage` Tier 1 already captured. Prices it only if you supply a price |

**`loop.py` must not pass ~250 lines.** It was 151 at the end of Tier 1; Tau's is 318. Growth
means a *decision* leaked in where only the *mechanism* belongs.

The tripwire fired for real. Adding the between-turns queues took `loop.py` to **249**, and the
response was to extract `tool_runner.py` — how one tool call becomes one tool result is a separate
mechanism that happens to be called from the loop. `loop.py` came back to **190**. The rule did
not block the feature; it identified which part of the file had stopped being the loop.

### Layer 3 · Coding app

| Capability | File | Why it exists |
|---|---|---|
| **`edit` tool** | `builtin_tools.py` | Exact-match replace, unique match required. Whole-file writes are hopeless past a few hundred lines |
| **Path resolution** | `paths.py` | **One** resolver, called by every filesystem tool. Symlinks resolved before the inside/outside verdict — failure **#4**. Tier 2 refused outside paths here; Tier 2.5 moved that to the gate and kept the refusal behind `--confine` |
| **Per-path write lock** | `file_lock.py` | Keyed on the *resolved* path, shared by `write` and `edit` — failure **#8** |
| **Approval gate** | `approval.py` | Fills `before_tool_call`. Prompts on shell and writes, remembers the answer, blanket-denies the catastrophes |
| Secret redaction | `redact.py` | Fills `after_tool_call`. Key-shaped strings never reach the model or a log |
| `prepare` seam on the shell tool | `builtin_tools.py` | So Tier 3+ sandboxing lands without surgery |
| `run_shell` timeout | `builtin_tools.py` | A hung command used to hang forever |
| Project instruction file | `cli.py` | `OMEGA.md` prepended to the system prompt. Stop retyping the conventions |

### Layer 3 · Sessions

| Capability | File | Why it exists |
|---|---|---|
| **Session persistence** | `session/jsonl.py` | Append-only, migrate-on-read. Pydantic makes this `model_dump_json` |
| Entries carry `parent_id` | `session/entries.py` | Branching is Tier 3, but **retrofitting a tree onto a list is a rewrite** |
| Storage interface | `session/store.py` | So the backend can change without the harness noticing |
| `--resume` / `--continue` | `cli.py` | Resume runs orphan repair first |

### Interface & harness

| Capability | File |
|---|---|
| REPL driving the harness, not the loop | `cli.py` |
| Tool activity shown *while* it runs | `cli.py` |
| Context % and running cost | `cli.py` |
| `--provider` selection | `cli.py` |
| **Headless driver** — prompt in, messages out | `headless.py` |
| Smoke eval | `evals/smoke.py` |

The headless driver is also the terminal-bench interface. Built once, used twice.

### Verified at close-out

- **289 tests in 1.7s, all offline.** No network, no API key, no cost.
- `mypy --strict` clean across 32 source files · `ruff` clean.
- **Ctrl-C mid-tool-call, then `--resume`: the next request succeeds.** Two tests cover the
  interrupt landing *inside* a tool, which is the case that actually happens.
- Writing outside the working directory is refused, and a refused write creates no directories
  on its way to being refused.
- A recursive delete aimed at a filesystem root is refused outright; the same operation aimed at
  a build directory is prompted, not blocked.
- **`--provider openai` runs the same application code** — asserted by a contract suite running
  identical assertions against both adapters, and by one test driving the whole stack (headless
  driver, harness, real tools, gate, redaction) on either provider.
- **Exactly two files import a vendor SDK**, both in `omega_ai/` — and this is now a test rather
  than a `grep`: `tests/test_layers.py::test_exactly_two_files_import_a_vendor_sdk`.
- The failure-#8 race is real, not decorative: take the per-path lock out and
  `test_two_concurrent_edits_to_one_file_both_land` fails with *"one edit was silently
  discarded"*.

---

## Part 2 — What Tier 2 lacks, and where Tier 3 puts it

Same test as Tier 1: every gap below must plug into a seam that already exists. If any of these
would force a change to `loop.py`, `provider.py`, or either event vocabulary, the layering is
wrong and that is the thing to fix.

### The two remaining beginner failures

| Missing | What it costs | The seam it plugs into |
|---|---|---|
| **Compaction** — failure **#1** | Long tasks still die at the context limit. Tier 2 only *shows* you the wall approaching | `transform_context`, wired and doing real work in Tier 2. Pi's 880-line compaction subsystem plugs into exactly this one callback, and the loop contains **zero** lines of it |
| **Prompt caching** — failure **#9** | Every turn re-bills the system prompt and the whole tool schema block | Not a hook — a *constraint*. Cache markers need a byte-identical prefix, which is why `OMEGA.md` is prepended once at startup and never regenerated per turn |

### Things Tier 3 adds next

| Missing | What it costs today | The seam |
|---|---|---|
| **Session branching** | You can rewind by reading the JSONL, but not fork and navigate | `parent_id` is already on every entry. Tier 3 adds `tree.py` — `path_to_entry`, cycle detection |
| **Search tools** — `grep`, `find`, `ls` | It shells out to `rg`, which works but has no output budget of its own | Three more `Tool` objects. `truncate_output()` exists; path resolution via `paths.py` exists |
| **A real TUI** | Print output can't show a diff, a spinner, or a sidebar | The 10 agent events **are** the UI contract. Tau's whole agent→UI bridge is 99 lines because of this |
| **Structured logging** | Debugging means reading print output | The same event stream the UI subscribes to. A second listener, not a new mechanism |
| **Image reading** | Screenshots can't be handed to the model | `types.py` content blocks are a discriminated union; an image block is an addition |
| **Subagents *or* plan mode** | No task decomposition | `before_tool_call` plus the headless driver. A subagent *is* the headless driver called from a tool |

### Deliberately not in Tier 3 either

Recorded so the absence is a decision and not an oversight: no MCP, no retrieval/RAG. Neither
reference implements them. Sandboxing is Tier 3+, and its `prepare` seam ships in Tier 2 so it
lands without surgery.

### Known rough edges, stated plainly

- **No price table ships.** A hardcoded dollars-per-million-tokens table is wrong the moment
  prices change or a model is renamed, and a *confidently wrong* cost figure is worse than none —
  it gets believed. Token counts are always shown; dollars only when `OMEGA_PRICE_INPUT` and
  `OMEGA_PRICE_OUTPUT` are set. Tau's provider catalog is the real answer, and it is Tau-parity work.
- **`--fake` reports zero tokens**, because it sends nothing. Correct, and briefly confusing.
- **Steering cannot actually be typed yet.** The queues are wired and the loop drains them between
  turns, but a `print`/`input` REPL has no way to accept a keystroke while a turn is running. A
  Tier 3 TUI is what makes them reachable; until then they are usable programmatically
  (`harness.queue_steering(...)`) and covered by tests.
- **The context gauge is chars/4.** It never claims to be exact — only never wildly wrong.
- **Tool calls still execute sequentially.** Tau's do too, despite advertising otherwise.
- **`--fake` still replays a fixed script.** It demonstrates machinery, not intelligence.
- **Approval memory is per-session.** No persisted trust store; that arrives with config.
- **Ctrl-C during an approval prompt does not return immediately.** The prompt reads stdin on a
  worker thread, and a signal cannot interrupt a blocked `input()`. The cancellation is recorded
  and takes effect as soon as the prompt is answered.
- **Nothing is sandboxed.** The approval gate is policy, not containment — and since Tier 2.5 it is the *only* control on where the file tools reach, `--confine` aside.
- **The retry wrapper is duplicated between the two adapters.** Same shape in `anthropic.py` and
  `openai.py`, because it is provider-layer *machinery* rather than vendor translation.
  `providers/streaming.py` is the obvious next refactor. Worth noting where the duplication sits:
  inside `providers/`, which is exactly where the boundary said vendor concerns belong. The
  abstraction leaked nothing upward; it repeated itself sideways.
- **`stream_options` may not be accepted by every OpenAI-compatible endpoint.** It is sent because
  without it a streamed response reports no usage at all and the cost counter silently reads zero.
  An older Ollama or vLLM build may reject it; the fix is a newer server, not per-endpoint guessing.
- **`omega.evals --real` only speaks to Anthropic.** The smoke eval has no `--provider` flag yet.
- **`run_shell` is not path-confined.** `cd .. && cat ~/.ssh/id_rsa` walks straight out of the
  root, and parsing shell commands to prevent that is a game you lose. The shell is covered by
  the approval gate instead; real containment is Tier 3+ sandboxing, and `prepare_shell` in
  `builtin_tools.py` is the seam it plugs into.

---

## The one-line summary

Tier 1 proved the loop terminates. **Tier 2 makes it safe to point at a real repository** — it can
be interrupted without corruption, it remembers, it asks before it destroys, and its provider
abstraction is no longer a claim but a measured result. What it still cannot do is survive a task
long enough to fill the context window, and that is the whole of Tier 3.
