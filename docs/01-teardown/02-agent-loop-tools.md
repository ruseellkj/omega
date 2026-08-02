# Layer 2 — Agent Loop & Tools

`pi/packages/agent` ↔ `tau/src/tau_agent`

> This is the layer that makes it an *agent* rather than a chatbot. It is also the smallest
> layer relative to its importance: Tau's entire portable core is **1,752 lines**, and the loop
> itself is **318**. Read `tau_agent/loop.py` end to end at least once — it is the single most
> valuable file in either repository.

---

## 1. The problem this layer solves

Layer 1 gave us one neutral way to ask a model for a response. But a single request/response is
a chatbot. An agent needs to *keep going*: ask the model, notice it wants to run a tool, run
the tool, hand the result back, ask again — until the model stops asking for tools.

That's it. That's the loop. Your notes put it well:

> An agent is an LLM in a loop that can observe, decide, act through tools, inspect results,
> and stop when done.

So this layer answers four questions, and deliberately nothing else:

1. **When do we call the model again?** (the termination condition)
2. **How do tools get described, dispatched, and reported back?**
3. **What does the outside world get to watch?** (the event vocabulary)
4. **Where can policy be injected without the loop knowing what the policy is?** (hooks)

Question 4 is the one that separates a toy from a foundation. The loop must not know what a
permission prompt is, what compaction is, or what a terminal looks like. It exposes **seams**,
and someone else fills them. Everything Pi's coding agent adds — approval gates, context
compaction, model switching — plugs into a seam defined here.

---

## 2. The core contract

### 2.1 The loop signature — the seams are the design

`tau/src/tau_agent/loop.py:44-58`

```python
async def run_agent_loop(
    *,
    provider: ModelProvider,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    prompts: Sequence[AgentMessage] = (),
    max_turns: int | None = None,
    signal: CancellationToken | None = None,
    get_steering_messages: Callable[[], Sequence[AgentMessage]] | None = None,
    get_follow_up_messages: Callable[[], Sequence[AgentMessage]] | None = None,
    before_tool_call: BeforeToolCall | None = None,
    after_tool_call: AfterToolCall | None = None,
) -> AsyncIterator[AgentEvent]:
```

with the hook types at `loop.py:37-41`:

```python
BeforeToolCall = Callable[[ToolCall], Awaitable[tuple[bool, str | None]]]
AfterToolCall = Callable[
    [ToolCall, AgentToolResult, bool],
    Awaitable[tuple[AgentToolResult, bool]],
]
```

Read `BeforeToolCall` carefully: given a tool call, return `(blocked, reason)`. **That is the
entire permission system, as far as the loop is concerned.** The loop has no notion of
approval, allowlists, or path confinement. It asks a function "should this run?" and honours
the answer. Your Tier-2 approval gate is an implementation of this one callable.

`AfterToolCall` is the mirror: given a call, its result, and whether it errored, return a
possibly-modified result and error flag. That's where output truncation, secret redaction, and
result rewriting belong.

**A note on `messages` being mutated.** It's a `list` and the loop appends to it (`loop.py:62`,
`93`, `132`, `155`). The caller's list *is* the transcript, updated in place. That's a
deliberate but sharp-edged choice — it means the harness and loop share one mutable history.
Worth knowing before you copy it; an immutable "return the new messages" design is also
defensible, and Tau does additionally return them via `AgentEndEvent(messages=new_messages)`.

### 2.2 Ten agent events, wrapping Layer 1's twelve

`tau/src/tau_agent/events.py:75-87`

```python
type AgentEvent = Annotated[
    AgentStartEvent | AgentEndEvent
    | TurnStartEvent | TurnEndEvent
    | MessageStartEvent | MessageUpdateEvent | MessageEndEvent
    | ToolExecutionStartEvent | ToolExecutionUpdateEvent | ToolExecutionEndEvent,
    Field(discriminator="type"),
]
```

Pi's, at `pi/packages/agent/src/types.ts:422-437`, is the same ten:

```typescript
export type AgentEvent =
	| { type: "agent_start" }
	| { type: "agent_end"; messages: AgentMessage[] }
	| { type: "turn_start" }
	| { type: "turn_end"; message: AgentMessage; toolResults: ToolResultMessage[] }
	| { type: "message_start"; message: AgentMessage }
	| { type: "message_update"; message: AgentMessage; assistantMessageEvent: AssistantMessageEvent }
	| { type: "message_end"; message: AgentMessage }
	| { type: "tool_execution_start"; toolCallId: string; toolName: string; args: any }
	| { type: "tool_execution_update"; toolCallId: string; toolName: string; args: any; partialResult: any }
	| { type: "tool_execution_end"; toolCallId: string; toolName: string; result: any; isError: boolean };
```

**Second independent confirmation of Layer 1's finding.** Two languages, same ten events, same
`type` strings, same nesting. The vocabulary is the architecture.

The events form four nested scopes, and the nesting is the mental model:

```
agent_start                                  one run
  turn_start                                 one model request + its tools
    message_start / message_update* / message_end      the assistant message
    tool_execution_start / _update* / _end             each tool
    message_start / message_end                        each tool result
  turn_end  (message + toolResults)
  … more turns …
agent_end  (all new messages)
```

Note `MessageUpdateEvent` (`events.py:39-44`) carries **both** the accumulated message *and*
the raw Layer-1 event:

```python
class MessageUpdateEvent(WireModel):
    type: Literal["message_update"] = "message_update"
    message: AgentMessage
    assistant_message_event: AssistantMessageEvent
```

That's event *nesting*, not replacement. A coarse consumer redraws `message` and ignores the
rest; a fine-grained renderer reaches into `assistant_message_event` to animate a single token
delta. One vocabulary serves both.

### 2.3 What a "tool" actually is — much more than schema + handler

`tau/src/tau_agent/tools.py:76-105`

```python
@dataclass(frozen=True, slots=True)
class AgentTool:
    """A tool exposed to the portable agent loop."""

    name: str
    label: str
    description: str
    parameters: Mapping[str, JSONValue]          # JSON Schema — for the model
    execute_fn: ToolExecutor                     # the handler
    prompt_snippet: str | None = None            # ← contributes to the system prompt
    prompt_guidelines: tuple[str, ...] = ()      # ← ditto
    prepare_arguments: ToolArgumentPreparer | None = None
    execution_mode: ToolExecutionMode = "parallel"
    render_call: ToolCallRenderer | None = None    # ← how to display the invocation
    render_result: ToolResultRenderer | None = None  # ← how to display the result
```

This is the most under-appreciated definition in either repo. A tool bundles **four different
audiences**:

| Field | Audience | Purpose |
|---|---|---|
| `parameters` (JSON Schema) | the model | how to call it |
| `description` | the model | when to call it |
| `prompt_snippet`, `prompt_guidelines` | the model | *usage policy*, injected into the system prompt |
| `execute_fn` | the runtime | what actually happens |
| `render_call`, `render_result` | the human | how it looks in the terminal |
| `prepare_arguments` | the runtime | coerce malformed args before validation |

If you take one design idea from Layer 2, take this: **a tool owns its own prompt text and its
own rendering.** The alternative — one giant system prompt describing all tools, plus a `switch`
in the renderer — is what makes agents hard to extend. Here, adding a tool is adding one
object.

`prepare_arguments` deserves a note: models emit malformed arguments regularly (a JSON string
where an object belongs, a missing optional). This is the sanctioned coercion point *before*
schema validation, rather than loosening the schema.

The result type, `tools.py:21-42`:

```python
class AgentToolResult(WireModel):
    content: list[TextContent | ImageContent] = Field(default_factory=list)
    details: JSONValue = None
    added_tool_names: list[str] | None = None
    terminate: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_text_content(cls, value: object) -> object:
        # a bare string becomes [TextContent(text=...)]
```

Two fields worth naming:

- **`content` vs `details`.** `content` goes to the *model*. `details` is structured data for
  *logs and UI* and never reaches the model. Keeping them separate is what lets a tool return
  a rich diff object to the renderer while sending the model a compact summary — directly
  relevant to context budget.
- **`added_tool_names`** — a tool result can *introduce new tools* from that transcript point
  on. That's dynamic capability loading (Pi's `types.ts:362-363`: "Names of tools introduced by
  this result and available from this transcript point onward").

### 2.4 The harness — where state lives

The loop is a pure async generator: give it inputs, get events. It holds nothing. `AgentHarness`
(`tau/src/tau_agent/harness.py:61-76`) is the stateful object wrapped around it — Tau's
docstring calls it *"Reusable stateful agent brain independent of coding/UI policy."*

```python
class AgentHarness:
    def __init__(self, config: AgentHarnessConfig, *, messages: Sequence[AgentMessage] = ()):
        self._config = config
        self._messages = list(messages)
        self._listeners: list[EventListener] = []
        self._current_signal: SimpleCancellationToken | None = None
        self._running = False
        self._steering_queue: deque[AgentMessage] = deque()
        self._follow_up_queue: deque[AgentMessage] = deque()
```

**Steering vs follow-up** (`harness.py:120-132`) is a genuinely good UX primitive most designs
miss:

```python
def steer(self, content: str) -> QueuedMessages:        # inject BETWEEN turns of this run
    return self.steer_message(UserMessage(content=content))

def follow_up(self, content: str) -> QueuedMessages:    # inject AFTER the run would end
    return self.follow_up_message(UserMessage(content=content))
```

These wire into the loop's two callbacks — steering drained every turn (`loop.py:160`),
follow-ups drained only when the loop is about to exit (`loop.py:162-165`). In plain terms:
*steering* is "while you're working, also consider this"; *follow-up* is "when you're done, do
this next." Both exist because a human types while the agent runs.

`queue_mode` (`harness.py:210-217`) controls whether a drain takes one queued message
(`"one_at_a_time"`, the default) or all of them.

`_ensure_not_running` (`harness.py:198-202`) allows exactly one run at a time, and the error
message teaches the fix:

```python
raise RuntimeError(
    "AgentHarness is already running; use steer() or follow_up() to queue messages."
)
```

### 2.5 The detail worth the whole file: orphaned tool calls

`tau/src/tau_agent/harness.py:224-244`

```python
def _append_interrupted_tool_results(self) -> None:
    returned_ids = {
        message.tool_call_id
        for message in self._messages
        if isinstance(message, ToolResultMessage)
    }
    for message in tuple(self._messages):
        if not isinstance(message, AssistantMessage):
            continue
        for call in message.tool_calls:
            if call.id in returned_ids:
                continue
            returned_ids.add(call.id)
            self._messages.append(
                ToolResultMessage(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    content=[TextContent(text="Tool call interrupted by user")],
                    is_error=True,
                )
            )
```

**The invariant being defended: every `tool_call` must have a matching `tool_result`, or
providers reject the conversation outright.**

Here's the failure it prevents. The model asks to run `bash`. You hit Ctrl-C while it's
running. History now holds an assistant message containing a tool call with no result. Your
next request is a hard API error, and the session is bricked until you delete messages.

The repair scans history for tool-call ids with no matching result and synthesizes one. Tau
calls it at **three** points — `prompt_message` (`harness.py:148`), `continue_`
(`harness.py:157`), and in `_run`'s `finally` when cancelled (`harness.py:186-187`).

This is a *shipped-software* detail. Nobody designs it up front; you learn it from a broken
session. Copy it in Tier 2, the moment you add cancellation.

---

## 3. Execution path, traced

### 3.1 The termination condition — answered precisely

`tau/src/tau_agent/loop.py:85-166`, reduced to its skeleton:

```python
while True:                                    # outer: follow-ups
    has_more_tools = True
    while has_more_tools or pending:           # inner: turns
        ...inject pending messages...
        if max_turns is not None and turn > max_turns: ...return
        ...stream assistant message...
        if assistant.stop_reason in {"error", "aborted"}: ...return
        calls = list(assistant.tool_calls)
        has_more_tools = bool(calls)           # ← THE condition
        for call in calls: ...execute...
        yield TurnEndEvent(message=assistant, tool_results=tool_results)
        turn += 1
        pending = tuple(get_steering_messages() ...)
    follow_ups = tuple(get_follow_up_messages() ...)
    if follow_ups:
        pending = follow_ups
        continue
    break
yield AgentEndEvent(messages=new_messages)
```

> **The loop ends when the model returns an assistant message containing no tool calls, and no
> steering or follow-up messages are queued.**

Three escape hatches short-circuit that: `max_turns` exceeded (`loop.py:99-107`), a terminal
`stop_reason` of `error`/`aborted` (`loop.py:134-137`), and an invalid `max_turns < 1`
(`loop.py:70-78`).

One subtlety worth copying: the condition is `bool(assistant.tool_calls)` — **the actual
content**, not `stop_reason == "toolUse"`. Content is ground truth; a stop reason is
provider-reported metadata that Layer 1 already had to normalize across six spellings. Trust
the content.

Pi's loop is structurally identical — `agent-loop.ts:170` `while (true)`, `agent-loop.ts:174`
`while (hasMoreToolCalls || pendingMessages.length > 0)`, `agent-loop.ts:196` the error/aborted
exit, `agent-loop.ts:207` `if (toolCalls.length > 0)`. Third confirmation of convergence.

### 3.2 Two views of history — the detail almost everyone gets wrong

`tau/src/tau_agent/loop.py:171-186`

```python
def _provider_context(messages: list[AgentMessage]) -> list[AgentMessage]:
    """Return replayable messages while retaining failures in durable history.

    Providers cannot consistently accept an assistant turn with no content. Tau
    persists terminal failures for diagnostics, but an empty failed or aborted
    turn is not model context and must not poison the next request.
    """
    return [
        message
        for message in messages
        if not (
            isinstance(message, AssistantMessage)
            and message.stop_reason in {"error", "aborted"}
            and not message.content
        )
    ]
```

Called at `loop.py:117` on the way into every provider request.

**There are two different histories, and conflating them is a bug:**

| | Durable transcript | Provider context |
|---|---|---|
| Purpose | diagnostics, resume, audit, UI | the next model request |
| Contains | everything, including empty failures | only replayable messages |
| Consumer | you | the model |

An empty failed turn is real history worth keeping — but sending it makes the *next* request
fail too, so one bad turn poisons the session. The fix is a filter at the boundary, not
deletion from history.

Pi generalizes this into a required hook (`agent/src/types.ts:173`):

```typescript
convertToLlm: (messages: AgentMessage[]) => Message[] | Promise<Message[]>;
```

Its doc comment (`types.ts:148-152`) makes the intent explicit: *"AgentMessages that cannot be
converted (e.g., UI-only notifications, status messages) should be filtered out."* Pi's
transcript can hold message types the model never sees — see §4.

### 3.3 Streaming a turn

`loop.py:189-222` translates Layer 1's events into Layer 2's:

| Layer 1 event | becomes |
|---|---|
| `AssistantStartEvent` | `MessageStartEvent(message=event.partial)` |
| `AssistantDoneEvent` | `MessageEndEvent(message=event.message)` |
| `AssistantErrorEvent` | `MessageEndEvent(message=event.error)` |
| anything else | `MessageUpdateEvent(message=partial, assistant_message_event=event)` |

Note that an **error still becomes a normal `MessageEndEvent`** carrying a message with
`stop_reason="error"`. The in-band-errors discipline from Layer 1 propagates upward unchanged:
a consumer has exactly one code path for "the assistant message finished," whatever the
outcome.

Both `AssistantDoneEvent` and `AssistantErrorEvent` branches guard with `if not started:` and
emit a synthetic `MessageStartEvent` — belt-and-braces even though Layer 1 already guarantees
a `start`. Cheap defence at a layer boundary is good practice.

### 3.4 Executing one tool call

`loop.py:225-282`, in order:

1. `ToolExecutionStartEvent` — emitted **before** any policy check, so the UI can show the
   attempt even if it gets blocked.
2. `before_tool_call` hook → `(blocked, reason)`.
3. Resolution, in precedence order: blocked → `"Tool execution was blocked"`; cancelled →
   `"Operation aborted"`; unknown tool → `"Tool {name} not found"`; else execute.
4. Any progress updates become `ToolExecutionUpdateEvent`s.
5. `after_tool_call` hook → possibly-rewritten `(result, is_error)`.
6. `ToolExecutionEndEvent`.
7. A `ToolResultMessage` is appended and emitted as `MessageStartEvent` + `MessageEndEvent`.

**Every failure path produces a normal tool result with `is_error=True`.** Nothing throws. The
model sees `"Tool bash not found"` as an observation and can adapt — which is the entire point
of an agent loop. An exception here would end the run instead.

### 3.5 Tools are an isolation boundary

`loop.py:285-306`

```python
try:
    result = await tool.execute(call.id, call.arguments, signal, on_update)
    return result, False, updates
except asyncio.CancelledError:
    raise
except Exception as exc:  # noqa: BLE001 - tools are an isolation boundary
    return _error_result(str(exc)), True, updates
finally:
    accepting = False
```

Three deliberate decisions in nine lines:

- **The broad `except` is correct here**, and the comment says why. A tool is third-party code
  touching the filesystem and network. If a tool crashing crashed the agent, one bad tool would
  end every session. The exception becomes an error result the model can read.
- **`CancelledError` is re-raised first.** In Python, cancellation arrives *as an exception*; a
  bare `except Exception` that swallowed it would make Ctrl-C unreliable. (`CancelledError`
  inherits from `BaseException`, not `Exception`, on modern Python — so this re-raise is
  belt-and-braces, and the right kind.)
- **`accepting = False` in `finally`** — late `on_update` calls from a misbehaving tool are
  ignored after it settles. Pi documents the same rule (`types.ts:374-375`): *"Calls made after
  the tool promise settles are ignored."*

---

## 4. Pi vs Tau — concept versus accident

### Identical (the architecture)

| Concern | Evidence |
|---|---|
| Ten agent events, same names and nesting | `types.ts:422-437` ≡ `events.py:75-87` |
| Nested `while` loop shape | `agent-loop.ts:170-174` ≡ `loop.py:85-87` |
| Termination = no tool calls + empty queues | both |
| `error`/`aborted` short-circuits the run | `agent-loop.ts:196`, `loop.py:134` |
| `beforeToolCall` can block; blocked → error tool result | `types.ts:57-64`, `loop.py:240-245` |
| `afterToolCall` can rewrite the result | `types.ts:79-90`, `loop.py:264-265` |
| Steering vs follow-up queues, `queue_mode` | `types.ts:44-50`, `harness.py:120-132` |
| Tool failures become tool results, never exceptions | both |
| `onUpdate` ignored after the tool settles | `types.ts:374-375`, `loop.py:291-305` |
| `content` (to model) vs `details` (to UI) split | `types.ts:355-369`, `tools.py:21-27` |
| `addedToolNames` for dynamic tool loading | `types.ts:362`, `tools.py:26` |
| Loop is stateless; a harness owns state | both |

### Idiomatic (accident)

| Pi | Tau |
|---|---|
| `EventStream<AgentEvent, AgentMessage[]>` | `AsyncIterator[AgentEvent]` via `yield` |
| `AbortSignal` | `CancellationToken` Protocol |
| `toolCallId`, `isError` | `tool_call_id`, `is_error` |
| `"one-at-a-time"` | `"one_at_a_time"` |
| TypeBox `TSchema` + `Static<T>` for typed params | `Mapping[str, JSONValue]` raw JSON Schema |
| Hooks return partial objects (`{block: true}`) | Hooks return tuples (`(blocked, reason)`) |
| `Promise.all` | `async for` |

Tau's tuple-returning hooks versus Pi's object-returning hooks is a real ergonomics
difference — Pi's `BeforeToolCallResult { block?, reason? }` extends without breaking callers;
Tau's `tuple[bool, str | None]` doesn't. Minor now, annoying later.

### Genuinely different — and these are the interesting ones

**1. Pi has five extra loop hooks. Tau has none of them.**

`pi/packages/agent/src/types.ts:144-287` — beyond the four Tau shares:

| Hook | Purpose |
|---|---|
| `convertToLlm` *(required)* | `AgentMessage[]` → `Message[]`; drop UI-only messages |
| `transformContext` | *"Context window management (pruning old messages)"* — **the compaction seam** |
| `getApiKey` | re-resolve credentials per call, for *"short-lived OAuth tokens … that may expire during long-running tool execution phases"* |
| `shouldStopAfterTurn` | graceful stop, *"e.g. before context gets too full"* |
| `prepareNextTurn` | replace context / **model** / thinking level between turns |

`transformContext` is the important one, and it answers a question Stage 2 will ask directly:
**how does compaction integrate with the loop?** Answer: *it doesn't.* The loop calls a
transform before each request (`agent-loop.ts:290`), and compaction is one implementation,
wired in at `agent/src/harness/agent-harness.ts:493` and `coding-agent/src/core/sdk.ts:350`.
The loop never learns what compaction is. That is the correct shape, and it is why
`agent-loop.ts` stays comprehensible at 792 lines while supporting an 880-line compaction
subsystem.

`prepareNextTurn` returning `{context?, model?, thinkingLevel?}` means Pi can **switch models
mid-run** — cheap model for mechanical turns, strong model for hard ones.

**2. Parallel tool execution: Pi does it, Tau doesn't — despite declaring it.**

Pi, `agent-loop.ts:420-425`:

```typescript
(tc) => currentContext.tools?.find((t) => t.name === tc.name)?.executionMode === "sequential",
);
if (config.toolExecution === "sequential" || hasSequentialToolCall) {
	return executeToolCallsSequential(...);
}
return executeToolCallsParallel(...);
```

with `await Promise.all(...)` at `agent-loop.ts:540`. The rule is conservative and correct: if
*any* tool in the batch declares itself sequential, the whole batch runs sequentially. A single
`write_file` in a batch of five `read_file`s forces order — which is what you want, since
concurrent writes race.

Tau declares `execution_mode: ToolExecutionMode = "parallel"` (`tools.py:88`) but **the field
is never used to dispatch**. Verified: the only other reference is a pass-through at
`tau_coding/extensions/runtime.py:660`, and there is no `asyncio.gather`/`TaskGroup` anywhere
for tool calls. `loop.py:142` is a plain sequential `for`. So Tau's tools always run one at a
time, and the default value is misleading.

**3. `terminate` — Pi honours it, Tau's field is vestigial.**

Pi, `agent-loop.ts:583`:

```typescript
return finalizedCalls.length > 0 && finalizedCalls.every((finalized) => finalized.result.terminate === true);
```

Documented at `types.ts:366-367`: *"Early termination only happens when every finalized tool
result in the batch sets this to true."* Unanimity required — one tool cannot unilaterally end
the run.

Tau declares `terminate: bool | None` (`tools.py:27`) and **never reads it anywhere in the
codebase**. Verified by grep. It is Pi-parity scaffolding, not a feature.

*Both of these are worth internalising as a general caution: a field existing in a type is not
evidence the behaviour exists. Grep for the read, not the declaration.*

**4. Same invariant, different layer: orphaned tool-result repair.**

Tau repairs orphans in the **harness** (`harness.py:224-244`), mutating the durable message
list. Pi repairs them in the **AI layer**, during message translation —
`ai/src/api/transform-messages.ts:158-219`:

```
// Second pass: insert synthetic empty tool results for orphaned tool calls
// If we have pending orphaned tool calls from a previous assistant, insert synthetic results now
// User message interrupts tool flow - insert synthetic results for orphaned calls
// If the conversation ends with unresolved tool calls, synthesize results now.
```

The tradeoff is real and worth choosing deliberately:

- **Pi (repair at the wire boundary):** non-destructive. The durable transcript keeps the
  orphan as it happened; only the outbound payload is patched. Invisible to the user.
- **Tau (repair in the harness):** persistent. `"Tool call interrupted by user"` becomes a real
  message — user-visible in the transcript and in resumed sessions.

Tau's is more honest to the user; Pi's is cleaner as history. I'd take Pi's placement with
Tau's user-visible text, i.e. synthesize at the boundary but mark it clearly.

**5. Pi's transcript is extensible; Tau's is closed.**

`pi/packages/agent/src/types.ts:296-319`:

```typescript
export interface CustomAgentMessages {
	// Empty by default - apps extend via declaration merging
}

export type AgentMessage = Message | CustomAgentMessages[keyof CustomAgentMessages];
```

An app declares `artifact` or `notification` message types, stores them in the same transcript,
and filters them out in `convertToLlm`. Tau's `AgentMessage` is a fixed union. This is why Pi
needs `convertToLlm` as a *required* hook while Tau needs only the small
`_provider_context` filter — the generality has a cost, paid once at the seam.

**6. Pi splits into three tiers where Tau uses two.**

| Pi | Lines | Tau | Lines |
|---|---|---|---|
| `agent-loop.ts` | 792 | `loop.py` | 318 |
| `agent.ts` (the Agent class) | 577 | — | — |
| `harness/agent-harness.ts` | 1,185 | `harness.py` | 244 |

Your notes described exactly this — "the agent layer, and on top of it the agent class, and on
top of that the agent harness." Confirmed for Pi; Tau collapses the middle tier. Pi's
`AgentState` (`types.ts:327-352`) is correspondingly richer, exposing `isStreaming`,
`streamingMessage`, `pendingToolCalls`, and `errorMessage`.

**7. Error convention for tool authors is inverted.** Pi instructs tools to *throw*
(`types.ts:388`: "Execute the tool call. **Throw on failure instead of encoding errors in
`content`**"). Tau's tools may either throw or return an error result. Both loops catch, so
behaviour matches — but Pi's rule is better: one way to fail means tool authors can't get it
subtly wrong.

### Correcting my notes

My pasted notes claim Pi has "no memory, no multi agent orchestration, no subagents, no plan
mode." Layer 2 already falsifies part of that from directory structure alone —
`pi/packages/agent/src/harness/` contains:

```
compaction/compaction.ts             880
compaction/branch-summarization.ts   275
session/session.ts                   570
session/memory-store.ts              116
session/jsonl-store.ts               355
skills.ts                            375
tools/  bash · read · write · edit · edit-diff · image · file-mutation-queue · path-utils
```

So **compaction, session persistence, a memory store, skills, and the coding tools all live in
Pi's portable agent package** — not in the coding agent, and not absent. The notes are stale;
one transcript is even titled *"Simple Pi Subagents."* Subagents and plan mode are still
unverified and deferred to Layers 3 and 5.

### Answering the plan's questions, for this layer

**"How does the tool layer define schemas, dispatch handlers, and feed results back?"**

*Define:* `AgentTool` (`tools.py:76-90`) carries a JSON Schema in `parameters`, plus
description and optional `prompt_snippet`/`prompt_guidelines` that get folded into the system
prompt, plus optional renderers. *Dispatch:* the loop builds `tool_by_name`
(`loop.py:80`), looks up by name, runs `before_tool_call`, awaits `execute`, runs
`after_tool_call` (`loop.py:225-282`). *Feed back:* every outcome — success, block,
cancellation, unknown tool, exception — becomes a `ToolResultMessage` keyed by
`tool_call_id`, appended to history and emitted as events. The model reads errors as
observations.

**"What is the loop's termination condition?"**

No tool calls in the assistant message **and** no queued steering/follow-up messages
(`loop.py:87`, `loop.py:141`). Escape hatches: `max_turns`, and `stop_reason ∈ {error,
aborted}`. Pi adds `shouldStopAfterTurn` and unanimous `terminate`.

**"How is conversation state stored and replayed across turns?"**

The loop appends to a caller-owned `list` and also reports new messages via `AgentEndEvent`.
The critical mechanic is the **two views** of that list (§3.2): the durable transcript keeps
everything; `_provider_context` / `convertToLlm` filters it to what a provider will accept.
Durable *storage* is not this layer's job — that's `session/` (Layer 3 / Layer 5).

---

## 5. If I rebuilt this layer from scratch

**Tier 1 — roughly 150 lines.**

1. **The ten events.** Same names as both repos.
2. **The nested loop**, with `has_more_tools = bool(assistant.tool_calls)` as the condition —
   branch on content, not on `stop_reason`.
3. **`max_turns` from day one.** A runaway loop burning tokens is the first bug you'll hit.
4. **Terminal `stop_reason` exits the run.**
5. **`AgentTool` with `name`/`description`/`parameters`/`execute`.** Three tools:
   `read_file`, `write_file`, `run_shell`.
6. **Every tool failure becomes a tool result with `is_error=True`.** Never throw out of the
   loop.
7. **Keep the loop stateless.** A generator that takes inputs and yields events.

**Tier 2 — the seams.**

8. **`before_tool_call` / `after_tool_call`.** Do this *before* you need permissions, so the
   approval gate is a hook implementation rather than surgery. Prefer Pi's object return over
   Tau's tuple.
9. **A harness** owning messages, listeners, the cancellation token, and a `_running` guard.
10. **Orphaned tool-result repair** — the same day you add cancellation, not later. Do *both*
    placements, and understand why they aren't interchangeable: a message synthesized in the
    wire-level transform (Pi) never enters the transcript, so it cannot be user-visible by
    construction. So — synthesize a real, labelled `"Tool call interrupted"` message in the
    harness (Tau's placement) so the user and resumed sessions see what happened, *and* keep a
    wire-level repair as a backstop for orphans that slip through by another route (Pi's
    placement). The harness repair is for honesty; the wire repair is for safety.
11. **The two views of history.** One `to_provider_context(messages)` function, called on the
    way into every request. Even if it only drops empty failed turns, having the seam means
    compaction has somewhere to live.
12. **Steering and follow-up queues.** Cheap here, and they're what makes the agent feel
    interactive.
13. **`CancelledError` re-raised before the broad `except`.** Python-specific and easy to get
    wrong.

**Tier 3.**

14. **`transform_context`** — then compaction is an implementation of it, and the loop stays
    untouched. This is the single most important structural decision for Tier 3.
15. **Parallel tool execution**, with Pi's conservative rule: any sequential tool in the batch
    forces the whole batch sequential.
16. **`should_stop_after_turn`** for graceful stops before the context fills.

**Skip unless you need them:** `terminate` (Tau shows an unused field is worse than no field),
`prepare_next_turn` model switching, extensible custom message types — Pi's `convertToLlm`
requirement is the tax that generality charges.

**The test that proves this layer:** with `FakeProvider` scripting two turns — one with a tool
call, one without — assert the exact event sequence and that the loop stops after turn two.
If that test is easy to write, the layer is right.

---

## Files read for this layer

| File | Lines | Role |
|---|---|---|
| `tau/src/tau_agent/loop.py` | 318 | the loop, traced in §3 |
| `tau/src/tau_agent/tools.py` | 118 | tool definition and result |
| `tau/src/tau_agent/events.py` | 87 | the ten events |
| `tau/src/tau_agent/harness.py` | 244 | stateful wrapper, orphan repair |
| `pi/packages/agent/src/types.ts` | 437 | Pi's full agent contract |
| `pi/packages/agent/src/agent-loop.ts` | 792 | targeted (termination, parallel, `terminate`) |
| `pi/packages/ai/src/api/transform-messages.ts` | — | targeted (orphan repair placement) |

Verification greps run: `terminate` reads, `execution_mode` reads, `asyncio.gather`/`TaskGroup`
presence, orphan-repair sites across all Pi packages, `transformContext` wiring.

Not yet read: `pi/packages/agent/src/agent.ts` (577, the middle tier),
`harness/agent-harness.ts` (1,185), `harness/compaction/*` (1,287) — all deferred to Layer 3,
where they belong. `tau_agent/messages.py` (277) and `session/*` (383) also deferred to
Layer 3.

---

*Next: Layer 3 — the coding environment (`pi/packages/coding-agent` 55,895 lines ↔
`tau_coding` 29,771). The largest layer by far, and where compaction, sessions, skills, and
the actual file/shell tools get wired into policy.*
