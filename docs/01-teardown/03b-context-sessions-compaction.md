# Layer 3b — Context, Sessions, Compaction & Prompt Caching

`pi/packages/agent/src/harness/{compaction,session}/` + `pi/packages/storage/sqlite-node/`
↔ `tau/src/tau_coding/{context_window,branch_summary}.py` + `tau/src/tau_agent/session/`

> The second half of Layer 3. This is the material Tier 3 depends on, and the material my
> pasted notes were most wrong about. It also closes the two debts Layers 1 and 2 deferred:
> prompt-cache prefix ordering, and how compaction actually integrates.

---

## 1. The problem this layer solves

A coding session runs out of room. Not gradually — a single `npm install` or one large file read
can consume a quarter of the window. And the window is the *only* memory the model has: nothing
persists between requests except what you resend.

So three distinct problems, often confused with each other:

1. **Accounting** — how full is the context right now? You can't manage what you don't measure.
2. **Compaction** — when it's nearly full, how do you shrink history without losing the thread?
3. **Persistence** — how does a session survive process exit, and what exactly gets stored?

And one that only appears once you're paying real money:

4. **Caching** — resending the same 30,000-token prefix on every tool round-trip is the single
   largest cost in a coding agent. How do you get the provider to charge you less for it?

Your note framed the third and fourth: *"context window ke remaining 20% pe summarize"* and
*"cache is based on hash from top to end."* Both are directionally right and both are more
specific in practice. This file makes them exact.

---

## 2. Accounting — deliberately approximate

`tau/src/tau_coding/context_window.py:17-23`

```python
CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4
TOOL_OVERHEAD_TOKENS = 16
SUMMARY_MESSAGE_CHAR_LIMIT = 500
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
DEFAULT_COMPACTION_RESERVE_TOKENS = 16_384
DEFAULT_COMPACTION_KEEP_RECENT_TOKENS = 20_000
COMPACTION_SUMMARY_PREFIX = "Previous conversation summary:\n"
```

and the estimator, `context_window.py:121-125`:

```python
def estimate_text_tokens(text: str) -> int:
    """Return a deterministic rough token estimate for text."""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)
```

**There is no tokenizer.** It's `ceil(len / 4)`.

This is a good decision and worth understanding rather than fixing. A real tokenizer is a heavy
dependency, differs per provider, and would need to be kept in sync with model releases. But
the *decision* it feeds is only "should we compact now?" — and for that you need a cheap
estimate that never wildly under-counts, not an exact figure. Note how often the docstrings say
**"deterministic"**: compaction behaviour must be reproducible and testable, which a
network-fetched or version-drifting tokenizer would break.

The estimate covers three things (`context_window.py:173-190`), and the third is the one people
forget:

```python
def estimate_context_usage(*, system, messages, tools) -> ContextUsageEstimate:
    system_tokens = estimate_text_tokens(system)
    message_tokens = sum(estimate_message_tokens(message) for message in messages)
    tool_tokens = sum(estimate_tool_tokens(tool) for tool in tools)
```

with tools priced as `TOOL_OVERHEAD_TOKENS + name + description + str(input_schema)`
(`context_window.py:146-153`). **Tool schemas are a first-class context cost.** Seven tools with
detailed JSON Schemas is tens of thousands of tokens resent on every single request — which is
exactly what §5's caching work exists to stop paying for.

`estimate_message_tokens` (`context_window.py:128-143`) also counts thinking blocks and tool
call arguments separately, because both are real payload the naive `message_text()` would miss.

---

## 3. Compaction

### 3.1 The threshold is a fixed reserve, not a percentage

`context_window.py:166-170`

```python
def auto_compaction_threshold_for_context_window(context_window_tokens: int) -> int | None:
    """Return Pi-style automatic compaction threshold for a model context window."""
    if context_window_tokens <= 0:
        return None
    return max(1, context_window_tokens - DEFAULT_COMPACTION_RESERVE_TOKENS)
```

> **Threshold = context window − 16,384 tokens.**

On a 128k window that fires at 111,616 — about 87%, not 80%. And the shape matters more than
the number: it's a **fixed reserve**, not a ratio. Pi's `compaction.ts:167` names what the
reserve is for — *"Tokens reserved for summary prompt and output."*

That's why a reserve beats a percentage: you need enough headroom to *run the summarization
request itself*, and that cost is roughly constant regardless of window size. A 20% reserve on
a 1M-token window would waste 200k tokens; a fixed 16k is right at both scales.

Pi splits the reserve further (`compaction.ts:564`):

```typescript
Math.floor(0.8 * reserveTokens),
```

80% of the reserve caps the summary *output*, leaving 20% for the summarization prompt. So the
budgeting is two levels deep.

`DEFAULT_COMPACTION_KEEP_RECENT_TOKENS = 20_000` is the other half of the policy: recent history
is retained verbatim, and only what precedes it gets summarized. **Compaction is
summarize-the-old, keep-the-new** — not summarize-everything.

### 3.2 The summarization prompt — identical in both repos

`tau/src/tau_coding/context_window.py:26-32`:

```python
SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. Your task is to read a conversation "
    "between a user and an AI coding assistant, then produce a structured summary "
    "following the exact format specified.\n\n"
    "Do NOT continue the conversation. Do NOT respond to any questions in the "
    "conversation. ONLY output the structured summary."
)
```

Pi's `compaction.ts:446-448` is the same text (Pi says "an AI assistant" where Tau says "an AI
coding assistant" — otherwise word-for-word, including the emphatic negations).

That "Do NOT continue the conversation" is not boilerplate. It defends against the summarizer's
characteristic failure: you hand a model a conversation ending in a question, and it *answers
the question* instead of summarizing. Stated twice, in caps, because it happens.

The output format (`context_window.py:34-60`):

```
## Goal
## Constraints & Preferences
## Progress
### Done  /  ### In Progress  /  ### Blocked
## Key Decisions
- **[Decision]**: [Brief rationale]
## Next Steps
## Critical Context
```

closing with:

> *"Keep each section concise. Preserve exact file paths, function names, and error messages."*

That last line is the coding-specific insight. Prose can be re-derived; **an exact file path,
function name, or error string cannot.** Those are the tokens whose loss actually breaks the
session, so they're called out explicitly.

### 3.3 Compaction is idempotent by design

There are **three** prompts, not one:

| Prompt | When | Key property |
|---|---|---|
| `SUMMARIZATION_PROMPT` (`:34`) | first compaction | produce the structure |
| `UPDATE_SUMMARIZATION_PROMPT` (`:62`) | every later compaction | merge new messages into `<previous-summary>` |
| `TURN_PREFIX_SUMMARIZATION_PROMPT` (`:95`) | one turn alone is too big | summarize the prefix, keep the suffix |

The update prompt's rules (`context_window.py:65-72`):

```
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it
```

**This is the part that makes long sessions survivable.** Compaction runs many times in a long
session, and a naive implementation re-summarizes the summary each time — a lossy operation
applied repeatedly, so detail decays geometrically. Framing it as *merge new into existing*,
with explicit PRESERVE rules, keeps the loss bounded. The `## Progress` section even models
state transitions (In Progress → Done) so the summary tracks the work rather than just
describing it.

`TURN_PREFIX_SUMMARIZATION_PROMPT` handles the case people forget — the unit that's too large
isn't the history, it's *one turn* (a turn that read three huge files). Summarize the turn's
prefix, keep its recent suffix.

### 3.4 The summary is stored as a plain user message

`context_window.py:268-281`

```python
def _split_previous_compaction_summary(messages):
    first = messages[0]
    if not isinstance(first, UserMessage):
        return None, messages
    text = message_text(first)
    if not text.startswith(COMPACTION_SUMMARY_PREFIX):
        return None, messages
    return text.removeprefix(COMPACTION_SUMMARY_PREFIX), messages[1:]
```

The summary lives as a `UserMessage` whose text starts with `"Previous conversation summary:\n"`
and is recognized by that prefix.

Pragmatic: no new message type, no schema change, works with any provider, survives
serialization for free. Slightly fragile: a user who literally types that prefix confuses the
detector. A dedicated message type (which Pi supports via `CustomAgentMessages`, Layer 2 §4.5)
would be cleaner. Worth knowing both options exist and why the cheap one was chosen.

### 3.5 The summarizer's own input is bounded

`tau/src/tau_coding/branch_summary.py:61-62`

```python
MAX_SUMMARY_SOURCE_MESSAGE_CHARS = 4_000
MAX_SUMMARY_SOURCE_TOTAL_CHARS = 60_000
```

Compaction is a model call, so it has a context limit too — the recursive trap. Caps per message
and in total, with `_trim_summary_source_text` (`branch_summary.py:175`) and a
`remaining_chars` budget walked across messages (`branch_summary.py:124`).

**And file operations are extracted structurally, not left to prose.**
`_branch_file_operations` (`branch_summary.py:199`) pulls the touched-files list out separately;
Pi does the same with `extractFileOperations` / `formatFileOperations`
(`compaction.ts:29-53`), storing them on the compaction entry (`compaction.ts:34`: *"File-operation
details stored on generated compaction entries"*).

That's the right instinct: *which files were touched* is structured data. Trusting a language
model to preserve a file list inside prose is how you lose it.

### 3.6 How compaction reaches the loop — the answer from Layer 2

It doesn't. Layer 2 established that Pi's loop exposes `transformContext`
(`agent/src/types.ts:195`, called at `agent-loop.ts:290`), documented as *"Context window
management (pruning old messages)"*, and compaction is simply an implementation of it, wired in
at `harness/agent-harness.ts:493` and `coding-agent/src/core/sdk.ts:350`.

**An 880-line compaction subsystem attaches to the loop through one optional callback.** That is
the payoff of designing seams before features, and it's the single most important structural
lesson for your Tier 3.

---

## 4. Sessions are trees, not lists

### 4.1 The entry model

`tau/src/tau_agent/session/tree.py:22-40`

```python
def path_to_entry(entries: list[SessionEntry], leaf_id: str) -> list[SessionEntry]:
    """Return the root-to-leaf path for `leaf_id`."""
    by_id = entries_by_id(entries)
    path: list[SessionEntry] = []
    seen: set[str] = set()
    current_id: str | None = leaf_id

    while current_id is not None:
        if current_id in seen:
            raise SessionTreeError(f"Cycle detected at session entry: {current_id}")
        seen.add(current_id)
        entry = by_id.get(current_id)
        if entry is None:
            raise SessionTreeError(f"Missing session entry: {current_id}")
        path.append(entry)
        current_id = entry.parent_id

    path.reverse()
    return path
```

`SessionEntry` has a `parent_id`. A session is therefore a **tree of entries**, and the
conversation you're having is one *path* through it, resolved leaf-to-root then reversed.

This is the biggest structural surprise of Layer 3, and it reframes everything:

- **Branching is native.** Edit an earlier message and you don't destroy history — you create a
  sibling branch from that parent.
- **`branch_summary` now makes sense** as a distinct entry type (`compaction.ts:86`,
  `branch_summary.py`) — separate from `compaction_summary`. One summarizes an abandoned or
  parallel branch; the other compacts the active path.
- **Pi's storage layer matches**: `storage/sqlite-node/src/sqlite/storage/branch-entries.ts`,
  `session-entries.ts`, `session-sequences.ts`, `session-materialized.ts` — the last implying a
  materialized view of the resolved path, because walking the tree on every read would be
  wasteful.

The defensive errors are worth copying: duplicate ids rejected (`tree.py:12-19`), cycles
detected, missing parents named. A corrupted session file should fail loudly at load, not
produce a silently truncated conversation.

### 4.2 Storage is append-only JSONL with migration on read

`tau/src/tau_agent/session/jsonl.py:19-30`

```python
def entry_to_json_line(entry: SessionEntry) -> str:
    """Serialize one session entry using only the canonical Pi wire shape."""
    return _SESSION_ENTRY_ADAPTER.dump_json(entry, exclude_none=True).decode() + "\n"


def entry_from_json_line(line: str, *, line_number: int | None = None) -> SessionEntry:
    """Deserialize one entry, migrating persisted Tau-v1 messages first."""
    location = f" on line {line_number}" if line_number is not None else ""
    try:
        payload = json.loads(line)
        migrated = _migrate_session_entry(payload)
        return _SESSION_ENTRY_ADAPTER.validate_python(migrated)
```

Three decisions:

- **One JSON object per line, appended.** A crash truncates the last line, not the file. No
  rewriting, so writes are cheap and durable.
- **`exclude_none=True`, "canonical Pi wire shape."** The persisted format is the wire format.
  One schema for storage, transport, and memory — no separate DTO layer.
- **Migration happens on read** (`_migrate_session_entry`), with the line number in the error.
  Sessions outlive schema versions, and the format *will* change. Deciding where migration lives
  before you need it is much cheaper than after.

Pi's equivalents: `harness/session/jsonl-store.ts` (355) for the file backend, plus
`storage/sqlite-node/` with real SQL migrations (`migrations/001_initial.sql`) and a
`search-backend.ts` for searching across sessions. Tau has `session/storage.py` (42 lines) and
no SQLite.

---

## 5. Prompt caching — the debt from Layer 1, paid

`tau/dev-notes/prompt-caching.md` is the best document in either repository. It is a written
record of production reasoning, and worth reading in full. The essentials:

### 5.1 The problem, stated concretely

> *"Tau's Anthropic provider parsed and priced cache usage from the very first release … but it
> never placed a single `cache_control` breakpoint in a request. The reporting half was wired up
> and the requesting half was not, so those counters read zero on every real turn and each
> request re-billed the system prompt, the whole tool schema block, and the entire conversation
> as fresh input. In a coding session that is expensive: the tool schemas alone run to tens of
> thousands of tokens and are resent on every tool call."*

Note the failure mode: the *metrics existed and read zero*, and zero looked like "no cache
available" rather than "we never asked." §5.5 is the fix for that class of bug.

### 5.2 Prefix ordering — the precise answer

> *"Anthropic rejects a request carrying more than four `cache_control` markers, and the cache
> prefix is evaluated in `tools` → `system` → `messages` order."*

**That is your note's "hash from top to end," made exact.** The cacheable prefix is
`tools`, then `system`, then `messages` — so anything that changes early invalidates everything
after it. Practical consequences:

- **Tool definitions must be byte-stable across requests.** Iterating a dict in nondeterministic
  order, or including a timestamp in a description, destroys every cache hit downstream.
- **Nothing volatile in the system prompt.** A current-time or token-count line at the top is
  catastrophic; at the very end it costs only itself.
- **Append-only history is what makes message caching work at all.** Editing history in place
  invalidates from the edit point on.

Tau spends all four markers (`prompt-caching.md:16-24`):

1. the **last tool** in the array — caches the whole schema block
2. the **final system block only** — a marker on the earlier OAuth identity block would cache a
   ~15-token prefix that the next marker already covers, wasting a slot
3. and 4. **two message positions** — this request's tail, and the previous request's tail

### 5.3 Two details that only show up in practice

**"Request" is not "user turn."**

> *"One prompt from the user drives as many requests as the agent needs tool-call round trips,
> and all four markers are recomputed on every one of them."*

And because Anthropic sends tool results with `role: "user"`, those message breakpoints
*"land on `tool_result` blocks far more often than on anything a human wrote."*

**Why two message breakpoints instead of one** — this is the subtle one:

> *"Anthropic checks at most 20 block positions back from a breakpoint before giving up. One Tau
> turn appends `2N+2` blocks for `N` tool calls … so a turn with nine or more parallel tool calls
> pushes the previous cache entry outside that window and misses. Marking where the previous
> request ended opens a second lookback window there. Breakpoints are not themselves billed, so
> a marker that already hits costs nothing."*

A 20-block lookback, `2N+2` blocks per turn, so N ≥ 9 breaks it. The second marker is free
insurance. You would never derive this from documentation alone.

The second position is *reconstructed from the payload*, not remembered
(`prompt-caching.md:46-56`) — because the transcript is append-only and each request stops right
before the assistant message it produces, the last user-role message before the final assistant
message *is* the previous request's tail. Two edge cases return an older position, and both
*"only shorten the reusable prefix rather than corrupting anything."* Designing so failures
degrade instead of breaking is the good habit here.

Also: `_mark_cache_breakpoint` only marks `text`/`image`/`tool_result` blocks, promotes a string
`content` to a one-element text block first, and skips empty ones — *"a breakpoint on an empty
block is rejected outright."*

### 5.4 Retention is an auth-mode decision, not a performance knob

`cache_retention` is `"none" | "short" | "long"` (`prompt-caching.md:65-78`):

- **`long`** (`ttl: "1h"`) — Anthropic **subscription OAuth only**. Subscription auth isn't
  billed per token, and the 5-minute default *"is shorter than a test run, a build, or reading a
  diff."*
- **`short`** — provider default, for **API-key** auth, *"so nobody silently pays the 2x
  cache-write premium they did not ask for."*
- **`none`** — no breakpoints, payload byte-identical to the pre-caching shape. Exists because
  gateway providers (`minimax`, `fireworks`, `vercel-ai-gateway`) speak the Anthropic protocol
  without being Anthropic and *"may reject `cache_control` blocks."*

And a genuinely surprising note (`prompt-caching.md:70-74`):

> *"Note that Pi deliberately defaults to five minutes, but for a reason that does not apply
> here: Pi is not a permitted subscription harness, so its users pay per-token API prices where
> the one-hour write premium does not pay off. Claude Code itself uses one hour for its own
> subscription users."*

**Pi's and Tau's defaults differ because of licensing, not engineering.** A reminder that not
every difference between two good codebases is a technical judgement.

Capability and intent are resolved separately (`prompt-caching.md:87-98`): intent from auth mode,
capability from three `compat` booleans layered detected → provider → per-model. *"Capability
only ever narrows intent, so the two compose with no precedence rule."* That's a clean way to
combine two sources of truth — make one a monotone restriction of the other and ordering stops
mattering.

### 5.5 Observability — without it you can't tell it works

> *"Without a visible hit rate there is no way to tell caching is working except by watching a
> rate limit, so `SessionStats` now accumulates `cached_input_tokens` and `cache_write_tokens`
> and exposes a `cache_hit_rate` property."*

And the detail that shows care: the rate is `None` — and omitted from the UI — *"when no
provider in the branch reported any cache activity at all, so backends without prompt caching
are not shown a permanent misleading `0%`."*

**Absent and zero are different values.** Conflating them is how you get a dashboard nobody
trusts. Pi has `coding-agent/src/core/cache-stats.ts` (164) for the same purpose.

### 5.6 The test suite is the specification

> *"`tests/test_prompt_caching.py` covers the awkward transcripts rather than the happy path:
> the four-breakpoint ceiling, a twelve-call parallel turn, adjacent assistant messages, image
> and `tool_result` tails, empty content, and that the caller's messages are never mutated."*

Every item is a bug that was hit. Copy the list; it's a free test plan.

---

## 6. Pi vs Tau — concept versus accident

### Identical

| Concern | Evidence |
|---|---|
| Compaction system prompt, near word-for-word | `context_window.py:26-32` ≡ `compaction.ts:446-448` |
| Structured summary format (Goal/Progress/Key Decisions/Next Steps) | `context_window.py:34-60` ≡ `compaction.ts:450+` |
| Separate first-time vs update prompts, `<previous-summary>` merge | `context_window.py:62`, `compaction.ts:483` |
| "PRESERVE exact file paths, function names, and error messages" | both |
| Fixed token *reserve* rather than a percentage threshold | `context_window.py:166-170`, `compaction.ts:167` |
| Keep recent history verbatim; summarize only the older prefix | both |
| File operations extracted structurally, not left in prose | `branch_summary.py:199`, `compaction.ts:29-53` |
| Char-based token estimation, no tokenizer | both |
| Tool schemas counted as context cost | `context_window.py:146-153` |
| Sessions are trees with `parent_id`; conversation = a path | `tree.py`, `branch-entries.ts` |
| `branch_summary` distinct from `compaction_summary` | `compaction.ts:86`, `branch_summary.py` |
| Append-only JSONL, wire shape == storage shape | `jsonl.py:19-21`, `jsonl-store.ts` |
| Compaction integrates *only* via the context-transform hook | `agent-loop.ts:290` |
| Cache hit-rate surfaced in the UI | `SessionStats`, `cache-stats.ts` |

### Idiomatic (accident)

| Pi | Tau |
|---|---|
| `Math.floor(0.8 * reserveTokens)` | `DEFAULT_COMPACTION_RESERVE_TOKENS` constant |
| TS template literals for prompts | Python string concatenation |
| `tokensBefore` on the entry | `tokens_before` |
| Pydantic `TypeAdapter` validation | TS structural types |

### Genuinely different

**1. Pi has a real storage layer; Tau has a file.** `pi/packages/storage/sqlite-node/` ships SQL
migrations (`migrations/001_initial.sql`), a `search-backend.ts`, `session-materialized.ts`, and
separate `branch-entries` / `session-entries` / `session-sequences` tables. Tau's
`session/storage.py` is 42 lines over JSONL. Pi can search across sessions; Tau can't.

**2. Cache retention defaults differ for licensing reasons** (§5.4) — Pi 5-minute, Tau 1-hour on
subscription OAuth. Not a technical disagreement.

**3. Tau spends its fourth breakpoint differently.** `prompt-caching.md:151-154`: Tau does not
mark Pi's OAuth identity block and *"spends the freed slot on a second message breakpoint."* A
deliberate, documented improvement over the reference implementation — evidence that porting
well means understanding, not transcribing.

**4. Tau's session file is 2,704 lines** (`tau_coding/session.py`), its single largest file. Pi
spreads the same responsibility across `session-manager.ts` (1,712), `agent-session.ts` (3,332),
and `harness/session/*`.

### Correcting my notes, again

| My note | Reality |
|---|---|
| *"context window ke remaining 20% pe summarize"* | Fixed **16,384-token reserve**, not 20%. ~87% on a 128k window. Reserve, because the summarization call itself needs room. |
| *"cache is based on hash from top to end"* | Right — and precisely: `tools` → `system` → `messages`, max **4** breakpoints, **20-block** lookback per breakpoint. |
| *"Pi … doesn't have memory"* | `harness/session/memory-store.ts`, an 880-line compaction subsystem, tree-structured branching sessions, and SQLite persistence. |

### Answering the plan's questions

**"How is conversation state stored and replayed across turns?"** (final answer, spanning
Layers 2 and 3b)

Stored as an **append-only JSONL log of tree-structured `SessionEntry` records**, each with a
`parent_id`; the live conversation is the root-to-leaf path resolved by `path_to_entry`
(`tree.py:22`). Persisted shape == wire shape, with migration applied on read. Replay for the
model is *not* the stored log: Layer 2's `_provider_context` / `convertToLlm` filters it, and
Layer 3b's compaction may replace an old prefix with a summary message carrying the
`"Previous conversation summary:\n"` prefix. Three views total — **durable log**, **resolved
path**, **provider payload**.

**"Where would prompt-cache-friendly prefix ordering matter, and does either repo do it?"**
(debt from Layer 1, closed)

It matters at request assembly, and **yes — Tau does it deliberately and documents the
reasoning**; Pi's `anthropic-messages.ts` is the reference implementation Tau adapted. Order is
`tools` → `system` → `messages`; four breakpoint budget; markers on the last tool, the final
system block, and two message tails. Everything upstream of a breakpoint must be byte-stable,
which is what makes append-only history and deterministic tool serialization load-bearing rather
than stylistic.

---

## 7. If I rebuilt this layer from scratch

**Tier 2 — accounting only.**

1. **`estimate_context_tokens(system, messages, tools)`** with `chars/4`. No tokenizer. Count
   tool schemas — they dominate early.
2. **Show it to the user.** A context gauge makes the next tier's necessity obvious.
3. **Append-only JSONL sessions**, one entry per line, wire shape == stored shape. Add
   `parent_id` to entries **now** even if you always set it to the previous entry — retrofitting
   a tree onto a list is a rewrite.
4. **Migrate on read**, with the line number in any error.

**Tier 3 — compaction.**

5. **`transform_context` on the loop first** (Layer 2, item 14). Compaction plugs into it and
   the loop never changes.
6. **Threshold = `context_window − reserve`**, reserve ≈ 16k. Not a percentage.
7. **Keep recent verbatim** (~20k tokens); summarize only the older prefix.
8. **Copy the three prompts.** They're MIT-licensed, battle-tested, and the "Do NOT continue the
   conversation" guard alone will save you a debugging session. Keep the update-merge variant —
   without it, repeated compaction decays.
9. **Bound the summarizer's own input** (4k/message, 60k total).
10. **Extract file operations structurally.** Don't trust prose to preserve a path list.
11. **Store the summary as a prefixed user message** to start; graduate to a dedicated entry type
    if the prefix ever collides.

**Tier 3 — caching, and treat it as a distinct project.**

12. **Order the payload `tools` → `system` → `messages`** and make each byte-stable. Sort keys
    when serializing schemas. No timestamps or token counts anywhere near the top.
13. **Four breakpoints**: last tool, final system block, current tail, previous tail. The second
    message marker is free and rescues wide parallel turns.
14. **Retention from auth mode**, capability narrowing intent — never the reverse.
15. **Report `cache_hit_rate`, and make "no cache activity" render as absent, not `0%`.**
16. **Write the awkward-transcript tests** from §5.6 before believing any of it works.

**The test that proves this layer:** run a session long enough to compact twice, then ask the
agent about a decision made before the first compaction. If it answers correctly, the
update-merge prompt is doing its job. Then check the cache hit rate is non-zero across turns —
if it's zero, something upstream of a breakpoint isn't byte-stable.

---

## Files read for this layer

| File | Lines | Role |
|---|---|---|
| `tau/dev-notes/prompt-caching.md` | 155 | read in full — §5 |
| `tau/src/tau_coding/context_window.py` | 281 | read in full — accounting + prompts |
| `tau/src/tau_agent/session/tree.py` | 40 | read in full — the tree model |
| `tau/src/tau_agent/session/jsonl.py` | 111 (partial) | storage format |
| `tau/src/tau_coding/branch_summary.py` | 214 (targeted) | summarizer input bounds |
| `pi/packages/agent/src/harness/compaction/compaction.ts` | 880 (targeted) | thresholds, prompts, file ops |

Surveyed, not read: `pi/packages/storage/sqlite-node/**` (migrations, search backend,
materialized views), `harness/session/*.ts` (1,161), `tau_coding/session.py` (2,704),
`session_manager.py` (333), `session_stats.py`.

---

*Next: Layer 4 — the terminal UI (`pi/packages/tui` 14,184 ↔ `tau_coding/tui/`), then
Layer 5 — `protocol`, `storage`, `server`, `client`, `evals`, and the extension system where
subagents and plan mode actually live.*
