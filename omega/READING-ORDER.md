# Reading omega

Thirty files across three packages. This is the order to read them in, and one
line on each saying what it is.

**Read package by package, in dependency order.** `omega_agent` imports nothing
from the other two, so it goes first. `cli.py` imports from everywhere, so it
goes last. That order means you are never reading a file that depends on
something you haven't seen.

Two habits worth having before you start:

- **Every file has a module docstring explaining why it exists.** Read those
  first — they are the short version, and several contain the argument rather
  than just a description.
- **Read `tests/test_loop.py` before `loop.py`.** Each test name is a sentence
  describing required behaviour. It is the spec, written in English.

---

## Package 1 · `omega_agent` — the portable core

**1,580 lines.** Knows about messages, events, tools and turns. Knows nothing
about files, vendors or terminals — and `tests/test_layers.py` fails if that ever
stops being true.

| # | File | Lines | What it is |
|---|---|---|---|
| 1 | `types.py` | 169 | **The vocabulary.** What a message is, what a content block is. Note `tool_calls` and `text` are *properties* — the loop's stop condition is one of them. |
| 2 | `events.py` | 157 | **The 12 stream events.** One model reply arriving, token by token. Every streaming event carries `partial`, the whole message so far. |
| 3 | `agent_events.py` | 166 | **The 10 agent events.** The whole run's progress. A separate file from the 12 on purpose — ask yourself why before reading the answer at the top. |
| 4 | **`provider.py`** | **56** | **The contract. One method.** Read it three times. Then ask why it is in *this* package and not in `omega_ai`. |
| 5 | `tools.py` | 78 | What a tool *is*: a schema plus a handler. Note why tools **raise** instead of returning an error flag. |
| 6 | `hooks.py` | 95 | **The six seams.** Every one is a decision the loop refuses to make. This is why the loop never grew. |
| 7 | `cancellation.py` | 52 | The stop button. Twenty lines, and it *latches* — read why that matters. |
| 8 | **`loop.py`** | **190** | **The machine.** Ask, run what was asked for, repeat. Find the stop condition — it is one line, and it reads content, not metadata. |
| 9 | `tool_runner.py` | 83 | One tool call becomes one tool result. Never raises. Split out of `loop.py` when the loop hit 249 of its 250-line limit. |
| 10 | `harness.py` | 275 | **Who owns the conversation.** Also the orphan repair, which is the fix for the nastiest bug in the whole project. |
| 11 | `session/entries.py` | 48 | What one line of a saved session looks like. Note `parent_id`, present even though nothing branches yet. |
| 12 | `session/jsonl.py` | 99 | Append-only writing, and a reader that survives a half-written last line. |
| 13 | `session/store.py` | 112 | Where sessions live, behind an interface so the backend can change later. |

---

## Package 2 · `omega_ai` — the vendors

**1,201 lines.** The only code in the tree that knows Anthropic or OpenAI exist.
One module per **wire format**, not per vendor.

| # | File | Lines | What it is |
|---|---|---|---|
| 14 | `provider.py` | 22 | A five-line re-export of the contract. The whole file is an argument about import direction. |
| 15 | `fake.py` | 184 | The contract implemented trivially. Written *before* the real adapter, which is why the entire suite runs offline. |
| 16 | `retry.py` | 109 | Which failures are worth retrying, and for how long. A 429 means "not now"; a 400 means "not ever". |
| 17 | `anthropic.py` | 441 | One wire format. The messy file, deliberately — all the vendor ugliness lives here so nothing above it has any. |
| 18 | **`openai.py`** | **445** | A *different* wire format. **Read its docstring table first** — that comparison is the entire argument for the layer, and the tool-result row is the sharpest thing in the codebase. |

---

## Package 3 · `omega_coding` — the application

**1,842 lines.** Files, shells, policy, the screen. Imports the other two;
neither imports it.

| # | File | Lines | What it is |
|---|---|---|---|
| 19 | `paths.py` | 156 | **One resolver**, called by every file tool. Three plausible implementations are wrong and the docstring names them — still true after Tier 2.5 removed the *refusal*, because judging inside-from-outside is the same problem. |
| 20 | `file_lock.py` | 57 | One lock per file, keyed on the *resolved* path. |
| 21 | `truncate.py` | 104 | The output budget: 2,000 lines or 50 KB, keeping the **tail**, because errors are at the end. |
| 22 | `builtin_tools.py` | 365 | The four tools — read, write, edit, run — sitting behind everything above. |
| 23 | `approval.py` | 217 | **The gate.** Fills `before_tool_call`. Note what it refuses outright versus what it asks about, and why the line is drawn where it is. |
| 24 | `redact.py` | 101 | Keeps credentials out of the transcript. Fills `after_tool_call`. |
| 25 | `history.py` | 42 | **Two views of history**: what is kept versus what is sent. The small sibling of the seam compaction will use at Tier 3. |
| 26 | `context.py` | 116 | How full the context window is. Measures a problem it does not fix. |
| 27 | `cost.py` | 96 | What the run cost. Read why it ships **no price table**. |
| 28 | `headless.py` | 113 | Run the agent with no keyboard. Also the benchmark interface, and at Tier 3+ a subagent is this function called from a tool. |
| 29 | `evals.py` | 136 | Does the assembled agent still work? Not a test — read the docstring on the difference. |
| 30 | **`cli.py`** | **386** | **Last.** The composition root: the only interactive entry point that picks a concrete provider. |

---

## Four questions to hold while reading

Ask these of every file. They matter more than remembering the code.

1. **Which package is this in, and could it be in another one?** If you can't
   justify the package, you haven't understood the file yet.
2. **What would break if this were somewhere else?** Several files are where they
   are for one specific reason, and the docstring usually says it outright.
3. **Is this a *decision* or a *mechanism*?** Decisions belong to `omega_coding`,
   reached through a hook. Mechanisms belong to `omega_agent`. That single
   distinction explains most of the layout.
4. **Which of the nine failures does this exist to prevent?** See
   `../dev-notes/03-architecture/02-beginner.md`. Almost every file traces to one.

---

## Which notes pair with which file

`dev-notes/` is a reference, not a book. Read a section when you reach the file it
explains, not front to back.

| After reading | Read |
|---|---|
| `types.py`, `events.py`, `provider.py` | `01-teardown/01-provider-stream.md` §2, §4 |
| `hooks.py`, `loop.py` | `01-teardown/02-agent-loop-tools.md` §3.1 (termination), §2.1 (hooks) |
| `harness.py` (orphan repair) | `01-teardown/02-agent-loop-tools.md` §2.5 |
| `builtin_tools.py`, `truncate.py` | `01-teardown/03-coding-tools.md` §3 |
| `file_lock.py` | `01-teardown/03-coding-tools.md` §4.2 |
| `paths.py`, `approval.py`, `redact.py` | `00-concepts/security.md` §3–5 |
| `session/` | `01-teardown/03b-context-sessions-compaction.md` |
| `session/`, `retry.py`, `file_lock.py`, `cost.py` — or "how do Pi/Tau/Claude Code do this?" | `00-concepts/state-and-delegation.md` — persistence, retry, locking, subagents and multi-session, compared across all four |
| anything, when the layering is unclear | `03-architecture/04-boundaries-and-layout.md` |

---

## You have got it when

You can answer these without looking anything up:

1. Why does the loop stop on `tool_calls` rather than `stop_reason`?
2. Why is `provider.py` in `omega_agent` and not `omega_ai` — and what is
   `omega_ai/provider.py` for?
3. `hooks.py` is in `omega_agent`, but `approval.py` is in `omega_coding`. Why
   the split, when one exists only to fill the other?
4. What does `partial` on every streaming event buy you?
5. Why do tools raise instead of returning an error flag?
6. Why was `fake.py` written before `anthropic.py`?
7. Adding a third provider — which files change?

**If 2, 3 and 7 come easily, the layering has landed**, and that is the entire
point of the three packages.

---

## If you want to prove it to yourself

Reading gives recognition; breaking gives understanding. Each of these should
fail, and the *name* of the failing test tells you what you broke:

| Break this | Watch fail |
|---|---|
| In `loop.py`, stop on `assistant.stop_reason != "toolUse"` instead of on content | `test_stop_condition_follows_content_not_stop_reason` |
| Take `async with table.for_path(path)` off `edit_file` | `test_two_concurrent_edits_to_one_file_both_land` — *"one edit was silently discarded"* |
| In `truncate.py`, keep the head instead of the tail | `test_line_budget_keeps_the_tail` |
| Add `from omega_coding.approval import ApprovalPolicy` to `loop.py` | `test_the_core_imports_nothing_above_it` |
| Import `anthropic` in `harness.py` | `test_exactly_two_files_import_a_vendor_sdk` |

The last two are new, and they are what the three-package split bought you.
