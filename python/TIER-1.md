# omega — Tier 1

What this tier contains, and what it deliberately does not.

**Tier 3 is not discussed here.** Only what exists now, and what Tier 2 adds next.

---

## Part 1 — What Tier 1 has

**~1,050 lines of source, 45 tests, all offline.**

### Layer 1 · Provider

| Capability | File | Why it exists |
|---|---|---|
| Neutral message model | `types.py` | So nothing above ever sees a vendor-shaped object |
| Content blocks: text, thinking, tool call | `types.py` | One reply can interleave all three |
| **The 12 stream events** | `events.py` | The contract every layer above subscribes to |
| `partial` on every streaming event | `events.py` | Consumers render without tracking state themselves |
| **The provider interface** | `provider.py` | One method. The *core* owns it; adapters conform |
| `CancellationToken` protocol | `types.py` | Threaded everywhere, set by nothing yet — the seam for Tier 2 |
| Fake provider + script builders | `providers/fake.py` | Proves the interface is small; makes everything testable offline |
| Anthropic adapter with real streaming | `providers/anthropic.py` | The only file that knows a vendor exists |
| Exactly one `start`, one terminal event | `providers/anthropic.py` | The loop never needs a timeout or a sentinel |
| Stop reasons normalised to 3 values | `providers/anthropic.py` | `tool_use`→`toolUse`, `max_tokens`→`length`, else `stop` |
| **Errors as events, never raised** | `providers/anthropic.py` | A stream that failed after 500 tokens keeps the 500 tokens |
| Readable auth / credit-balance errors | `providers/anthropic.py` | Because a stack trace for "no credits" is useless |
| Tool results merged into one user message | `providers/anthropic.py` | Anthropic rejects split results from a parallel turn |
| Empty assistant turns dropped from payload | `providers/anthropic.py` | Providers reject content-less assistant messages |
| Token usage captured | `types.py`, adapter | Input/output counts, reported when the provider gives them |

### Layer 2 · Agent core

| Capability | File | Why it exists |
|---|---|---|
| **The agent loop** | `loop.py` | Ask → run tools → report back → repeat |
| **Stop on content, not `stop_reason`** | `loop.py` | Content is ground truth; stop reason is normalised metadata |
| `max_turns` cap | `loop.py` | A confused model would otherwise burn the budget |
| Terminal error ends the run | `loop.py` | Without raising |
| Every tool outcome becomes a tool result | `loop.py` | Crash, unknown tool, cancellation — the model reads all of them |
| `CancelledError` re-raised before the broad catch | `loop.py` | Python-specific; swallowing it makes Ctrl-C unreliable |
| Tools are an isolation boundary | `loop.py` | One bad tool must not end every session |
| Caller-owned transcript | `loop.py` | `messages` is mutated in place, as in Tau |

### Layer 3-ish · Tools (minimal)

| Capability | File | Why it exists |
|---|---|---|
| `Tool` / `ToolResult` | `tools.py` | Schema + handler, with `content` (model) split from `details` (UI) |
| **Tools raise on failure** | `tools.py` | One way to fail, so a tool author can't get it subtly wrong |
| `read_file`, `write_file`, `run_shell` | `builtin_tools.py` | The minimum that makes it a *coding* agent |
| **Output truncation** — 2,000 lines / 50 KB | `truncate.py` | The failure that ends a session fastest |
| Tail-biased truncation | `truncate.py` | Errors and stack traces are at the end |
| Full output spilled to a temp file | `truncate.py` | Truncation isn't data loss if you give the model the path |
| Budget stated in the tool description | `builtin_tools.py` | Built from the same constants, so it can't drift |
| Failing commands carry their output | `builtin_tools.py` | "exited with code 1" tells the model nothing |

### Interface

| Capability | File |
|---|---|
| Print-based REPL with streaming text | `cli.py` |
| `--fake` — full agent, no key, no network, no cost | `cli.py` |
| Clear message when `ANTHROPIC_API_KEY` is missing | `cli.py` |

### Verified

- **45 tests, 0.5s, fully offline.** No network, no API key, no cost.
- `mypy --strict` clean across 12 source files.
- `ruff` clean.
- `omega --fake` runs a complete turn: streams text → calls a tool → executes it → prints the
  result → answers.
- **Only `providers/anthropic.py` imports the vendor SDK.**

---

## Part 2 — What Tier 1 lacks, and where Tier 2 puts it

Every gap below already has a seam waiting. **That is the point of the tier** — if any of these
required changing the loop, the provider interface, or the event vocabulary, the layering would
have been wrong.

### Things that will hurt first

| Missing | What it costs today | The seam it plugs into |
|---|---|---|
| **`edit` tool** | Whole-file writes only. Fine for 50 lines, hopeless for 2,000 | A fourth `Tool` in `builtin_tools.py`. Nothing else changes |
| **Cancellation** | Ctrl-C kills the process mid-turn | `CancellationToken` is already a parameter on `stream_response`, `run_agent_loop`, and every `ToolExecutor`. Tier 2 supplies an implementation and wires it to the signal handler |
| **Orphaned tool-result repair** | An interrupt leaves a tool call unanswered; the next request is a **permanent** API error | Needs a harness that owns `messages`. `cli.py` already warns about this rather than pretending |
| **Session persistence** | Close the terminal, lose everything | `types.py` models are Pydantic, so JSONL serialisation is `model_dump_json`. Tier 2 adds `session/` and a `parent_id` on entries |
| **Approval gates** | It runs `rm -rf` without asking. **Scratch directories only** | A `before_tool_call` hook parameter on `run_agent_loop`, consulted in `_execute_tool_call` where the unknown-tool check already sits |
| **Path confinement** | It can read `~/.ssh` and write anywhere | One `_validate_path` helper every filesystem tool calls |

### Things that will annoy next

| Missing | What it costs today | The seam |
|---|---|---|
| **Second provider** | Locked to Anthropic — and the abstraction is *unproven* until a second one exists | `providers/openai.py` implementing the same Protocol. If anything outside `providers/` has to change, the interface was wrong |
| **Retry / backoff** | One 429 throws away the whole run | Inside the adapter, below the event boundary, so the loop never learns a retry happened |
| **Cost accounting** | `Usage` is captured but nothing sums or prices it | A counter over the `done` events the loop already yields |
| **The 10 agent events** | Tool *results* can't be streamed — `cli.py` reports them after the fact by watching `messages` grow | A new `agent_events.py` beside `events.py`. The loop emits those instead of passing the 12 straight through |
| **Harness** | No object owns the conversation; `cli.py` holds a bare list | `harness.py` wrapping the loop, holding messages, listeners, and the cancellation token |
| **Steering / follow-up queues** | Typing while it works does nothing | Two callbacks the loop drains between turns |
| **Project instruction file** | Project conventions retyped every session | Read a file, prepend to `SYSTEM_PROMPT` |
| **Two views of history** | A failed turn could poison later requests | The adapter already drops empty assistant turns; Tier 2 generalises this into a `transform_context` hook — which is also where compaction lands later |
| **Headless harness + smoke eval** | Can't run the agent programmatically | A function that takes a prompt and returns the final messages |

### Known rough edges, stated plainly

- **`--fake` replays a fixed script** regardless of what you type. It demonstrates the machinery,
  not intelligence.
- **Ctrl-C during a turn** may leave the transcript invalid. `cli.py` says so instead of hiding it.
- **`run_shell` has no timeout.** A hung command hangs until you kill the process.
- **Tool calls execute sequentially.** Fine — Tau's do too, despite advertising otherwise.
- **Nothing is sandboxed.** There is no `prepare` seam on the shell tool yet either; that arrives
  with approvals.

---

## The one-line summary

Tier 1 is a **working agent with real layers**: it streams, it calls tools, it stops correctly,
and its provider is swappable. It is not safe, not persistent, and not interruptible — and each
of those is a Tier-2 addition to a seam that already exists, not a rewrite.
