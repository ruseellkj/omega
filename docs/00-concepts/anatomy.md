# Anatomy of a Terminal Coding Agent

What one is *made of* — capabilities and concerns, not tech stack.

Every entry cites `../01-teardown/` for evidence, or says plainly that neither reference
implements it. Four groups, in the order you'd build them:

- **A. Core** — required for anything to function at all
- **B. Coding-specific** — required because the domain is code
- **C. Scale** — required once sessions get long or tasks get big
- **D. Production** — required once real users and real filesystems are involved

Each entry follows the same shape: *what it is · what breaks without it · how Pi/Tau do it ·
which tier*.

Tier key: **T1** = the loop works · **T2** = it's usable · **T3** = it survives long tasks ·
**T3+** = optional, later.

---

## Summary table

| # | Component | Group | Tier |
|---|---|---|---|
| 1 | Provider abstraction | Core | T1 |
| 2 | Streaming + event vocabulary | Core | T1 |
| 3 | Message & content model | Core | T1 |
| 4 | The agent loop | Core | T1 |
| 5 | Termination condition | Core | T1 |
| 6 | Tool schemas | Core | T1 |
| 7 | Tool dispatch & result formatting | Core | T1 |
| 8 | System prompt assembly | Core | T1 |
| 9 | Turn limit | Core | T1 |
| 10 | In-band error handling | Core | T1 |
| 11 | The four file/shell tools | Coding | T1 |
| 12 | Output truncation & spillover | Coding | T1 |
| 13 | Exact-match editing | Coding | T2 |
| 14 | Write serialization (per-path lock) | Coding | T2 |
| 15 | Search tools | Coding | T3 |
| 16 | Project instruction files | Coding | T2 |
| 17 | Image support | Coding | T3 |
| 18 | Cancellation | Scale | T2 |
| 19 | Orphaned tool-result repair | Scale | T2 |
| 20 | Two views of history | Scale | T2 |
| 21 | Session persistence & resume | Scale | T2 |
| 22 | Context accounting | Scale | T2 |
| 23 | Compaction | Scale | T3 |
| 24 | Prompt caching | Scale | T3 |
| 25 | Session branching | Scale | T3 |
| 26 | Steering & follow-up queues | Scale | T2 |
| 27 | Retry & backoff | Production | T2 |
| 28 | Cost & token accounting | Production | T2 |
| 29 | Permissions & approval gates | Production | T2 |
| 30 | Path confinement | Production | T2 |
| 31 | Sandboxing | Production | T3+ |
| 32 | Secret handling | Production | T2 |
| 33 | Terminal UI | Production | T3 |
| 34 | Observability / logging | Production | T3 |
| 35 | Headless harness & evals | Production | T2 |
| 36 | Extension system | Production | T3 |
| 37 | Skills | Production | T3+ |
| 38 | Subagents / orchestration | Production | T3+ |
| 39 | Plan mode | Production | T3+ |
| 40 | Client/server split | Production | T3+ |
| 41 | Retrieval / RAG | — | **skip** |
| 42 | MCP | — | **T3+, optional** |

---

## A. Core

### 1. Provider abstraction
**What:** one interface every model vendor is reached through.
**Without it:** every vendor quirk leaks into the loop, the tools, and the renderer. Adding a
second provider means editing all of them.
**Pi/Tau:** a single method — `stream_response(model, system, messages, tools, signal)`. Defined
in the *portable core*, implemented by adapters, so the dependency points inward.
→ `01-provider-stream.md` §2.1
**Tier:** T1. Second adapter in T2 — that's the only real test the abstraction works.

### 2. Streaming + event vocabulary
**What:** a fixed set of events describing a response as it arrives. Twelve in both repos:
`start`, `text_start/delta/end`, `thinking_start/delta/end`, `toolcall_start/delta/end`, `done`,
`error`.
**Without it:** you wait for the full response before showing anything, and every consumer
invents its own progress representation.
**Pi/Tau:** identical twelve, same `type` strings, in both languages. Every streaming event also
carries `partial` — the whole message so far — so consumers stay stateless.
→ `01-provider-stream.md` §2.2–2.3
**Tier:** T1. **Get the names right; everything above depends on them.**

### 3. Message & content model
**What:** `user` / `assistant` / `toolResult` messages; content blocks of `text`, `thinking`,
`toolCall`, `image`.
**Without it:** you can't represent a tool result, reasoning, or an image — and `toolResult`
smuggled inside a user message breaks providers.
**Pi/Tau:** `toolResult` is its own role. Thinking blocks carry opaque provider signatures that
must be replayed verbatim or multi-turn reasoning breaks.
→ `01-provider-stream.md` §2.2, §3
**Tier:** T1.

### 4. The agent loop
**What:** ask the model → run requested tools → feed results back → repeat.
**Without it:** you have a chatbot.
**Pi/Tau:** a nested `while`. Outer loop drains follow-ups; inner loop runs turns. Tau's is
**318 lines** total.
→ `02-agent-loop-tools.md` §3.1
**Tier:** T1.

### 5. Termination condition
**What:** the rule for when to stop.
**Without it:** infinite loop, or stopping one turn too early.
**Pi/Tau:** stop when the assistant message contains **no tool calls** and no messages are
queued. Branch on actual content, *not* on `stop_reason` — content is ground truth.
→ `02-agent-loop-tools.md` §3.1
**Tier:** T1.

### 6. Tool schemas
**What:** a machine-readable description of each tool (JSON Schema) plus a description of when
to use it.
**Without it:** the model can't call anything correctly.
**Pi/Tau:** a tool is more than schema + handler — it also carries prompt text
(`prompt_snippet`, `prompt_guidelines`) and renderers (`render_call`, `render_result`). Adding a
tool is adding one object, not editing four files.
→ `02-agent-loop-tools.md` §2.3
**Tier:** T1.

### 7. Tool dispatch & result formatting
**What:** look up by name, run it, turn the outcome into a message the model reads.
**Without it:** tool calls go nowhere.
**Pi/Tau:** *every* outcome — success, blocked, cancelled, unknown tool, crash — becomes a normal
`ToolResultMessage` keyed by `tool_call_id`. Errors are observations the model can react to, not
exceptions that end the run. Tools are an explicit isolation boundary.
→ `02-agent-loop-tools.md` §3.4–3.5
**Tier:** T1.

### 8. System prompt assembly
**What:** the instructions sent with every request.
**Without it:** the model doesn't know it's a coding agent, what tools exist, or what the
environment is.
**Pi/Tau:** assembled from templates plus per-tool snippets — `system_prompt.py` (205) +
`prompt_templates.py` (212) in Tau; `system-prompt.ts` + `prompt-templates.ts` in Pi.
**Keep it byte-stable across requests** or you destroy prompt caching (#24).
→ `03b-context-sessions-compaction.md` §5.2
**Tier:** T1.

### 9. Turn limit
**What:** a hard cap on loop iterations.
**Without it:** a confused model burns your budget in a runaway loop. This is the first bug you
will hit.
**Pi/Tau:** `max_turns`, checked before each turn; exceeding it produces a normal error message,
not a crash.
→ `02-agent-loop-tools.md` §3.1
**Tier:** T1.

### 10. In-band error handling
**What:** failures travel as data (an `error` event / an error message), not as thrown
exceptions.
**Without it:** a stream that emitted 500 tokens and then failed loses the 500 tokens. An
exception carries the failure but not the partial content.
**Pi/Tau:** stated as an explicit contract, and the adapter *synthesizes* a terminal event if a
provider dies silently — so the loop can assume exactly one `start` and one terminal event,
always.
→ `01-provider-stream.md` §3.1, §3 step 7
**Tier:** T1. **Counter-intuitive and worth internalizing: in streaming systems, failures are
data.**

---

## B. Coding-specific

### 11. The four file/shell tools
**What:** `read`, `write`, `edit`, `bash`.
**Without them:** it isn't a coding agent.
**Pi/Tau:** **exactly these four** in both portable layers — verified from Pi's
`harness/tools/index.ts` exports and Tau's registered tool names. `bash` is the universal escape
hatch that subsumes everything else.
→ `03-coding-tools.md` §2
**Tier:** T1 (`read`, `write`, `bash`); `edit` in T2.

### 12. Output truncation & spillover
**What:** cap tool output, keep the *tail*, write the full text to a temp file, and tell the
model the path.
**Without it:** one `npm install` eats a quarter of your context window.
**Pi/Tau:** same budget in both — **2,000 lines or 50 KB**, whichever hits first. The notice
states which limit was hit and the absolute line range. Tau even embeds the recovery command
(`sed -n '...p' file`).
→ `03-coding-tools.md` §3.4
**Tier:** T1. **Principle: truncation isn't data loss if you tell the model how to get the
rest.**

### 13. Exact-match editing
**What:** edits specify `oldText` → `newText`, never line numbers.
**Without it:** line numbers go stale and the agent corrupts files silently.
**Pi/Tau:** exact string matching, failing loudly when not found — so the model re-reads instead
of guessing. `edit-diff` is the largest tool file in both repos (500–560 lines); applying edits
reliably is the hard part.
→ `03-coding-tools.md` §4.3
**Tier:** T2.

### 14. Write serialization (per-path lock)
**What:** one writer at a time per resolved file path.
**Without it:** two parallel edits to one file both read the original and the second silently
discards the first.
**Pi/Tau:** Pi has `file-mutation-queue.ts`; Tau uses a per-path async lock — **even though
Tau's loop never parallelizes**, as defence against a future change.
→ `03-coding-tools.md` §4.2
**Tier:** T2.

### 15. Search tools (`grep` / `find` / `ls`)
**What:** bounded, formatted search with sensible ignores.
**Without it:** the model runs `grep -rn` through `bash` and gets unbounded output including
`node_modules`.
**Pi/Tau:** **Pi's coding app adds three** (`grep` 385, `find` 374, `ls` 225); **Tau has none** —
its model uses `bash`. So these are an *optimization over `bash`*, not a necessity.
→ `03-coding-tools.md` §2, §5
**Tier:** T3. Let the need prove itself.

### 16. Project instruction files
**What:** per-repo instructions the agent reads on start (the `CLAUDE.md` / `AGENTS.md` idea).
**Without it:** you retype project conventions every session.
**Pi/Tau:** Tau has "project instructions" in `tau_coding`; Pi ships it as an **extension**
(`claude-rules.ts`).
→ `05-beyond-the-core.md` §6
**Tier:** T2. Cheap, high payoff.

### 17. Image support
**What:** read images into `ImageContent` blocks.
**Without it:** no screenshots, no diagrams.
**Pi/Tau:** both, with a byte ceiling and an explicit refusal message when exceeded. Pi also does
image *generation*; Tau doesn't.
→ `03-coding-tools.md` §4.1
**Tier:** T3.

---

## C. Scale

### 18. Cancellation
**What:** Ctrl-C stops the agent, not the process.
**Without it:** your only interrupt is killing the process, losing the session.
**Pi/Tau:** a cancellation token threaded from the harness into the loop, the provider, and every
tool. In Python, `CancelledError` must be re-raised *before* any broad `except`.
→ `02-agent-loop-tools.md` §3.5
**Tier:** T2.

### 19. Orphaned tool-result repair
**What:** synthesize a result for any tool call that never got one.
**Without it:** Ctrl-C during a tool leaves an unanswered tool call, and **every subsequent
request is a hard API error.** The session is bricked.
**Pi/Tau:** both, at different layers — Tau in the harness (visible in the transcript), Pi at the
wire boundary (invisible, non-destructive). Do both: honest message + wire-level backstop.
→ `02-agent-loop-tools.md` §2.5, §4.4
**Tier:** T2, **the same day you add cancellation.** This is a shipped-software detail nobody
designs up front.

### 20. Two views of history
**What:** the durable transcript is not the same list you send the model.
**Without it:** one failed turn poisons every later request, because providers reject
content-less assistant turns.
**Pi/Tau:** Tau filters with `_provider_context`; Pi generalizes it into a required
`convertToLlm` hook. Keep failures in history for diagnostics; filter them at the boundary.
→ `02-agent-loop-tools.md` §3.2
**Tier:** T2. **Also the seam compaction later plugs into — build it even if it only drops empty
failures.**

### 21. Session persistence & resume
**What:** sessions survive process exit.
**Without it:** every crash is a total loss.
**Pi/Tau:** append-only **JSONL**, one entry per line, with the *wire shape as the stored shape*
and migration applied on read. Pi adds a SQLite backend with real SQL migrations and
cross-session search.
→ `03b-context-sessions-compaction.md` §4.2
**Tier:** T2.

### 22. Context accounting
**What:** know how full the context is.
**Without it:** you can't decide when to compact. You hit a hard provider error instead.
**Pi/Tau:** **no tokenizer** — `chars / 4`, deliberately deterministic. Counts system prompt,
messages, *and tool schemas* (which dominate early).
→ `03b-context-sessions-compaction.md` §2
**Tier:** T2. **You don't need exact counts, just a cheap estimate that never wildly
under-counts.**

### 23. Compaction
**What:** replace old history with a structured summary; keep recent history verbatim.
**Without it:** long sessions die at the context limit, or the model starts hallucinating as it
fills.
**Pi/Tau:** threshold = `context_window − 16,384` (a **fixed reserve**, not a percentage — the
summarization call itself needs room). Keeps ~20k recent tokens verbatim. **Three** prompts: a
first-time one, an *update-merge* one that folds new messages into `<previous-summary>`, and one
for a single over-large turn. The prompt text is near-identical in both repos.
→ `03b-context-sessions-compaction.md` §3
**Tier:** T3. Attaches to #20's seam and needs **zero** loop changes.

### 24. Prompt caching & prefix stability
**What:** get the provider to charge less for the prefix you resend every turn.
**Without it:** you re-bill the system prompt, all tool schemas, and the whole conversation on
*every tool round-trip*. This is the single largest cost in a coding agent.
**Pi/Tau:** documented in detail. Prefix order is **`tools` → `system` → `messages`**, max **4**
breakpoints, and a breakpoint only looks back **20 blocks**. Everything upstream of a breakpoint
must be byte-stable — so deterministic schema serialization and append-only history become
load-bearing, not stylistic.
→ `03b-context-sessions-compaction.md` §5
**Tier:** T3, as its own project.

### 25. Session branching
**What:** sessions are a **tree** of entries (each with a `parent_id`); the live conversation is
one root-to-leaf path.
**Without it:** editing an earlier message destroys history instead of forking it.
**Pi/Tau:** both. Pi's SQLite schema has separate `branch-entries` tables and a materialized view
of the resolved path.
→ `03b-context-sessions-compaction.md` §4.1
**Tier:** T3 — but **put `parent_id` on entries in T2.** Retrofitting a tree onto a list is a
rewrite.

### 26. Steering & follow-up queues
**What:** two queues for messages typed while the agent works. *Steering* injects between turns;
*follow-up* injects after the run would have ended.
**Without it:** typing while the agent runs either does nothing or corrupts the turn.
**Pi/Tau:** both, with a `queue_mode` controlling one-at-a-time vs drain-all.
→ `02-agent-loop-tools.md` §2.4
**Tier:** T2. Cheap, and it's what makes the agent feel interactive.

---

## D. Production

### 27. Retry & backoff
**What:** retry transient provider failures.
**Without it:** a single 429 ends the run.
**Pi/Tau:** handled *inside* the provider layer and deliberately hidden — retry events are
swallowed at the adapter boundary so the loop never learns attempt 1 failed.
→ `01-provider-stream.md` §3 step 1
**Tier:** T2.

### 28. Cost & token accounting
**What:** track input/output/cache-read/cache-write tokens and money.
**Without it:** no idea what a session costs, and no way to tell whether caching works.
**Pi/Tau:** Pi's `Usage` breaks out `cacheRead`, `cacheWrite`, `cacheWrite1h`, `reasoning`, plus
tiered pricing. Report cache hit rate as **absent, not `0%`**, when no provider reported cache
activity.
→ `01-provider-stream.md` §4, `03b` §5.5
**Tier:** T2.

### 29. Permissions & approval gates
**What:** ask before running a tool.
**Without it:** the agent runs `rm -rf` unsupervised.
**Pi/Tau:** the loop knows nothing about permissions. It calls `before_tool_call(call) →
(blocked, reason)`. That one callback is the entire seam; Pi ships `permission-gate`,
`confirm-destructive`, `protected-paths`, `project-trust`, `dirty-repo-guard`, `timed-confirm` as
extensions on it.
→ `02-agent-loop-tools.md` §2.1, `05-beyond-the-core.md` §6
**Tier:** T2. See `security.md`.

### 30. Path confinement
**What:** every filesystem tool resolves the path, then verifies it's inside the working
directory.
**Without it:** `../../../.ssh/id_rsa`.
**Pi/Tau:** one validation function all file tools call.
→ `03-coding-tools.md` §4.1
**Tier:** T2. **One place, not per-tool.**

### 31. Sandboxing
**What:** run shell commands in a restricted environment.
**Without it:** the blast radius is your whole machine.
**Pi/Tau:** the *portable* bash tool has **no sandboxing** — it runs whatever it's given.
Confinement is injected through a `prepare` hook that can rewrite the command. Pi ships
`sandbox/` as an extension.
→ `03-coding-tools.md` §3.6
**Tier:** T3+. Add the `prepare` seam in T2 so this lands later without surgery.

### 32. Secret handling
**What:** credentials from env only; never logged, never in the transcript.
**Without it:** API keys end up in session files you might commit.
**Pi/Tau:** Tau's provider layer takes an auth *resolver callback* — it never learns what OAuth
is or touches a token store. Pi keeps OAuth flows inside the provider package.
→ `01-provider-stream.md` §4.3
**Tier:** T2. See `security.md`.

### 33. Terminal UI
**What:** the interactive front end.
**Without it:** you have a script, not a tool.
**Pi/Tau:** the biggest divergence between them — Pi *builds its own* (14,184 lines, two runtime
deps), Tau *uses Textual*. The UI is only an event subscriber: Tau's bridge is **99 lines**.
→ `04-terminal-ui.md` §2
**Tier:** T3, **on a framework.** Print statements are correct for T1–T2.

### 34. Observability / logging
**What:** structured logs, diagnostics on messages.
**Without it:** you can't debug a session after the fact.
**Pi/Tau:** both attach `diagnostics` to assistant messages; Tau has `diagnostics.py`.
**Tier:** T3.

### 35. Headless harness & evals
**What:** run the agent programmatically — feed a task, inspect the result.
**Without it:** you can't test end to end, and you can't benchmark at all.
**Pi/Tau:** Pi's whole eval suite is **414 lines**, and `smoke.eval.ts` is **17**. Tau has none.
→ `05-beyond-the-core.md` §4
**Tier:** T2. **This is also the terminal-bench interface** — build it once, use it twice.

### 36. Extension system
**What:** let users change behaviour without forking.
**Without it:** every preference becomes a config flag or a patch.
**Pi/Tau:** Pi ships ~**75** example extensions; Tau's system is 4 files. Crucially, **every
extension plugs into a hook that already existed** for its own reasons. Enumerate your hooks,
then expose them — don't design the API first.
→ `05-beyond-the-core.md` §6
**Tier:** T3.

### 37. Skills
**What:** reusable prompt/instruction bundles loaded on demand.
**Without it:** you paste the same guidance repeatedly.
**Pi/Tau:** both have it — Pi `harness/skills.ts` (375) + `core/skills.ts` (487), Tau
`skills.py` (239).
**Tier:** T3+.

### 38. Subagents / orchestration
**What:** an agent that spawns nested agents.
**Without it:** one context does all the work.
**Pi/Tau:** **an extension, not core** (`examples/extensions/subagent/`). And it needs no special
support — **a subagent is just a tool whose implementation runs another loop.**
→ `05-beyond-the-core.md` §6
**Tier:** T3+. Not before the single loop is solid.

### 39. Plan mode
**What:** a read-only mode that produces a plan before touching files.
**Without it:** the agent starts editing before you've agreed on the approach.
**Pi/Tau:** **an extension** — confirmed by `cli/args.ts:300`: *"Extensions can register
additional flags (e.g. `--plan` from plan-mode extension)."* Built from `before_tool_call`
(block writes) + dynamic tools.
→ `05-beyond-the-core.md` §6
**Tier:** T3+.

### 40. Client/server split
**What:** the agent runs as a process other front-ends attach to.
**Without it:** one UI, same process; a UI crash loses the session.
**Pi/Tau:** Pi only — length-prefixed **CBOR** frames (4-byte big-endian header, 16 MB cap) over
a Unix socket, with constant-time token auth and snapshot-then-deltas for late joiners. Not
HTTP+JSON, not gRPC. Tau is single-process.
→ `05-beyond-the-core.md` §2–3
**Tier:** T3+. Only when you want a second front-end.

---

## What to deliberately NOT build

### 41. Retrieval / RAG / embeddings — **skip**
**Neither repo has any of it.** No vector store, no embedding model, no semantic search
anywhere in ~145,000 lines. Code search is `grep`.
A model that can read files and run `grep` does not need a retrieval pipeline over a codebase
it can already navigate.
→ `05-beyond-the-core.md` §8

### 42. MCP — **optional, T3+**
**Neither repo has it.** Verified: no `modelcontextprotocol` dependency in any of Pi's
`package.json` files; the only string matches are substrings inside OAuth files.
MCP is a way to consume *third-party* tools. With read/write/edit/search/shell covered, there's
no gap it fills. Pi's own extension API is strictly more capable in-process — extensions can
touch rendering, permissions, and compaction, whereas MCP servers only add tools.
Treat it as an **integration**, not a layer.
→ `05-beyond-the-core.md` §7

### Also not in core (Pi ships all four as extensions)
A **todo/planning tool**, **slash commands**, **git checkpointing**, **project trust**.
→ `05-beyond-the-core.md` §6

---

## The shortest honest version

If you only remember five things:

1. **An agent is a loop that stops when the model stops asking for tools.**
2. **Events are the contract** — one fixed vocabulary, and everything above just subscribes.
3. **The loop asks questions instead of knowing answers** — those hooks become your entire
   extension surface later.
4. **Four tools make a coding agent**: read, write, edit, bash.
5. **Context fills up** — so measure it, summarize the old part, keep the recent part, and keep
   your prefix byte-stable so caching works.

Everything else on this list is something you add when a specific failure makes you want it.

---

*Companion: `security.md` — the concerns that arise because this thing executes shell commands
and writes files.*
