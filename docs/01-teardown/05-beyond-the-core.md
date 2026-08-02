# Layer 5 — Beyond the Core

`pi/packages/{protocol,storage,server,client,evals}` + `coding-agent/examples/extensions/`
↔ `tau/src/tau_coding/extensions/`

> The five Pi packages the "four-package" description omits, plus the extension system. This is
> the layer where my pasted notes were most wrong — not because things are missing, but because
> they live somewhere I didn't look.

---

## 1. Why this layer exists as a document

Pi is usually described as four packages: `ai`, `agent`, `coding-agent`, `tui`. It ships **nine**.
The other five are small and easy to overlook:

| Package | Lines | What it is |
|---|---|---|
| `protocol` | 753 | the wire format between processes |
| `client` | 956 | connect to a running agent |
| `server` | 4,166 | host an agent other processes attach to |
| `evals` | 1,277 | measure whether the agent actually works |
| `storage/sqlite-node` | — | durable session storage with SQL migrations |

Between them they answer four questions Layers 1–4 never raise: *how do two processes talk about
an agent, how does a session survive properly, how do you know the agent is any good,* and *how
do users change behaviour without forking?*

Tau has essentially none of this — no protocol, no server, no client, no evals, no SQLite. It has
a four-file extension system and JSONL files. **That gap is the most useful thing in this
document**, because it marks the boundary between "a working coding agent" and "a product."

---

## 2. `protocol` — 753 lines, and it settles a question from my notes

Three files:

```
schemas.ts   412    the message shapes
codec.ts     172    encode / decode
framing.ts   165    where one message ends and the next begins
```

`framing.ts:1-6`:

```typescript
const FRAME_HEADER_LENGTH = 4;
const MAX_UINT32 = 0xffff_ffff;
const PAYLOAD_BLOCK_SIZE = 64 * 1024;

/** Default upper bound for one framed CBOR payload. */
export const DEFAULT_MAX_FRAME_LENGTH = 16 * 1024 * 1024;
```

and the encoder (`framing.ts:29-38`):

```typescript
/** Prefixes a payload with its unsigned 32-bit big-endian byte length. */
export function encodeFrame(payload: Uint8Array): Uint8Array {
	if (!(payload instanceof Uint8Array)) throw new TypeError("Frame payload must be a Uint8Array");
	if (payload.byteLength > MAX_UINT32) throw new RangeError("Frame payload exceeds the unsigned 32-bit length limit");
	const frame = new Uint8Array(FRAME_HEADER_LENGTH + payload.byteLength);
	const length = payload.byteLength;
	frame[0] = length >>> 24;
	frame[1] = length >>> 16;
	frame[2] = length >>> 8;
	…
```

So the transport is **length-prefixed CBOR**: a 4-byte big-endian length header followed by a
binary CBOR payload, capped at 16 MB per frame.

### This directly answers my own notes

My notes had a whole section circling this:

> server side events or eventstreams, easy to scale via load balancers
> http layer // gRPC · json body · response thru event stream
> browser to backend (http) for data use binary or PBs · backend - backend (gRPC)

Pi's answer: **neither HTTP+JSON nor gRPC.** A length-prefixed binary codec over a socket.

And the reasoning is sound once you see the use case. This isn't a public API — it's one local
process streaming agent events to another. gRPC would drag in protobuf codegen and HTTP/2 for
no benefit. HTTP+SSE would mean text framing and base64 for binary content (Layer 1's
`ImageContent`). CBOR is JSON's data model in a binary encoding: same shapes, no escaping,
native byte strings, and a length prefix makes framing trivial.

**The general lesson:** "how do processes talk" has more than the two answers a web background
suggests. For local IPC with structured streaming events, length-prefixed CBOR is smaller and
simpler than both. The 16 MB cap and the explicit `MAX_UINT32` guard are the kind of bounds you
want in a decoder that reads attacker- or bug-controlled lengths.

---

## 3. `server` + `client` — the agent as a daemon

```
server/src/server.ts     399     the host
server/src/sessions.ts   351     LiveSessionManager
server/src/protocol.ts   272     handshake + version negotiation
server/src/snapshots.ts   64     ServerSnapshotPublisher
client/src/client.ts     298
client/src/connection.ts 240
client/src/unix.ts       156     Unix domain socket transport
client/src/state.ts      150
```

`server.ts:1` imports `createHash, randomUUID, timingSafeEqual` from `node:crypto`, and
`server.ts:41` holds `private readonly listeners: readonly PiServerListener[]`.

Three things follow:

**It's authenticated.** `timingSafeEqual` means token comparison in constant time — the standard
defence against timing attacks on secret comparison. Even for a local socket, someone thought
about it.

**Transport is pluggable.** `listeners` is an injected array of `PiServerListener`, and
`client/unix.ts` provides a Unix-domain-socket implementation. So the same server can be exposed
over a socket file, a TCP port, or anything else, without the core knowing.

**There's a snapshot mechanism.** `ServerSnapshotPublisher` plus `client/state.ts` — a late-joining
client needs the *current state*, not a replay of every event since startup. This is the standard
"snapshot + subsequent deltas" pattern, and it's what makes attaching a second UI mid-session
possible.

### Why an agent needs a server at all

This is the architectural payoff of Layer 4's "the UI is only an event consumer." If the agent is
a process emitting `AgentEvent`s, and the UI merely subscribes, then the UI does not have to be
in the same process. That unlocks:

- a TUI and an editor plugin attached to one agent simultaneously
- an agent running on a remote machine (note `examples/extensions/ssh.ts`)
- a UI crash without losing the session
- the agent surviving terminal disconnection

Tau has none of this. Its agent and UI are one process, which is entirely reasonable for a
teaching codebase — and the absence tells you the feature is *optional*, arriving only when
you want multiple front-ends.

---

## 4. `evals` — 414 lines in `src/`, and that's the point

```
pi-harness.ts        257
extensions.eval.ts   140
smoke.eval.ts         17
```

That is a startlingly small eval suite for a project this size, and it reframes the terminal-bench
goal in your plan.

`smoke.eval.ts` at **17 lines** is the shape to copy: a minimal end-to-end check that the agent
starts, takes an instruction, calls a tool, and stops. `pi-harness.ts` (257) is the scaffolding to
drive the agent programmatically — which is the actual prerequisite for any benchmark.
`extensions.eval.ts` (140) tests that extensions load and fire.

**The lesson for your stated end goal:** terminal-bench is not something you bolt on. It needs
exactly what `pi-harness.ts` is — a way to run the agent headless, feed it a task, and inspect
the result. Build that harness in Tier 2 as a test utility (Layer 1's `FakeProvider` plus a
scripted task) and the benchmark becomes a matter of supplying tasks. Skip it and you'll be
retrofitting an interface into a TUI-coupled agent.

Note also that Pi's own eval suite is small enough to suggest the team relies more on unit tests
than on end-to-end evals. Worth knowing before assuming a big eval harness is table stakes.

---

## 5. `storage/sqlite-node` — session storage done properly

Not a package at `packages/storage` — a package *inside* it, which is why my first line-count
attempt reported zero (looking for `packages/storage/src`, which doesn't exist).

```
src/sqlite/migrations.ts
src/sqlite/migrations/001_initial.sql
src/sqlite/session-store.ts
src/sqlite/search-backend.ts
src/sqlite/storage/session-entries.ts
src/sqlite/storage/branch-entries.ts
src/sqlite/storage/session-sequences.ts
src/sqlite/storage/session-materialized.ts
```

Three things worth extracting, all confirming Layer 3b's tree model:

- **Real SQL migrations** (`001_initial.sql`), numbered. Sessions outlive schema versions.
- **`branch-entries` separate from `session-entries`** — the branching tree from Layer 3b §4.1 is
  first-class in the schema, not an application-level convention.
- **`session-materialized.ts`** — a materialized view of the resolved root-to-leaf path, because
  walking the tree on every read is wasteful. This is the performance answer to Layer 3b's
  `path_to_entry`.
- **`search-backend.ts`** — search *across* sessions. Tau cannot do this; JSONL files aren't
  queryable.

The layering is clean: `agent/harness/session/` defines the store interface, `jsonl-store.ts` and
`memory-store.ts` are simple backends in-package, and SQLite is a *separate installable package*.
Same dependency-inversion discipline as Layer 1's provider contract — the core owns the
interface, backends conform.

---

## 6. The extension system — the actual product

`pi/packages/coding-agent/examples/extensions/` holds roughly **75 examples**. Sorted, they read
as a specification of everything a coding agent might want:

| Category | Extensions |
|---|---|
| **Permissions & safety** | `permission-gate`, `confirm-destructive`, `protected-paths`, `project-trust`, `dirty-repo-guard`, `timed-confirm`, `sandbox/` |
| **Orchestration** | `subagent/`, `plan-mode/`, `handoff` |
| **Context** | `custom-compaction`, `trigger-compact`, `summarize` |
| **Tools** | `todo`, `dynamic-tools`, `tool-override`, `truncated-tool`, `inline-bash`, `structured-output`, `kimi-deferred-tools` |
| **Git** | `git-checkpoint`, `auto-commit-on-exit`, `git-merge-and-resolve` |
| **Rules & config** | `claude-rules`, `commands`, `preset`, `dynamic-resources/`, `reload-runtime`, `session-name`, `bookmark` |
| **Providers** | `custom-provider-anthropic/`, `custom-provider-gitlab-duo/`, `provider-payload`, `model-status` |
| **Input** | `input-transform`, `input-transform-streaming`, `prompt-customizer`, `github-issue-autocomplete`, `file-trigger`, `send-user-message` |
| **Rendering** | `message-renderer`, `entry-renderer`, `built-in-tool-renderer`, `custom-header`, `custom-footer`, `status-line`, `working-indicator`, `minimal-mode`, `hidden-thinking-label`, `widget-placement`, `modal-editor` |
| **Infrastructure** | `event-bus`, `rpc-demo`, `ssh`, `interactive-shell`, `notify`, `with-deps/` |
| **Interaction** | `qna`, `question`, `questionnaire` |
| **Demos & games** | `doom-overlay/`, `snake`, `space-invaders`, `tic-tac-toe`, `pirate`, `hello`, `gondolin` |

### Correcting my notes, definitively

My notes asserted:

> Pi - most minimal coding harness
> - it doesnt have memory
> - it doesnt have multi agent orchestration
> - no subagents
> - no plan mode

The accurate version:

| Claim | Reality |
|---|---|
| no memory | **Wrong.** `harness/session/memory-store.ts`, 880-line compaction, tree-structured sessions, SQLite persistence (Layer 3b) |
| no subagents | **Half right.** Not in core; ships as `examples/extensions/subagent/` |
| no plan mode | **Half right.** Not in core; ships as `examples/extensions/plan-mode/`. Confirmed by `cli/args.ts:300` — *"Extensions can register additional flags (e.g., `--plan` from plan-mode extension)"* |
| no multi-agent orchestration | **Half right,** same pattern — `subagent/` + `handoff.ts` |

The correction isn't "the notes were wrong about features." It's that **the notes were looking in
the wrong place.** Pi's minimalism is a statement about its *core*, not its capability. The core
stays small precisely *because* an extension API absorbs everything else.

And the same reframing applies to things my notes never mentioned: permissions, sandboxing,
CLAUDE.md-style rules (`claude-rules.ts`), slash commands (`commands.ts`), and a todo tool
(`todo.ts`) are all extensions. Layer 3a noted "no todo tool in either repo" — accurate for the
toolset, incomplete without this: there *is* one, as an extension.

### Why this shape works

Trace it back through the layers and every extension category lands on a seam that already
existed:

| Extension | Plugs into |
|---|---|
| `permission-gate`, `confirm-destructive`, `protected-paths` | Layer 2's `beforeToolCall` |
| `truncated-tool`, `structured-output` | Layer 2's `afterToolCall` |
| `custom-compaction`, `trigger-compact`, `summarize` | Layer 2's `transformContext` |
| `dynamic-tools`, `tool-override` | Layer 2's tool list + `addedToolNames` |
| `message-renderer`, `custom-footer`, `status-line` | Layer 4's event subscription |
| `custom-provider-*` | Layer 1's `ModelProvider` contract |
| `subagent`, `plan-mode` | composition of all of the above |

**Nothing needed a new seam.** The hooks designed in Layers 1–2 for their own sake turn out to be
the complete extension surface. That is the strongest possible evidence for the plan's advice to
build hooks in Tier 2 before you need them.

### Tau's version is four files

```
tau_coding/extensions/{__init__,api,loader,runtime}.py
```

`api.py` (the surface), `loader.py` (discovery), `runtime.py` (execution — and the file where
`execution_mode` is passed through at line 660, per Layer 2). A minimal, legible extension system
without 75 examples. Good enough to prove the pattern; that's the teaching-codebase tradeoff again.

---

## 7. MCP — neither repo has it

**Verified:** grepping both repos for `modelcontextprotocol` / `mcp` returns only false positives
(substrings inside `ai/src/auth/oauth/anthropic.ts` and `tau_coding/oauth_anthropic.py`), and **no
`package.json` in Pi declares an MCP dependency.**

So: two well-regarded minimal coding agents, ~145,000 lines between them, and **zero MCP
support.**

That's a genuinely useful negative result for your plan, which listed MCP as something "there has
to be." There doesn't. MCP is a way to acquire *third-party* tools; a coding agent whose seven
tools cover reading, writing, editing, searching, and shell execution has no gap MCP fills. Pi's
answer to extensibility is its own extension API, which is strictly more capable in-process
(extensions can touch rendering, permissions, and compaction — MCP servers only add tools).

For your build: **MCP is a Tier-3+ optional**, worth adding when you want to consume someone
else's tool ecosystem, not before. Frame it as an *integration*, not a *layer*.

---

## 8. What each repo does NOT have

The plan asked this explicitly. Consolidated across all five layers:

### Neither repo has

- **MCP support** (§7)
- **A todo/planning tool in core** (Pi: extension; Tau: absent)
- **Retrieval / RAG / embeddings.** No vector store, no semantic search anywhere. Code search is
  `grep`. Worth stating plainly: a good coding agent needs no embedding model.
- **Multi-agent orchestration in core** (Pi: `subagent` extension; Tau: passing references in
  `session.py` and `tui/app.py`)

### Tau lacks, relative to Pi

| Missing | Consequence |
|---|---|
| `protocol` / `server` / `client` | single-process only; no attaching a second UI, no remote agent |
| `storage/sqlite-node` | JSONL only; **no cross-session search** |
| `evals` | no headless harness; terminal-bench would need one built first |
| Provider quirk matrix (Layer 1) | fewer providers work out of the box |
| Search tools `grep`/`find`/`ls` (Layer 3a) | model burns turns on unbounded `bash` search |
| Parallel tool execution (Layer 2) | strictly sequential, despite declaring otherwise |
| `terminate` honoured (Layer 2) | field exists, never read |
| Image generation, model catalog (Layer 1) | out of scope |
| ~75 extension examples | pattern proven, ecosystem absent |

### Pi lacks, relative to Tau

Genuinely short, and interesting:

- **Documented design reasoning.** Tau's `dev-notes/` (25 files including the
  `prompt-caching.md` from Layer 3b) has no Pi equivalent. For *learning*, this is Tau's single
  biggest advantage.
- **A second message cache breakpoint** — Tau's documented improvement over Pi
  (`prompt-caching.md:151-154`).
- **A user-visible interrupted-tool-result message** — Tau's harness synthesizes one; Pi repairs
  silently at the wire (Layer 2 §4.4).

---

## 9. If I rebuilt this layer from scratch

**Tier 2.**

1. **A headless harness** — run the agent programmatically, feed a prompt, assert on events.
   `pi-harness.ts` is 257 lines. This is your test infrastructure *and* the future
   terminal-bench interface, so it pays twice.
2. **A 17-line smoke eval**, per `smoke.eval.ts`: start, instruct, tool-call, stop.

**Tier 3.**

3. **An extension API**, once you have hooks worth exposing. Do not design it first — Pi's
   extension surface is exactly the union of seams that already existed (§6). Enumerate your
   hooks, then expose them.
4. **Reimplement your own features as extensions** to test the API. Pi's ~75 examples are largely
   this exercise; `permission-gate` and `todo` are the obvious first two.
5. **SQLite sessions** only when you want cross-session search. JSONL is fine until then — but
   put `parent_id` on entries from Tier 2 (Layer 3b) so migration is a data move, not a redesign.

**Tier 3+ / defer.**

6. **`protocol` / `server` / `client`** — only when you want a second front-end. If Layer 4's
   adapter stayed thin, this is additive rather than a rewrite. Length-prefixed CBOR over a Unix
   socket, with `timingSafeEqual` token auth and a snapshot-then-deltas handshake.
7. **MCP** — an integration, when you want someone else's tools.
8. **Terminal-bench** — after the harness from item 1 exists.

**Explicitly do not build:** retrieval/RAG. Neither reference has it, and `grep` plus a
model that can read files covers the ground.

---

## 10. Files examined for this layer

| File | Lines | Depth |
|---|---|---|
| `pi/packages/protocol/src/framing.ts` | 165 | read (framing, §2) |
| `pi/packages/server/src/server.ts` | 399 | targeted (auth, listeners) |
| package inventories: `protocol`, `client`, `server`, `evals` | 3,400 | measured |
| `pi/packages/storage/sqlite-node/**` | — | file listing |
| `coding-agent/examples/extensions/` | ~75 items | full listing, categorized |
| `tau/src/tau_coding/extensions/` | 4 files | listing |

Verification greps run: MCP presence across both repos and all `package.json` files; subagent and
plan-mode locations; `cli/args.ts` plan-flag ownership; server transport (Unix socket vs network).

Surveyed, not read: `protocol/schemas.ts` (412), `codec.ts` (172), `server/sessions.ts` (351),
`evals/pi-harness.ts` (257), and every extension implementation.

---

## Layer 5 in one paragraph

Pi's five extra packages are the difference between an agent and a product: a binary wire protocol
so processes can share one agent, a client/server split so the UI is detachable, SQL-backed
sessions so history is searchable, and a harness so quality is measurable. The extension system is
where the notes' "missing" features actually live — subagents, plan mode, permissions, sandboxing,
todo, CLAUDE.md rules — and crucially, every one of them plugs into a seam that Layers 1 and 2
already had. Neither repo has MCP or retrieval, which is the most useful thing this layer says
about scope.

---

*End of Stage 1 teardown. Next: Stage 2 — `docs/00-concepts/anatomy.md` and `security.md`, built
on these five files.*
