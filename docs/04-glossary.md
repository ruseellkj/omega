# Glossary

Alphabetical, for looking things up. Each entry: **plain definition** · *analogy* · why it matters
here · where it lives in Pi/Tau.

Assumes you have never built an agent.

---

### Agent
A program that receives a goal, decides what actions to take, uses tools, observes the results,
and continues until done.
*Like a contractor, not a vending machine — you state an outcome, not a sequence of button
presses.*
The distinction from a chatbot is the loop and the tools.

### Agent loop
The cycle: ask the model → run the tools it requested → send the results back → repeat.
*A conversation with a colleague who can only ask you to do things, never do them.*
**This is the entire idea.** Everything else supports it.
Pi `agent-loop.ts` (792) · Tau `loop.py` (**318**)

### Backpressure
Deliberately slowing a fast producer so a slow consumer can keep up.
*A funnel — pour faster and it overflows, so you pour at the rate it drains.*
A command printing 10,000 lines/sec would trigger 10,000 screen redraws. Both repos throttle to
~100 ms.
`bash.ts:9` (`BASH_UPDATE_THROTTLE_MS`)

### Blast radius
How much damage one wrong action can cause.
*The difference between spilling coffee on your desk and on the server rack.*
The organizing idea of agent security: you can't prevent every mistake, so shrink what a mistake
can reach.
`00-concepts/security.md` §3

### Breakpoint (cache breakpoint)
A marker saying "everything before this point is reusable — charge me less for it."
*A bookmark in a textbook: the librarian doesn't re-shelve the first 200 pages each time.*
Anthropic allows **4** per request, evaluated `tools` → `system` → `messages`.
`tau/dev-notes/prompt-caching.md`

### CBOR
A binary format with the same data model as JSON, but smaller and with native byte strings.
*Shorthand versus longhand — same meaning, fewer strokes.*
Pi's inter-process protocol. Chosen over HTTP+JSON and gRPC for local streaming.
`protocol/codec.ts`, `framing.ts`

### Compaction
Replacing old conversation history with a summary to free context space.
*Meeting minutes: you keep the decisions and discard the recording.*
Fires at `context_window − 16,384` tokens. Keeps ~20k recent tokens verbatim.
Pi `compaction/` (1,287) · Tau `context_window.py`

### Content block
One piece of a message: text, thinking, a tool call, or an image.
*Paragraphs in a letter — a message is a list of them, not one string.*
A single reply can interleave visible text, hidden reasoning, and tool requests.
`types.ts:338-366`

### Context window
The maximum amount of text a model can consider at once.
*A desk. Everything you're working with must fit on it; anything else is in a filing cabinet the
model can't open.*
Everything is re-sent every request, so this fills up and is the constraint behind compaction and
truncation.

### Differential rendering
Redrawing only the characters that changed, not the whole screen.
*Correcting one word with an eraser instead of retyping the page.*
Without it, streaming text flickers badly. Pi hand-rolls it; Tau inherits it from Textual.
`tui.ts:2`

### Embedding
A list of numbers representing text's meaning, used for similarity search.
*A colour swatch for meaning — "close" swatches mean similar ideas.*
**Neither Pi nor Tau uses embeddings at all.** See *RAG*.

### Eval
An automated test measuring how well the agent completes real tasks.
*A driving test, not a written exam.*
Pi's whole eval suite is 414 lines; `smoke.eval.ts` is **17**.
`packages/evals/`

### Event stream
A sequence of small messages describing something in progress.
*A live sports commentary instead of the final score.*
The contract between every layer. Twelve provider events; ten agent events.
`provider_events.py`, `events.py`

### Extension
Third-party code that changes agent behaviour by plugging into existing hooks.
*An appliance plugged into a wall socket — the socket had to exist first.*
Pi ships ~75 examples. Note: extensions run with **full** agent authority, not sandboxed.
`coding-agent/examples/extensions/`

### Follow-up (vs steering)
A queued message injected *after* the agent would have stopped.
*Leaving a note on someone's desk for when they finish.*
Paired with *steering*, which injects between turns instead.
`harness.py:127-132`

### Guardrail
Any check that constrains what the agent may do.
*A stair railing — doesn't move you, stops you falling.*
Approval gates, path confinement, blocklists, sandboxes.

### Harness
The stateful wrapper around the loop: holds the conversation, listeners, queues, and cancellation.
*The car around the engine — the engine spins, the car has a seat, a key, and a brake.*
The loop is stateless; the harness owns everything durable.
Pi `agent-harness.ts` (1,185) · Tau `harness.py` (244)

### Hook
A callback the loop calls to ask a question it refuses to answer itself. See *seam*.
*A bouncer checking a list someone else wrote.*
`before_tool_call` returning `(blocked, reason)` **is** the whole permission system.
`loop.py:37-41`

### JSONL
A text file with one JSON object per line, appended to.
*A logbook — you add a line, you never rewrite the page.*
The session format. A crash truncates one line, not the file.
`session/jsonl.py`

### KV cache
See *prompt cache*. "KV" refers to the model's internal key/value attention state, which is what
gets reused.

### MCP (Model Context Protocol)
A standard letting an agent use tools provided by separate third-party servers.
*A universal power adapter for tools.*
**Neither Pi nor Tau supports it.** Verified: no `modelcontextprotocol` dependency anywhere.
Optional, Tier 3+.

### Orphaned tool call
A tool request with no matching result — usually from an interrupt.
*A restaurant order with no dish and no refund. The kitchen refuses further orders.*
**Providers reject the conversation outright**, permanently. Must be repaired by synthesizing a
result.
`harness.py:224-244`

### Partial
A snapshot of the whole message-so-far, attached to every streaming event.
*A photo of the page after each word, not just the word.*
Lets any consumer render without tracking state itself. Costs a deep copy per token.
`stream.py:40-41`

### Path confinement
Verifying a resolved file path is inside the working directory.
*A hotel key that opens your room and not the vault.*
Resolve **first**, then check — `../..` and symlinks look innocent before resolution. Does not
cover `bash`.
`tools.py:140`

### Permission mode
The policy governing which tool calls need confirmation.
*Ranging from "sign every cheque" to "here's the company card."*
Implemented entirely inside the `before_tool_call` hook.

### Plan mode
A read-only mode where the agent proposes an approach before touching files.
*Reviewing blueprints before pouring concrete.*
**An extension in Pi, not core** — `cli/args.ts:300` confirms the flag is registered by the
extension.

### Prompt cache
A provider discount for re-sending an identical prefix.
*A tab at a bar instead of settling up per drink.*
The single biggest cost lever, and it forces byte-stability on everything early in the request.
`dev-notes/prompt-caching.md`

### Prompt injection
Text the agent *reads* being treated as instructions it should follow.
*A note slipped into your inbox on company letterhead saying "wire the money."*
Unsolved in general. Containable via approval gates and small blast radius.
Pi has `sanitize-unicode.ts`; nothing more found in either repo.

### Provider adapter
Code translating one vendor's API into the neutral interface.
*A plug adapter — the appliance never learns which country it's in.*
Key insight: adapters key on **wire format**, not vendor. Pi has 38 providers over 10 formats.
Pi `ai/src/api/` · Tau `tau_ai/anthropic.py` etc.

### RAG (retrieval-augmented generation)
Searching a knowledge base and inserting the results into the prompt.
*Handing someone the three relevant pages instead of the whole library.*
**Neither Pi nor Tau does this.** Code search is `grep`. A model that can read files doesn't need
it. Deliberately skip.

### Sandbox
A restricted environment limiting what a command can touch.
*A padded room.*
Pi's portable `bash` tool has **none** — it runs whatever it's given. Confinement is injected via
the `prepare` hook.
`bash.ts:36-39`

### Seam
A place you can change behaviour without editing the code there.
*A zip in a jacket — the jacket was built so a liner can be added later.*
**A hook is what you get when you refuse to let a layer know something.** Nine hooks support ~75
extensions.
`03-architecture/03-production.md` §2

### Session
One conversation, persisted. Structurally a **tree** of entries, each with a `parent_id`; the live
conversation is one root-to-leaf path.
*A choose-your-own-adventure book — you're on one path, the others still exist.*
Editing an earlier message forks rather than destroys.
`session/tree.py`

### Skill
A reusable bundle of instructions loaded on demand.
*A recipe card pulled from a box when you need that dish.*
Pi `harness/skills.ts` (375) · Tau `skills.py` (239)

### SSE (Server-Sent Events)
An HTTP mechanism for a server to push a text stream to a client.
*A ticker tape — one-way, arrives as it happens.*
How most model APIs stream. Pi uses CBOR frames for its *own* protocol instead.

### Steering
A queued message injected *between turns* of a run in progress.
*Calling out "also check the brakes" while the mechanic works.*
Paired with *follow-up*.
`harness.py:120-125`

### Stop reason
Why the model stopped: `stop`, `length`, or `toolUse`.
*"Finished," "ran out of paper," or "need something from you."*
Normalized from six vendor spellings. Note the loop branches on **content**, not this.
`stream.py:80-85`

### Streaming
Sending a response in pieces as it's generated.
*Watching someone write, versus receiving the finished letter.*
Required for a usable UI, and the reason the event vocabulary exists.

### Structural typing
Something satisfies an interface by having the right shape, not by declaring it.
*If it has a handle and holds water, it's a jug.*
Why Tau's 38-line `FakeProvider` inherits from nothing.
`provider.py:19` (`Protocol`)

### System prompt
Standing instructions sent with every request.
*A job description that gets restated at the start of each shift.*
Must be byte-stable across requests or prompt caching breaks.
Tau `system_prompt.py` (205)

### Subagent
A nested agent spawned to handle a sub-task.
*Delegating to a junior with their own notepad.*
**Needs no special support: a subagent is just a tool whose implementation runs another loop.** An
extension in Pi.

### Terminal-bench
A benchmark measuring coding agents on real terminal tasks.
*A standardized obstacle course.*
Needs a headless harness first — build that in Tier 2 and the benchmark becomes supplying tasks.

### Token
The unit models read and bill in — roughly ¾ of a word.
*Syllables, not letters.*
Both repos estimate at `chars / 4` with **no tokenizer**, because "should we compact?" doesn't need
precision.
`context_window.py:17`

### Tool (function calling)
A capability you expose to the model, which it can request by name.
*A menu. The model orders; the kitchen cooks.*
Four are enough: read, write, edit, bash. A tool also carries prompt text and renderers.
`tools.py:76-105`

### Tool call
The model's request to run a tool, with arguments and an id.
*A written order slip.*
Every one **must** get a matching result. See *orphaned tool call*.

### Tool schema
A JSON Schema describing a tool's arguments.
*A form with labelled fields.*
Tool schemas are a major context cost — tens of thousands of tokens, re-sent every request.

### Transcript
The full stored record of a conversation.
*The court reporter's record, versus what the jury is currently shown.*
**Three different views exist**: durable log, resolved path, provider payload. Conflating them is
the bug.

### Truncation
Cutting output to a size budget.
*An executive summary with "full report attached."*
Keep the **tail** (errors are at the end), state the limit and line range, and give the model the
temp-file path. 2,000 lines / 50 KB in both repos.
`bash.ts:130-142`

### TUI (terminal user interface)
A full interactive interface drawn with text.
*A dashboard made of characters.*
Larger than the agent core in both repos. Pi builds its own; Tau uses Textual.

### Wire format
The exact byte-level shape of a request or response.
*The envelope and postal form, not the letter.*
Adapters should key on this, not on vendor name — otherwise you write the same adapter thirty
times.

---

## The eight terms that matter most

If the list is too long: **agent loop**, **event stream**, **tool call**, **context window**,
**compaction**, **prompt cache**, **hook/seam**, **orphaned tool call**.

The first six describe how it works. The seventh is why it stays maintainable. The eighth is the
bug that will otherwise waste your afternoon.
