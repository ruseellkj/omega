# Layer 1 — Provider / Stream

`pi/packages/ai` ↔ `tau/src/tau_ai`

> Read this layer first. Everything above it consumes what this layer produces, and the
> single design decision made here — *what an assistant response looks like as a sequence of
> events* — determines the shape of every layer above.

---

## 1. The problem this layer solves

Every model vendor invented its own HTTP API. Different URLs, different request bodies,
different names for the same idea, different ways of streaming partial output back, different
words for "I stopped because I want to call a tool." Anthropic sends one shape, OpenAI
another, Google a third, and Bedrock wraps a fourth in AWS signing.

If the agent loop talked to vendors directly, then every vendor quirk would leak upward into
the loop, the tool dispatcher, and eventually the terminal renderer. Adding a second provider
would mean editing all of them. That is the trap — and it is exactly the trap of an agent
"tied to Anthropic."

So this layer's job is a translation service with one rule:

> **Upward, everything looks the same. Downward, every vendor is different.**

It defines one neutral vocabulary for "a model is responding," then writes one adapter per
vendor whose only job is to convert that vendor's stream into the neutral vocabulary. Nothing
above this layer ever learns which vendor answered.

A useful analogy: this is a **power adapter**, not a translator. A translator conveys meaning
loosely. An adapter is a strict contract — the plug either fits or it doesn't. There is a
fixed set of events, and an adapter that emits something outside the set is broken.

---

## 2. The core contract

### 2.1 The provider interface — Tau states it in 14 lines

`tau/src/tau_agent/provider.py:13-32`

```python
class CancellationToken(Protocol):
    def is_cancelled(self) -> bool:
        """Return whether the current stream should stop."""
        ...


class ModelProvider(Protocol):
    """Provider-neutral Pi-compatible model stream interface."""

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        """Stream one model response as assistant message events."""
        ...
```

That is the entire provider abstraction. One method. In: which model, the system prompt, the
conversation, the available tools, and a way to cancel. Out: an async stream of neutral events.

**Where this file lives is as important as what it says.** The contract is defined in
`tau_agent` — the portable core — and `tau_ai` merely re-exports it:

`tau/src/tau_ai/provider.py:1-5`

```python
"""Public re-exports of the provider contract implemented by Tau adapters."""

from tau_agent.provider import CancellationToken, ModelProvider

__all__ = ["CancellationToken", "ModelProvider"]
```

So the dependency arrow points **from adapters to core**, never the reverse. The core defines
what it needs; adapters conform. Invert that — let the provider package own the interface —
and your "portable" core now imports your vendor code, which means it isn't portable. This is
dependency inversion, and it is the single most common thing to get backwards.

Note also `Protocol`: Tau uses Python structural typing, so an adapter satisfies
`ModelProvider` by *having the right method*, with no base class and no import of the
protocol at all. `FakeProvider` (§2.4) never inherits from anything.

### 2.2 The event vocabulary — twelve events, and that's all

`tau/src/tau_agent/provider_events.py:12-107` (abridged; every event is a Pydantic model)

```python
class AssistantStartEvent(WireModel):
    type: Literal["start"] = "start"
    partial: AssistantMessage

class TextDeltaEvent(WireModel):
    type: Literal["text_delta"] = "text_delta"
    content_index: int
    delta: str
    partial: AssistantMessage

class ToolCallEndEvent(WireModel):
    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int
    tool_call: ToolCall
    partial: AssistantMessage

DoneReason  = Literal["stop", "length", "toolUse"]
ErrorReason = Literal["aborted", "error"]

class AssistantDoneEvent(WireModel):
    type: Literal["done"] = "done"
    reason: DoneReason
    message: AssistantMessage
```

The full set, `provider_events.py:93-107`:

| Lifecycle | Text | Thinking | Tool call |
|---|---|---|---|
| `start` | `text_start` | `thinking_start` | `toolcall_start` |
| `done` | `text_delta` | `thinking_delta` | `toolcall_delta` |
| `error` | `text_end` | `thinking_end` | `toolcall_end` |

Three observations that carry real weight:

**It's a start/delta/end triple per channel.** A model emits three kinds of content — visible
text, hidden reasoning, and tool invocations — and each gets the same three-phase treatment.
Learn one, you know all three.

**`content_index` positions the block.** Content is an ordered list on the message; the index
says which slot this event is about. That is what lets a renderer update block 2 while block 3
hasn't started.

**Every streaming event carries `partial: AssistantMessage`** — the whole message as it exists
*so far*. This is a deliberate, slightly expensive choice: a consumer never has to accumulate
deltas itself. A renderer can ignore `delta` entirely and just redraw `partial`. `stream.py`
implements it with a deep copy per event (`tau_ai/stream.py:40-41`):

```python
def _snapshot(message: AssistantMessage) -> AssistantMessage:
    return message.model_copy(deep=True)
```

Cost: one deep copy per token. Benefit: every consumer is stateless. For a terminal agent
that is a trade worth making, and worth *knowing* you made.

### 2.3 Pi's version of the same contract

Pi's events are the same twelve, in TypeScript. `pi/packages/ai/src/types.ts:501-513`:

```typescript
export type AssistantMessageEvent =
	| { type: "start"; partial: AssistantMessage }
	| { type: "text_start"; contentIndex: number; partial: AssistantMessage }
	| { type: "text_delta"; contentIndex: number; delta: string; partial: AssistantMessage }
	| { type: "text_end"; contentIndex: number; content: string; partial: AssistantMessage }
	| { type: "thinking_start"; contentIndex: number; partial: AssistantMessage }
	| { type: "thinking_delta"; contentIndex: number; delta: string; partial: AssistantMessage }
	| { type: "thinking_end"; contentIndex: number; content: string; partial: AssistantMessage }
	| { type: "toolcall_start"; contentIndex: number; partial: AssistantMessage }
	| { type: "toolcall_delta"; contentIndex: number; delta: string; partial: AssistantMessage }
	| { type: "toolcall_end"; contentIndex: number; toolCall: ToolCall; partial: AssistantMessage }
	| { type: "done"; reason: Extract<StopReason, "stop" | "length" | "toolUse">; message: AssistantMessage }
	| { type: "error"; reason: Extract<StopReason, "aborted" | "error">; error: AssistantMessage };
```

Compare that against Tau's list. Same twelve `type` strings, same fields, same meanings. The
only differences are `contentIndex` vs `content_index` and TS unions vs Pydantic models.

**This is the most important single finding of Layer 1.** Two implementations, two languages,
written by different people — and the event vocabulary is *identical*, down to the string
literals. That is not coincidence and not copying-for-its-own-sake: the vocabulary **is** the
architecture. Everything else in both codebases is negotiable; this isn't.

Pi's provider entry point is a function type rather than a Protocol method,
`types.ts:320-324`:

```typescript
export type StreamFunction<TApi extends Api = Api, TOptions extends StreamOptions = StreamOptions> = (
	model: Model<TApi>,
	context: Context,
	options?: TOptions,
) => AssistantMessageEventStream;
```

with the conversation bundled into a `Context` object (`types.ts:487-491`):

```typescript
export interface Context {
	systemPrompt?: string;
	messages: Message[];
	tools?: Tool[];
}
```

Tau passes `system`, `messages`, `tools` as three separate keyword arguments; Pi bundles them
into one `Context`. Same three pieces of information.

### 2.4 The fake provider — 38 lines that make the whole system testable

`tau/src/tau_ai/fake.py:13-38`

```python
class FakeProvider:
    """A provider that replays predefined assistant event streams."""

    def __init__(self, streams: Iterable[Iterable[AssistantMessageEvent]]) -> None:
        self._streams = [list(stream) for stream in streams]
        self.calls: list[tuple[str, str, list[AgentMessage], list[AgentTool]]] = []

    def stream_response(self, *, model, system, messages, tools, signal=None):
        self.calls.append((model, system, list(messages), list(tools)))
        stream = self._streams.pop(0) if self._streams else []

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            for event in stream:
                if signal is not None and signal.is_cancelled():
                    return
                yield event

        return iterator()
```

This is the payoff for keeping the contract small. Because `ModelProvider` is one method
returning events, a complete test double is 38 lines — and it does three jobs: replays
scripted event sequences, **records every call** for assertions (`self.calls`), and honours
cancellation. With this, the entire agent loop, tool dispatch, and session handling can be
tested with zero network access and deterministic output.

Pi's equivalent is `pi/packages/ai/src/providers/faux.ts` (541 lines) — same role, far more
elaborate.

**Design rule to carry forward:** the fake is the proof. If your provider interface is too
large to fake in a page of code, it's too large.

---

## 3. Execution path, traced

Tau's `canonicalize_provider_stream` (`tau_ai/stream.py:88-208`) is the clearest place to
watch a raw provider stream become canonical events. It's a bridge: vendor parsers emit an
older internal `ProviderEvent` shape, and this converts it to the public contract.

**Setup** (`stream.py:100-104`) — the accumulator and channel tracking:

```python
partial = AssistantMessage(api=api, provider=provider, model=model)
active_index: int | None = None
active_kind: str | None = None
started = False
terminal = False
```

**Step 1 — swallow retries** (`stream.py:107-109`):

```python
if isinstance(event, ProviderRetryEvent):
    # Retries are provider-internal at the Pi AI boundary.
    continue
```

A retry is not part of the neutral vocabulary. The loop above must never learn that attempt 1
failed and attempt 2 succeeded. Retry is an implementation detail of *this* layer.

**Step 2 — guarantee exactly one `start`** (`stream.py:110-117`): emit `AssistantStartEvent`
on the provider's start event, or lazily on the first content event if the provider never
sent one.

**Step 3 — a text delta arrives** (`stream.py:119-135`):

```python
if isinstance(event, ProviderTextDeltaEvent):
    if active_kind != "text":
        async for end_event in _end_active_block(partial, active_index):
            yield end_event                      # close whatever was open
        active_index = len(partial.content)      # new slot
        active_kind = "text"
        partial.content.append(TextContent(text=""))
        yield TextStartEvent(content_index=active_index, partial=_snapshot(partial))
    block = partial.content[active_index]
    block.text += event.delta                    # accumulate
    yield TextDeltaEvent(content_index=active_index, delta=event.delta,
                         partial=_snapshot(partial))
```

Read the channel-switch logic carefully — it's the crux. Providers interleave reasoning and
text freely. When the channel changes, `_end_active_block` (`stream.py:44-63`) emits the
closing `*_end` for the previous block *before* opening the new one. That preserves the
invariant **every `*_start` gets a matching `*_end`**, which is what allows a renderer to be a
simple state machine.

**Step 4 — a tool call arrives** (`stream.py:156-168`): close any open block, then emit
`ToolCallStartEvent` immediately followed by `ToolCallEndEvent`. Tool calls arrive atomically
from this parser, so there are no `toolcall_delta` events here — the vocabulary permits
streaming tool arguments, this path just doesn't need it.

**Step 5 — the response ends** (`stream.py:169-190`):

```python
final = event.message.model_copy(deep=True)
final.content = [block.model_copy(deep=True) for block in partial.content]
_copy_replay_metadata(final, event.message)
final.stop_reason = _finish_reason(event.finish_reason, has_tools=bool(final.tool_calls))
yield AssistantDoneEvent(reason=final.stop_reason, message=final)
```

Note the comment at `stream.py:175-176`: *"Preserve the exact streamed content order. The
parser's final message remains authoritative only for response metadata/usage."* Content
comes from what was actually streamed; usage and metadata come from the provider's final
message. Trusting the provider's final content instead would risk contradicting what the user
already watched appear on screen.

`_copy_replay_metadata` (`stream.py:66-78`) then copies `thinking_signature`, `redacted`, and
`text_signature` across. These are opaque vendor tokens that must be handed back verbatim on
the next turn or multi-turn reasoning breaks. Streaming doesn't carry them; the final message
does.

**Step 6 — normalize the stop reason** (`stream.py:80-85`):

```python
def _finish_reason(value: str | None, *, has_tools: bool) -> str:
    if has_tools or value in {"tool_calls", "tool_use", "toolUse"}:
        return "toolUse"
    if value in {"length", "max_tokens", "MAX_TOKENS", "incomplete"}:
        return "length"
    return "stop"
```

Six vendor spellings collapse into three canonical reasons. This tiny function is
provider-agnosticism in miniature — and `stop_reason == "toolUse"` is precisely what the
agent loop (Layer 2) branches on to decide whether to keep going.

**Step 7 — guarantee a terminal event** (`stream.py:201-208`):

```python
if not started:
    yield AssistantStartEvent(partial=_snapshot(partial))
if not terminal:
    error = partial.model_copy(deep=True)
    error.stop_reason = "error"
    error.error_message = "Provider stream ended without a terminal event"
    error.usage = Usage()
    yield AssistantErrorEvent(reason="error", error=error)
```

If a socket dies mid-stream, the consumer still receives a terminal event. **The contract is
therefore: exactly one `start`, exactly one `done`-or-`error`, always.** Buying that invariant
here — in one place, cheaply — means the loop never needs a timeout, a sentinel, or a
"what if the stream just stops" branch.

### 3.1 Errors travel in-band, never as exceptions

Pi states this as an explicit contract on `StreamFunction` (`types.ts:313-319`):

```
// Contract:
// - Must return an AssistantMessageEventStream.
// - Once invoked, request/model/runtime failures should be encoded in the
//   returned stream, not thrown.
// - Error termination must produce an AssistantMessage with stopReason
//   "error" or "aborted" and errorMessage, emitted via the stream protocol.
```

Both codebases obey it. **Why it matters:** a stream that emitted 500 tokens and *then* failed
has two things to report — the partial content and the failure. An exception carries only the
failure and throws the content away. An `error` event carries both, because it ships the
accumulated `AssistantMessage` with `stopReason: "error"`.

This is a genuinely counter-intuitive rule worth internalizing: **in streaming systems,
failures are data, not control flow.**

---

## 4. Pi vs Tau — concept versus accident

### Identical (this is the architecture)

| Concern | Evidence |
|---|---|
| The twelve-event vocabulary, including `type` string literals | `types.ts:501-513` ≡ `provider_events.py:12-107` |
| `partial` snapshot on every streaming event | both |
| Start/delta/end triple per content channel | both |
| `content_index` to position blocks | both |
| Canonical stop reasons `stop` / `length` / `toolUse` | `types.ts:391`, `provider_events.py:77` |
| Errors in-band, terminal event guaranteed | `types.ts:313-319`, `stream.py:201-208` |
| Content block types: text / thinking / toolCall / image | `types.ts:338-366`, `messages.py` |
| Opaque reasoning signatures replayed across turns | `types.ts:344-352`, `stream.py:66-78` |
| Adapters depend on the core, not vice-versa | `tau_ai/provider.py:1-5`; Pi's `ai` owns types, `agent` consumes |

### Idiomatic to the language (accident — safe to change)

| Pi (TypeScript) | Tau (Python) |
|---|---|
| `contentIndex` | `content_index` |
| Discriminated union on `type` | Pydantic models + `Field(discriminator="type")` (`provider_events.py:93-107`) |
| `StreamFunction` — a function type | `ModelProvider` — a `Protocol` method |
| `Context { systemPrompt, messages, tools }` | three keyword args |
| `AbortSignal` (web standard) | `CancellationToken` Protocol with `is_cancelled()` |
| `AssistantMessageEventStream` | `AsyncIterator[AssistantMessageEvent]` |
| Structural typing via interfaces | Structural typing via `Protocol` |

Both languages landed on **structural** typing for the provider seam — TS interfaces and
Python Protocols. Neither used inheritance. Worth noting: an adapter should not have to import
a base class to be an adapter.

### Genuinely different designs (not accident — different decisions)

**1. The capability/quirk matrix. Pi has one; Tau doesn't.**

Pi carries per-API compatibility descriptors — `OpenAICompletionsCompat` alone has ~25 fields
(`types.ts:519-574`), plus `OpenAIResponsesCompat`, `AnthropicMessagesCompat`, `BedrockCompat`.
A sample:

```typescript
supportsStore?: boolean;
supportsDeveloperRole?: boolean;        // "system" vs "developer" role
supportsFinishReason?: boolean;         // some providers omit it entirely
maxTokensField?: "max_completion_tokens" | "max_tokens";
requiresToolResultName?: boolean;
requiresAssistantAfterToolResult?: boolean;
requiresThinkingAsText?: boolean;       // wrap thinking in <thinking> tags
cacheControlFormat?: "anthropic";
```

And the field that tells the whole story (`types.ts:541-551`) — ten different ways vendors
express "think harder":

```typescript
thinkingFormat?: "openai" | "openrouter" | "deepseek" | "together" | "zai"
               | "qwen" | "chat-template" | "qwen-chat-template"
               | "string-thinking" | "ant-ling";
```

Tau has `model_limits.py` (48 lines) and no quirk matrix at all.

**This is the real cost curve of provider-agnosticism.** The neutral contract stays tiny —
Tau proves 5 lines is enough. But *coverage* is not free: reality is a combinatorial mess of
vendor deviations, and it has to live somewhere. Pi's answer is to make it declarative data
per model rather than branches inside adapters. That's the right answer, and it's still
hundreds of fields.

**2. Provider ≠ API.** Pi separates the two (`types.ts:16-26` and `34-72`): **10** wire
formats, **38** providers. Most providers speak `openai-completions`; only the format needs an
adapter. Tau ships 5 adapter files. The lesson for your own build: key adapters on *wire
format*, not vendor name, or you'll write the same adapter thirty times.

**3. Where authentication lives — Tau's is arguably cleaner.**

Pi puts OAuth inside the provider package: `ai/src/auth/oauth/` holds `anthropic.ts`,
`openai-codex.ts`, `github-copilot.ts`, `openrouter.ts`, `kimi-coding.ts`, `radius.ts`. (This
is your notes' "two types of providers" — API key vs. the $200-subscription OAuth path —
implemented as six flows.)

Tau has no OAuth in `tau_ai` at all. It lives in the *application* layer —
`tau_coding/oauth_anthropic.py`, `oauth_github_copilot.py`, `oauth_device.py`,
`oauth_registry.py`, `credentials.py`. `tau_ai` instead accepts a resolver callback
(`tau_ai/env.py`):

```python
@dataclass(frozen=True, slots=True)
class RuntimeProviderAuth:
    api_key: str
    base_url: str | None = None
    headers: Mapping[str, str] | None = None

type RuntimeProviderAuthResolver = Callable[[], Awaitable[RuntimeProviderAuth]]
```

So `tau_ai` knows only *"something will hand me credentials when I ask, just before the
call."* It never learns what OAuth is, never touches a token store, never opens a browser.
Credential storage and refresh are the app's business. The `Awaitable` return is what makes
mid-session token refresh possible without the adapter caring.

**4. Cost and cache are first-class in Pi's types.** `Usage` (`types.ts:368-389`) breaks out
`cacheRead`, `cacheWrite`, `cacheWrite1h`, `reasoning`, and a nested `cost` object; `ModelCost`
(`types.ts:743-758`) supports tiered pricing by input-token threshold. Prompt caching is
visible right in the type system — `CacheRetention = "none" | "short" | "long"`
(`types.ts:101`), `cacheControlFormat`, `supportsLongCacheRetention`,
`sessionAffinityFormat` (so repeat requests land on the same replica and actually hit cache).
This is where your note *"KV cached tokens ki cost kam hoti hai"* becomes code: cache reads
are priced separately from fresh input, so the type system tracks them separately.

**5. Scope. Pi's `ai` also does image generation** (`ProviderImages`, `ImagesModel`,
`openrouter-images.ts`) and a generated model catalog (`models.generated.ts`,
`model-catalog.ts`, `models-store.ts`). Tau has neither.

### Answering the plan's questions, for this layer

**"How does provider-agnosticism actually work — and where exactly does translation happen?"**

Three parts. (a) A neutral *event* vocabulary — twelve events — plus a neutral *message* shape
(`AgentMessage` / `AssistantMessage` with text/thinking/toolCall content blocks). (b) A
one-method interface owned by the core, `stream_response(...) -> AsyncIterator[event]`.
(c) One adapter per **wire format**, and nothing else in the system knowing vendors exist.

Translation happens in exactly one place: inside the adapter, on the way out. Concretely in
Tau, `canonicalize_provider_stream` (`stream.py:88-208`) — with `_finish_reason`
(`stream.py:80-85`) as the smallest complete example of normalization. Requests translate on
the way *in* within each adapter (`anthropic.py`, `openai_compatible.py`, …).

**"Where would prompt-cache-friendly prefix ordering matter, and does either repo do it?"**

It matters here in that the *type system* exposes the controls — `CacheRetention`,
`cacheControlFormat: "anthropic"`, `supportsCacheControlOnTools`, `sessionAffinityFormat`,
`prompt_cache_key` — and Pi has a dedicated `api/openai-prompt-cache.ts`. But *ordering* is
not decided here: caches key on a stable prefix hash (your note: *"cache is based on hash
from top to end"*), so whoever assembles the message list controls cache hits. That's Layers 2
and 3. Flagged, and deliberately deferred — with the note that Tau has
`dev-notes/prompt-caching.md`, to be read for Layer 3.

---

## 5. If I rebuilt this layer from scratch

**Minimum viable version — Tier 1.** Roughly 200 lines.

1. **Message and content types.** `UserMessage`, `AssistantMessage`, `ToolResultMessage`;
   content blocks `TextContent`, `ThinkingContent`, `ToolCall`. Make `toolResult` its own role
   (as both repos do, `types.ts:415-431`) rather than smuggling results inside user messages.
2. **The twelve events, verbatim.** Copy the vocabulary. It's the one part of both codebases I
   would not redesign, and matching it means Pi and Tau stay readable as references.
3. **`partial` on every streaming event.** Pay the copy. Stateless consumers are worth it.
4. **The one-method interface, defined in the core package.** Get the dependency direction
   right on day one; it is painful to reverse later.
5. **One adapter.** Whichever vendor you know best.
6. **The fake.** Write it *with* the first adapter, not after. It is the test that the seam is
   real.
7. **Two invariants, enforced in the adapter:** exactly one `start`; exactly one terminal
   event, synthesized if the vendor fails to send one.
8. **Errors as `error` events, never exceptions.**

**Deliberately deferred to Tier 2+:** the quirk matrix (add flags only when a second adapter
forces you to), OAuth (env-var API key is fine; keep the resolver-callback seam so OAuth can
land in the app layer later), the model catalog, cost tiers, images, transports beyond SSE.

**The Tier-2 test that proves this layer is right:** add a second adapter for a *different
wire format*. If anything outside `provider/` has to change, the abstraction leaked — fix the
contract rather than special-casing the caller. That's why the plan makes a second provider a
Tier-2 requirement instead of a Tier-3 nicety.

**One thing to copy that is easy to miss:** keying adapters on wire format rather than vendor.
Pi's 38-providers-over-10-APIs split is the difference between adding a provider being a
config entry versus a new file.

---

## Files read for this layer

| File | Lines | Role |
|---|---|---|
| `tau/src/tau_agent/provider.py` | 32 | the contract |
| `tau/src/tau_agent/provider_events.py` | 107 | the twelve events |
| `tau/src/tau_ai/provider.py` | 5 | re-export (dependency direction) |
| `tau/src/tau_ai/events.py` | 37 | re-export |
| `tau/src/tau_ai/stream.py` | 208 | canonicalization, traced in §3 |
| `tau/src/tau_ai/fake.py` | 38 | test double |
| `tau/src/tau_ai/env.py` | 153 (partial) | auth resolver seam |
| `pi/packages/ai/src/types.ts` | 795 | Pi's whole contract |

Structure surveyed but not read line-by-line: Pi's `api/` adapters (~8,000 lines across 10
wire formats), `auth/oauth/` (6 flows), `models*.ts` catalog; Tau's `anthropic.py`,
`openai_compatible.py`, `openai_codex.py`, `google.py`, `mistral.py` (~4,000 lines).

**Transcript coverage for this layer was poor** — grepping 19 auto-caption files for
provider-abstraction language returned only fragments ("abstraction across providers and
context"). The code carried this layer; transcripts are noted as low-yield here rather than
padded with weak quotes.

---

*Next: Layer 2 — the agent loop and tools (`pi/packages/agent` ↔ `tau_agent`, where
`loop.py` is 318 lines and the entire core is 1,752).*
