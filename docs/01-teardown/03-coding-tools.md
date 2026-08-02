# Layer 3a — The Coding Toolset

`pi/packages/agent/src/harness/tools/` + `pi/packages/coding-agent/src/core/tools/`
↔ `tau/src/tau_coding/tools.py`

> Layer 3 is 4× the size of every other layer (Pi's `coding-agent` alone is 55,895 lines), so
> it is split. **This file covers the tools.** `03b-context-sessions-compaction.md` covers
> system-prompt assembly, sessions, compaction, and prompt caching.

---

## 1. The problem this layer solves

Layers 1 and 2 are domain-agnostic — they'd serve a customer-support agent unchanged. This is
where the agent becomes a *coding* agent, and the entire difference is the toolset: the model
gets the ability to look at and change a filesystem, and to run commands.

Which sounds simple, and is where every hard problem lives:

- A model asks to read a 400 MB log. Do you put it in the context window?
- A model runs `npm install`, which prints 40,000 lines. Same question, worse.
- A model wants to edit line 200 of a file it read ten turns ago, which has since changed.
- Two tool calls in one batch write the same file.
- A model asks to read `../../../.ssh/id_rsa`.
- A command hangs forever.

None of these are model problems. They're **tool design** problems, and the answers are what
separate a demo from something you'd let near a real repository.

---

## 2. The inventory — and the surprise

### Both portable layers ship exactly four tools

`pi/packages/agent/src/harness/tools/index.ts`, in full:

```typescript
export { …, createBashTool } from "./bash.ts";
export { createEditTool, … } from "./edit.ts";
export { createReadTool, … } from "./read.ts";
export { createWriteTool, type WriteToolInput } from "./write.ts";
```

Four factories: **bash, edit, read, write.** Tau registers the same four
(`tau_coding/tools.py` — verified: the only tool names in the file are `"bash"`, `"read"`,
`"write"`, `"edit"`).

**There is no `grep` tool, no `glob`, no `ls`, and no `find` in either portable layer.** Search
happens because the model runs `grep` or `find` through `bash`.

That's worth sitting with. Claude Code ships dedicated Glob and Grep tools; Pi and Tau's
portable core does not. The minimum viable coding toolset is **four tools**, and one of them
(`bash`) is a universal escape hatch that subsumes the rest.

### What the coding *app* adds

`pi/packages/coding-agent/src/core/tools/` — 4,109 lines:

| Tool | app | portable | ratio |
|---|---|---|---|
| `bash` | 505 | 161 | 3.1× |
| `edit` | 437 | 127 | 3.4× |
| `edit-diff` | 560 | 500 | 1.1× |
| `read` | 351 | 144 | 2.4× |
| `write` | 267 | 39 | 6.8× |
| **`grep`** | 385 | — | new |
| **`find`** | 374 | — | new |
| **`ls`** | 225 | — | new |
| `truncate` | 276 | *(in `harness/utils`)* | — |
| `output-accumulator` | 222 | — | new |

Two conclusions:

1. **Seven tools is the real coding-agent toolset**: bash, read, write, edit, grep, find, ls.
   Dedicated search tools are an *optimization over `bash`*, not a necessity — they exist
   because `grep -rn` through a shell returns unbounded output, while a purpose-built grep tool
   can cap results, format them for the model, and skip `.git`/`node_modules` by default.
2. **The app's versions are 2–7× larger than the portable ones.** That extra bulk is not
   functionality — it's *policy*: truncation, rendering, approval integration, path
   confinement. The portable tool does the thing; the app tool does the thing *safely and
   legibly*. That split is the layering lesson of this whole file.

And a notable absence: **no todo/task/planning tool in either repo.** Claude Code has one. Pi
puts plan mode in `examples/extensions/plan-mode/` instead (verified in Layer 2's notes —
`cli/args.ts:300`: *"Extensions can register additional flags (e.g., `--plan` from plan-mode
extension)"*).

---

## 3. The bash tool, traced

`pi/packages/agent/src/harness/tools/bash.ts` is 161 lines and contains most of the
interesting decisions.

### 3.1 The schema is two fields

```typescript
const bashSchema = Type.Object({
	command: Type.String({ description: "Bash command to execute" }),
	timeout: Type.Optional(Type.Number({ description: "Timeout in seconds (optional, no default timeout)" })),
});
```

`bash.ts:11-14`. Note "**no default timeout**" — a deliberate choice with a real tradeoff. A
default timeout would kill legitimate long builds; no default means a hung command hangs until
the user cancels. Pi picks the latter and relies on cancellation (Layer 2's `signal`) as the
backstop.

`MAX_TIMEOUT_SECONDS = 2_147_483_647 / 1000` (`bash.ts:8`) guards the int32 millisecond
overflow in `setTimeout` — the sort of bug you only find in production.

### 3.2 The description carries the policy

`bash.ts:57`:

```typescript
description: `Execute a bash command in the current working directory. Returns stdout and stderr. Output is truncated to last ${DEFAULT_MAX_LINES} lines or ${DEFAULT_MAX_BYTES / 1024}KB (whichever is hit first). If truncated, full output is saved to a temp file. Optionally provide a timeout in seconds.`,
```

The limits are **interpolated into the description**, so the model is told the constraint it
will be subject to. This is the concrete version of Layer 2's `prompt_snippet` idea: a tool's
prompt text is part of the tool, and it stays in sync with the code because it's built from the
same constants.

### 3.3 Streaming output, throttled

`bash.ts:9` sets `BASH_UPDATE_THROTTLE_MS = 100`, and `scheduleOutputUpdate`
(`bash.ts:92-105`) coalesces chunk callbacks into at most ten `onUpdate` calls per second.

Without this, a chatty command emits an `onUpdate` per chunk, each becoming a
`ToolExecutionUpdateEvent` (Layer 2), each triggering a re-render. The throttle is what keeps
`npm install` from freezing the UI. **Streaming needs backpressure, and 100 ms is the number
both repos landed near.**

### 3.4 Truncation tells the model exactly what it lost

`bash.ts:130-142`:

```typescript
if (capture.truncation.truncated) {
	details = { truncation: capture.truncation, fullOutputPath: capture.fullOutputPath };
	const startLine = capture.truncation.totalLines - capture.truncation.outputLines + 1;
	const endLine = capture.truncation.totalLines;
	if (capture.truncation.lastLinePartial) {
		outputText += `\n\n[Showing last ${formatSize(capture.truncation.outputBytes)} of line ${endLine} (line is ${lastLineSize}). Full output: ${capture.fullOutputPath}]`;
	} else if (capture.truncation.truncatedBy === "lines") {
		outputText += `\n\n[Showing lines ${startLine}-${endLine} of ${capture.truncation.totalLines}. Full output: ${capture.fullOutputPath}]`;
	} else {
		outputText += `\n\n[Showing lines ${startLine}-${endLine} of ${capture.truncation.totalLines} (${formatSize(DEFAULT_MAX_BYTES)} limit). Full output: ${capture.fullOutputPath}]`;
	}
}
```

Four things at once, and all four matter:

- It keeps the **tail**, not the head. Compiler errors and stack traces are at the end.
- It says **which limit** was hit — lines, bytes, or one absurdly long line.
- It gives **absolute line numbers** ("lines 13235-15234 of 15234"), so the model knows the
  scale of what it can't see.
- It writes the full output to a temp file and **hands the model the path**. The model can then
  `sed -n '400,500p' /tmp/…` to page through it.

That last point is the design principle: **truncation is not data loss if you tell the model
how to get the rest.** The context window is a viewport, not the storage.

Tau does the same, and goes one step further — its read tool embeds the *exact recovery
command* in the truncation notice (`tau_coding/tools.py:329-330`):

```python
f"{format_size(DEFAULT_MAX_OUTPUT_BYTES)} limit. Use bash: sed -n "
f"'{start_display}p' {raw_path} | head -c {DEFAULT_MAX_OUTPUT_BYTES}]"
```

The tool teaches the model its own workaround. Cheap, and it converts a dead end into a next
step.

**The constants are the same in both repos:**

| | Pi | Tau |
|---|---|---|
| max lines | `DEFAULT_MAX_LINES` | `DEFAULT_MAX_OUTPUT_LINES = 2_000` |
| max bytes | `DEFAULT_MAX_BYTES` | `DEFAULT_MAX_OUTPUT_BYTES = 50 * 1024` |

2,000 lines or 50 KB. Independently arrived at, which suggests it's roughly the right order of
magnitude: big enough for a real stack trace, small enough not to blow a context window in one
tool call.

### 3.5 Failure carries the output with it

`bash.ts:144-154`:

```typescript
const appendStatus = (status: string): string => `${outputText ? `${outputText}\n\n` : ""}${status}`;
if (capture.cancelled) throw new Error(appendStatus("Command aborted"));
if (capture.executionError?.code === "timeout") {
	throw new Error(appendStatus(`Command timed out after ${timeout} seconds`), { cause: capture.executionError });
}
if (capture.executionError) throw capture.executionError;
if (capture.exitCode !== 0 && capture.exitCode !== undefined) {
	throw new Error(appendStatus(`Command exited with code ${capture.exitCode}`));
}
```

Note `appendStatus` — **the error message contains the captured output**. A test suite that
fails with exit code 1 must show the model the failures, not just "exited with code 1." Getting
this wrong is a classic agent bug: the model retries blindly because the failure told it
nothing.

This is also Pi's tool convention in action (Layer 2, `types.ts:388`: *"Throw on failure
instead of encoding errors in `content`"*). The tool throws; Layer 2's `_run_tool` equivalent
catches and converts it into a `ToolResultMessage` with `is_error=True`. Same destination,
one convention.

**A non-zero exit code is an error.** Worth noting because it's arguable — `grep` exits 1 when
it finds nothing, which is not a failure. The dedicated grep tool in `coding-agent` presumably
exists partly to fix exactly this.

### 3.6 Two extension seams

`bash.ts:36-39`:

```typescript
export interface BashToolOptions<TContext extends ExecutionToolContext = ExecutionToolContext> {
	commandPrefix?: string;
	prepare?: BashPrepare<TContext>;
}
```

`commandPrefix` is prepended to every command (`bash.ts:63`) — for `set -euo pipefail`, or
sourcing an env file. `prepare` (`bash.ts:68`) gets the mutable `BashExecution` object
(`command`, `cwd`, `env`, `inheritEnv`) before execution, so the app can rewrite the command —
which is where sandbox wrapping would go.

The portable tool therefore has **no sandboxing of its own**. It runs whatever it's given.
Confinement is the app's job, injected through `prepare`. Consistent with the layering, and
worth knowing before you trust the portable tool with anything.

---

## 4. Read, write, edit

### 4.1 Read is not "return the file"

From Tau's docstrings and truncation code (`tau_coding/tools.py:188-388`), the read tool does:

- Resolve relative paths against `cwd` (`tools.py:196`).
- Accept **line ranges**, so the model can page.
- Truncate to the same 2,000-line / 50 KB budget, keeping first-line-exceeds-limit as a
  distinct case (`tools.py:325`) — one 60 KB minified line needs a different message than
  10,000 normal lines.
- Handle **images**: files over `DEFAULT_MAX_SOURCE_IMAGE_BYTES` are refused with an explicit
  reason (`tools.py:229-235`), and there's an `_omitted_image_result` path (`tools.py:388`).
  Images become `ImageContent` blocks (Layer 1's content type), which is why that block type
  exists.
- Return `details` with the resolved path and truncation metadata (`tools.py:323`) — UI data
  that never reaches the model, per Layer 2's `content`/`details` split.

`_validate_local_read_path` (`tools.py:140`) is the confinement hook.

### 4.2 Write and edit share a per-path lock

`tau_coding/tools.py:427-430`, on the write tool:

> *"…by a per-path async lock so multiple writes/edits to the same resolved file…"*

Pi's equivalent is a whole file: `file-mutation-queue.ts` (56 lines portable, 61 in the app).

**This is the concurrency bug Layer 2 set up.** Pi executes tool calls in parallel
(`agent-loop.ts:425`), so two `edit` calls on one file can interleave: both read the original,
both write, and the second silently discards the first. Serializing per resolved path fixes it.

Two defences for one bug, and they're complementary: Layer 2's *"any sequential tool in the
batch forces the whole batch sequential"* is coarse and conservative; the per-path lock is
fine-grained and lets edits to *different* files still run concurrently. Tau has the lock even
though its loop never parallelizes — defence against a future change.

### 4.3 `edit` is find-and-replace, and `edit-diff` is the hard part

Tau's edit tool takes a list of items, *"Each edit item contains `oldText` and `newText`"*
(`tools.py:482`). Not line numbers — **exact string matching**.

That's the right call and worth understanding. Line numbers go stale the instant anything else
edits the file; an exact `oldText` either matches or fails loudly. Failing loudly is the
feature: the model gets "oldText not found" and re-reads, instead of corrupting line 200 of a
file that shifted by three lines.

`edit-diff.ts` being the largest tool file in both layers (**500 lines portable, 560 in the
app**) tells you where the difficulty actually is. Not deciding *what* to change — applying a
change to text reliably, and rendering the result as a readable diff.

---

## 5. Pi vs Tau — concept versus accident

### Identical

| Concern | Evidence |
|---|---|
| Exactly four portable tools: bash, read, write, edit | `harness/tools/index.ts`, `tau_coding/tools.py` |
| No grep/glob/ls/find in the portable layer | both |
| No todo/task/planning tool at all | both |
| Truncation budget ≈ 2,000 lines / 50 KB | `truncate.ts`, `tools.py:41-42` |
| Keep the **tail** of output, not the head | both |
| Full output spilled to a temp file, path given to the model | `bash.ts:131`, `tools.py:329` |
| Truncation notice states limit + absolute line numbers | `bash.ts:136-141`, `tools.py:332-351` |
| Limits interpolated into the tool description | `bash.ts:57`, `tools.py:368-369` |
| Per-path lock serializing writes/edits | `file-mutation-queue.ts`, `tools.py:427-430` |
| `edit` matches exact `oldText`, never line numbers | both |
| Images as first-class content with a size ceiling | `image.ts`, `tools.py:229-235` |
| Throttled progress updates during long commands | `bash.ts:9`, both |
| `details` for the UI, `content` for the model | both |

### Idiomatic (accident)

| Pi | Tau |
|---|---|
| TypeBox `Type.Object({...})` schemas | dict-shaped JSON Schema |
| `createBashTool(options)` factories | `create_read_tool_definition(*, cwd)` factories |
| `AbortSignal` | `ToolCancellationToken` |
| `oldText`/`newText` (camelCase in the schema) | same camelCase — *kept for wire parity* |
| throttle via `setTimeout` | throttle via asyncio |

Note that Tau keeps `oldText`/`newText` in camelCase even in Python. Tool schemas are part of
the *model-facing* contract, so they follow the wire format rather than the host language's
conventions. Small detail, right instinct — renaming them to `old_text` would change the
prompt surface for no benefit.

### Genuinely different

**1. Tau has no dedicated search tools at all; Pi's app has three.** Pi's `coding-agent` adds
`grep.ts` (385), `find.ts` (374), `ls.ts` (225). Tau's model uses `bash` for all of it. Whether
that matters is an empirical question — a dedicated grep can bound output and skip
`node_modules`, but `bash` + `rg` is what an experienced developer would type anyway.

**2. Pi has a two-tier toolset; Tau has one.** Pi's portable `harness/tools/` are minimal and
policy-free; `coding-agent/core/tools/` are the 2–7× larger policy-bearing versions. Tau's
`tau_coding/tools.py` (1,202 lines) is the only tier — it sits at the app layer and there's no
policy-free variant beneath it.

Pi's split is better for *reuse* (a non-coding agent can take `harness/tools/` and add its own
policy) and worse for *comprehension* (two `bash` tools, and you must know which one runs).

**3. `output-accumulator.ts` (222) and `truncate.ts` (276) are separate modules in Pi's app** —
~500 lines purely for managing tool output volume. That's a real signal about where effort
goes: output management is a subsystem, not a helper.

### Answering the plan's questions, for this layer

**"The standard coding toolset — and why does each tool exist separately?"** (a Stage 2
deliverable, answered here):

| Tool | Why not just `bash`? |
|---|---|
| `read` | Needs line ranges, byte/line budgets, and image decoding into `ImageContent`. `cat` gives you none of that and floods context. |
| `write` | Must be atomic and lock per path. `echo > file` can't be serialized against a concurrent `edit`. |
| `edit` | Exact `oldText` matching with a loud failure, plus diff rendering. `sed` fails silently or corrupts. |
| `bash` | The escape hatch. Everything not worth a dedicated tool — builds, tests, git, package managers. |
| `grep`/`find`/`ls` *(app only)* | Bounded, formatted output with sensible ignores. Optimizations over `bash`, not necessities — Tau omits all three. |

The pattern: **a tool earns its own existence when it needs a budget, a lock, or a
model-legible failure mode.** Otherwise `bash` covers it.

---

## 6. If I rebuilt this layer from scratch

**Tier 1 — three tools.** `read_file`, `write_file`, `run_shell`. Skip `edit` — write-whole-file
is fine for small files and lets you get the loop working. Already:

1. **Truncate everything**, tail-biased, with the limit in the tool description.
2. **Say what was truncated** — limit hit, absolute line numbers.

**Tier 2 — where the real work is.**

3. **`edit` with exact `oldText` matching.** Never line numbers. Fail loudly when the match
   isn't unique or isn't found.
4. **Spill full output to a temp file and give the model the path**, plus the recovery command
   (Tau's touch). This is the highest-value hour in Tier 2 — it converts truncation from data
   loss into pagination.
5. **Per-path async lock** shared by `write` and `edit`, keyed on the *resolved* path. Do it now
   even if your loop is sequential.
6. **Throttle `on_update` to ~100 ms.**
7. **Path confinement**, as a `_validate_path` function every filesystem tool calls: resolve,
   then verify the result is under the working directory. This is the seam that stops
   `../../../.ssh/id_rsa`, and it belongs in one place.
8. **A `prepare`-style hook on the shell tool** so sandboxing can be added later without
   touching the tool.
9. **Errors carry the captured output.** `appendStatus` is the pattern.

**Tier 3.**

10. **`grep`/`find`/`ls`** — only once you've watched the model waste turns on unbounded `bash`
    search. Let the need prove itself.
11. **Diff rendering** for edits (`edit-diff` is 500+ lines for a reason — budget for it).
12. **Image support** in `read`, with a byte ceiling.

**Don't build:** a todo/planning tool. Neither reference has one, and Pi deliberately made plan
mode an extension. If you want it later, make it an extension too.

**The test that proves this layer:** ask the agent to find and fix a bug in a file that's
5,000 lines long, with a test suite that prints 20,000 lines on failure. If truncation, paging,
and error-output-carrying all work, it succeeds. If any one is missing, it loops blindly.

---

## Files read for this layer

| File | Lines | Role |
|---|---|---|
| `pi/packages/agent/src/harness/tools/bash.ts` | 161 | traced in full, §3 |
| `pi/packages/agent/src/harness/tools/index.ts` | 23 | the exported four |
| `tau/src/tau_coding/tools.py` | 1,202 (targeted) | schemas, truncation, locks, confinement |

Surveyed by size and name, not read line-by-line: `coding-agent/src/core/tools/*` (4,109 across
15 files), `harness/tools/{read,write,edit,edit-diff,image,file-mutation-queue,path-utils}.ts`
(1,006).

Verification greps run: complete tool-name inventory in both repos; presence/absence of
`glob`/`grep` tools; `plan`-related hits in `cli/args.ts`.

---

*Next: `03b-context-sessions-compaction.md` — system-prompt assembly, session persistence,
compaction, and the prompt-caching mechanics that Layers 1 and 2 both deferred.*
