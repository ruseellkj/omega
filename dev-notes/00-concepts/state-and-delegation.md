# State and delegation

Where a coding agent keeps things, and how it hands work to another agent.

Seven questions, answered against source. Everything about omega, Pi and Tau below
was read out of their code — file and line given. Claude Code is closed source, so
its rows come from its published documentation, and the difference is marked
wherever it matters.

Companion to `01-teardown/03b-context-sessions-compaction.md`, which covers the
session format in more depth. This note is about the comparison.

---

## 1 · Persistence: append-only JSONL vs `--resume`

**These are two different things, and the confusion is fair — they are always
mentioned together.**

| | What it is | When it happens |
|---|---|---|
| **append-only JSONL** | the *writing* mechanism | continuously, while you work |
| **`--resume`** | the *reading* command | once, when you start omega |

One is the notebook. The other is opening the notebook again.

### The writing half

Every message is appended to a file as one line of JSON, the moment it exists.
`<root>/.omega/sessions/<id>.jsonl` — beside the project, so a transcript travels
with the work it describes (`session/store.py:11`).

A session file, abbreviated:

```
{"kind":"header","session_id":"20260903T142211-a3f9c1","created_at":"...","model":"..."}
{"kind":"entry","id":"4b1c2d3e4f5a","parent_id":null,"message":{"role":"user",...}}
{"kind":"entry","id":"7c8d9e0f1a2b","parent_id":"4b1c2d3e4f5a","message":{"role":"assistant",...}}
{"kind":"entry","id":"9f0a1b2c3d4e","parent_id":"7c8d9e0f1a2b","message":{"role":"toolResult",...}}
```

**"Append-only" means nothing is ever rewritten.** New lines go on the end; old
lines are never touched. That is the whole safety property:

- **Crash mid-write** → you lose *at most the last line*. Everything before it is
  intact and readable.
- **The alternative** — hold the conversation in memory and rewrite the whole file
  each turn — means a crash during the write can lose *everything*, because the
  old file has already been truncated.

The reader is built for that: `read_records` survives a half-written last line
rather than refusing the file (`session/jsonl.py`).

`parent_id` on each entry points at the line before it. Nothing branches at
Tier 2 — it is there so branching at Tier 3 is an addition rather than a format
change.

### The reading half

Two flags, and the split matters:

| Command | What it finds |
|---|---|
| `omega --resume` | the **most recent** session in this directory |
| `omega --session <id>` | **that specific** session |

`--resume` picks by newest modification time, in nanoseconds — because two
sessions started in the same second are common, and a whole-second comparison
would choose arbitrarily (`session/store.py:99`).

**This is the same split Claude Code makes**, under different names: its
`--continue` is omega's `--resume` (most recent here), and its `--resume <id>` is
omega's `--session <id>` (a named one).

### Worked example

```bash
$ omega                       # session A starts; every message appended as it happens
You: refactor the loop
...twelve turns of work...
$ ^C                          # terminal closed, lid shut, crash — same outcome

$ omega --resume              # reads A's file back, replays it into the transcript
Resumed 24 messages.
You: now add a test for it    # the model still knows what "it" is
```

Without the JSONL there is nothing to resume from. Without `--resume` the file
exists and nobody reads it. **You need both, and they are not the same feature.**

One extra step happens on resume: `harness.repair_orphans()` fills any tool call
that never got a result, because a provider rejects an unanswered tool call on
*every future request* — see `01-teardown/02-agent-loop-tools.md` §2.5.

---

## 2 · Retry and backoff — failure #5

### What "the API says slow down" means

It is **HTTP 429, "Too Many Requests"**. A rate limit. The server is not broken
and your request is not wrong — you are sending faster than your account is
allowed, so the server declines this one and expects you to try again shortly.

**Yes, the fix is retry:** wait, then send the *same* request again.

### Backoff

"Backoff" is waiting *longer each time* instead of hammering. omega's numbers
(`omega_ai/retry.py`):

| Attempt | Wait before it |
|---|---|
| 1st retry | 0.5s |
| 2nd retry | 1.0s |
| 3rd retry | 2.0s |
| ceiling | 8.0s |

Three attempts, then give up. Deliberately small: **a human is waiting.** Ninety
seconds of silent backoff is worse than a clear failure they can react to.

No jitter, either — jitter exists to stop a *fleet* of clients retrying in
lockstep, and omega is one client. A deterministic delay is also one less thing
making a test flaky.

**The server's own advice wins.** If the response carries a `Retry-After` header,
that is used instead of the curve, capped at the ceiling. The service knows when
it will be ready; we are guessing.

### Which failures are retried

```python
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}   # "not now"
_FATAL_STATUS     = {400, 401, 403, 404, 413, 422}             # "not ever"
```

A 429 or 503 means *not now*. A 400 means *that request is malformed* — sending it
again wastes time and hides the bug. `ConnectionError` and `TimeoutError` are
retried; `asyncio.CancelledError` never is, because the user asked to stop.

### Where it sits, and why that is the interesting part

**Below the event boundary.** The loop never learns a request was attempted three
times. That is what lets retry be added, tuned or replaced without anything above
`omega_ai` changing.

**One hard constraint:** a stream can only be retried *before it has emitted
anything*. Once 500 tokens have gone upward, restarting would produce them twice.
So the adapter counts what it has yielded and stops being allowed to retry after
the first event; a later failure is reported as an error event carrying the
partial content.

---

## 3 · Where state actually lives

Two kinds of state, and they behave differently.

### The conversation

| | In memory | On disk |
|---|---|---|
| **omega** | `harness` message list | `<project>/.omega/sessions/<id>.jsonl` |
| **Pi** | agent state | `~/.pi/agent/sessions/--Users-me-project--/<ts>_<id>.jsonl` |
| **Tau** | session object | `~/.tau/sessions/<slug>-<sha6>/<id>.jsonl` |
| **Claude Code** | (not inspectable) | `~/.claude/projects/<encoded-cwd>/*.jsonl` |

**All four converged on the same design**: encode the project path into a
directory name, then one append-only JSONL file per session inside it. Nobody
chose a database for the CLI.

Pi *does* ship a SQLite session store — but in `packages/storage/sqlite-node`, for
its server. `grep -rn "sqlite" pi/packages/coding-agent/src/` returns **nothing**:
the CLI uses JSONL like the rest. (omega's `session/store.py:6` says "Pi
eventually moves to SQLite for cross-session search" — fair as a direction, but
the CLI has not.)

One structural difference: **omega stores sessions inside the project**
(`.omega/`); the other three store them in the home directory, keyed by project
path. omega's way means the transcript travels with a copied repo; theirs means a
`git clean` cannot delete your history. Both defensible.

### Approvals — and this one is a real difference

| | Where "always allow" is remembered | Survives restart? |
|---|---|---|
| **omega** | `set()` in RAM (`approval.py`) | **no** |
| **Pi** | permission gate is an *example extension* | n/a in core |
| **Tau** | permission gate is an *example extension* | n/a in core |
| **Claude Code** | settings files on disk | **yes** |

This is why Claude Code asks once and omega asks again every session — and it is
the reason omega originally needed a path fence at all. See
`00-concepts/security.md`.

### The files that hold state in omega

| File | Holds |
|---|---|
| `omega_agent/harness.py` | the live transcript, queues, cancellation |
| `omega_agent/session/store.py` | where sessions live, behind a `Protocol` |
| `omega_agent/session/jsonl.py` | append-only write, crash-tolerant read |
| `omega_agent/session/entries.py` | one line's shape, including `parent_id` |
| `omega_coding/approval.py` | always-allow sets — RAM only |
| `omega_coding/cost.py` | token and cost totals for the run |
| `omega_coding/context.py` | how full the context window is |

---

## 4 · Cross-session, temp files, restarting, and cost

### Temp files

Two different things get written outside the project:

| What | Where | Cleaned up? |
|---|---|---|
| session transcripts | `<project>/.omega/sessions/` | no — that is the point |
| truncation spill files | system temp, `omega-*.txt` | **no** |

The spill files hold the full output of a command whose result was truncated, so
the model can read past the budget (`truncate.py`). On macOS they land in
`/var/folders/.../T/`. **Nothing deletes them** — a known rough edge, and the
reason `_spill` now masks credentials before writing.

### Restarting

```bash
omega --resume              # most recent session in this directory
omega --session <id>        # a specific one
omega --no-save             # write nothing at all
```

### Cost and tokens — real, not decorative

**The counting is real.** `CostTracker` sums `usage.input` and `usage.output` from
what the provider actually reports on every response (`cost.py:64`). Those are
true token counts, not estimates.

**The dollar figure is deliberately absent by default.** omega ships **no price
table**, because a hardcoded dollars-per-million-tokens table is wrong the moment
prices change or a model is renamed — and a *confidently wrong* cost figure is
worse than none, because it gets believed and budgets get planned on it. Dollars
appear only if `OMEGA_PRICE_INPUT` and `OMEGA_PRICE_OUTPUT` are set. Tau's
provider catalog is the real answer, and it is Tau-parity work.

**The context gauge is an estimate** — characters ÷ 4 (`context.py`). It measures
a problem it does not fix, and never claims precision.

### So what is actually unbuilt

| Missing | Why it matters |
|---|---|
| **compaction** — failure #1 | long tasks still die at the context limit; omega only *shows* the wall approaching |
| **prompt caching** — failure #9 | every turn re-bills the system prompt and the whole tool schema block |

Both are Tier 3, and both plug into seams that already exist:
`transform_context` for compaction, and a byte-stable prompt prefix for caching —
which is why the system prompt is built once at startup.

---

## 5 · File locking — failure #8

### The problem

Two edits to one file, at the same time:

```
edit A reads  "hello world"
edit B reads  "hello world"        ← both hold the original
edit A writes "HELLO world"
edit B writes "hello WORLD"        ← A's change is gone
```

No error. No warning. **The work just disappears**, which is the worst kind of
bug: nothing downstream can detect it.

### The fix, and who has it

One lock per file. The second writer waits.

| | Locks? | Keyed on | Covers | Shell locked? | Verified at |
|---|---|---|---|---|---|
| **omega** | yes | resolved path | `write_file`, `edit_file` | **no** | `file_lock.py`; `builtin_tools.py:239,270` |
| **Pi** | yes | absolute path | `write.ts`, `edit.ts` | **no** | `withFileMutationQueue`; `bash.ts` has 0 occurrences |
| **Tau** | yes | resolved path | its write/edit path | **no** | `tools.py:137`, `:1189`, `:1192` |
| **Claude Code** | unknown | — | — | — | closed source; not inspectable |

**All three references lock, all three key on the *resolved* path, and none of
them lock the shell.** That is not coincidence — it is the same problem with the
same answer.

Keying on the resolved path matters: `notes.txt`, `./notes.txt` and a symlink
`alias.txt` are one file. Handing out three locks for one file is
indistinguishable from having none.

### But none of them run tools in parallel

`loop.py:144`:

```python
for tool_request in tool_requests:
    await execute_tool_call(...)
```

`for` plus `await` is strictly sequential. Tau runs tools one at a time too, and
keeps its lock anyway.

**So the lock does nothing today.** It is insurance for the day tools run
concurrently — the judgement omega copied from Tau — and the cost is a dictionary
lookup.

### The part that cannot be fixed

If tools ever do run concurrently:

- `write_file` vs `edit_file` → **safe**, they share the lock
- `echo x > f.txt` vs `edit_file` → **unsafe, permanently**

To lock a file you must know *which* file. For `edit_file` it is an argument. For a
shell command you would have to parse and understand the command to work out which
paths it touches — through pipes, variables and subshells. That is not solvable in
general, which is why **the shell is the unfixable side of this race in all three
implementations.**

---

## 6 · How context reaches a second provider

### The whole conversation, every single turn

Chat APIs are **stateless**. The provider remembers nothing between calls. So every
turn sends the entire history:

```
turn 1  →  [system, user]
turn 2  →  [system, user, assistant, toolResult]
turn 3  →  [system, user, assistant, toolResult, assistant, toolResult]
...
```

`loop.py` passes `messages=context` — the full list — on every request. Not a
window, not the last N. **This is why the context limit is a real ceiling, and why
compaction is failure #1**: the conversation only grows, and eventually one more
turn does not fit.

### What gets filtered

One hook trims the list on its way out: `convert_to_llm`, filled by
`drop_empty_failed_turns` (`history.py`). It removes assistant turns that failed
before producing anything — those belong in the *transcript* (you asked something
and it broke; hiding that makes the session a lie) but sending them back is at best
noise and at worst a rejected request.

**Two views of history, deliberately**: what is kept, and what is sent. Tier 1
conflated them, so anything a provider could not use had to be either kept and
sent, or thrown away entirely.

### Why the provider swap was cheap

Switching from Anthropic to OpenAI changed **the shape of the wire format, not the
contents of the conversation.** Same messages, same order, same completeness —
translated into a different JSON layout inside `omega_ai/openai.py`. Nothing above
that file knew a second format existed, which is the claim Tier 2 measured rather
than asserted.

---

## 7 · Subagents

### The short version

| | Subagents? | Where |
|---|---|---|
| **Pi** | yes, as a shipped **example extension** | `examples/extensions/subagent/` (1,015 lines) |
| **Tau** | **not in core** | third-party: `rian-dolphin/tau-subagents` |
| **Claude Code** | **yes, built in** | the `Agent` tool + `.claude/agents/*.md` |
| **omega** | no | Tier 3; `headless.py` is the seam |

### How the decision to spawn one is made

**There is no cleverness here, and this is the part worth internalising.**

A subagent is invoked through **an ordinary tool call.** The model sees a tool
called `agent` (or `Task`, or `subagent`) in its tool list, alongside `read_file`
and `run_shell`, with a description saying what it is for. When the model judges
the task fits, it calls it — exactly as it calls any other tool.

There is **no "is this context too hard?" detector.** No heuristic measures
difficulty and decides to delegate. The model reads a tool description and picks —
the same mechanism that decides between `read_file` and `run_shell`.

What *does* shape the decision is the same three things as any tool: the tool's
**name**, its **description**, and the **system prompt** steering when to prefer
it. That is the whole trigger mechanism.

### Pi's architecture — a separate OS process

From `examples/extensions/subagent/index.ts`:

```typescript
import { spawn } from "node:child_process";
...
if (agent.model) args.push("--model", agent.model);
if (agent.tools?.length) args.push("--tools", agent.tools.join(","));
args.push("--append-system-prompt", tmpPromptPath);
args.push(`Task: ${task}`);
spawn(invocation.command, invocation.args, { stdio: ["ignore", "pipe", "pipe"] });
```

**It runs `pi` again, as a child process.** Which gives the isolation for free — a
separate process has a separate context window by construction.

**What crosses the boundary is deliberately tiny:**

| Direction | What travels |
|---|---|
| parent → child | the **task string**, a system prompt file, a model name, a tool list |
| child → parent | the child's **final output**, capped at 50 KB per task |

**The parent's conversation is never sent.** The child starts empty and is told one
thing: *"Task: find all the authentication code."* It cannot see what you and the
parent have been discussing.

That is the entire point. The parent's context stays small because the child's
forty tool calls happen somewhere else, and only the conclusion comes back.

**Agent definitions** are markdown with YAML frontmatter:

```markdown
---
name: scout
description: Fast codebase recon, returns compressed context
tools: read, grep, find, ls, bash
model: claude-haiku-4-5
---
System prompt for the agent goes here.
```

Loaded from `~/.pi/agent/agents/*.md` (user level, always) and `.pi/agents/*.md`
(project level, **opt-in only**). The default `agentScope` is `"user"` — because a
project-local agent file is repo-controlled text that can instruct a model to read
files and run commands, so a cloned repo must not be able to introduce one
silently. Interactive runs also confirm before running a project agent.

**Three invocation modes:**

| Mode | Arguments | Behaviour |
|---|---|---|
| single | `{agent, task}` | one agent, one task |
| parallel | `{tasks: [...]}` | concurrent, max 8 tasks, 4 at a time |
| chain | `{chain: [...]}` | sequential; `{previous}` interpolates the prior output |

Chain mode's `{previous}` placeholder is how context moves *between* children: the
parent splices one child's output into the next child's task string. Still just a
string — there is no shared conversation.

Pi also ships workflow presets as prompt templates: `/implement` runs
scout → planner → worker.

### Tau — not in core, and a different choice

Tau's own docs point at `rian-dolphin/tau-subagents`, which ports
`tintinweb/pi-subagents`. The notable difference from Pi's example: it spawns
subagents **in-process**, not as child processes, with their own tools and system
prompts. It adds foreground/background modes, `get_subagent_result` and
`steer_subagent` tools, agent types in `.tau/agents/*.md`, and an `/agents`
command.

In-process is cheaper to start and lets the parent *steer* a running child. A child
process is more thoroughly isolated and dies cleanly on Ctrl-C. Reasonable people
differ; both exist.

### Claude Code

Built in rather than an extension. Per its documentation: each subagent has its own
isolated context window, its own system prompt and a restricted tool set;
definitions live in `.claude/agents/`; the pattern is orchestrator-worker
("hub-and-spoke"), where a primary instance coordinates specialists. A subagent
receives only what is relevant to its task rather than the full dialogue, and
returns only its results rather than its whole context.

Same architecture as Pi's, in other words, with the delegation tool promoted from
example to core.

### omega

None, and that is the right call for Tier 2. The seam exists:
`omega_coding/headless.py` runs the agent with no keyboard, and **a subagent is
that function called from inside a tool.** Nothing in the loop or the provider
contract has to change to add it — which is the test every Tier 3 item has to pass.

---

## 8 · Multiple sessions and "cross-session"

### Creating and listing them

| | New session | List them | Switch |
|---|---|---|---|
| **omega** | every run without `--resume` | `ls .omega/sessions/` | `--session <id>` |
| **Pi** | every run | reads session file headers | resume by id |
| **Tau** | every run | `index.jsonl` per project + global | `list_sessions(cwd)` |
| **Claude Code** | every run | built-in picker | `--resume <id>` |

Pi and Tau both maintain an **index file** so listing does not mean parsing every
transcript. omega does not — `latest_session_id()` just takes the newest mtime,
which is enough for one flag and would not be enough for a picker UI.

### How the project is keyed

All four hash or slugify the working directory:

```
omega        <project>/.omega/sessions/20260903T142211-a3f9c1.jsonl
Pi           ~/.pi/agent/sessions/--Users-me-code-project--/<ts>_<id>.jsonl
Tau          ~/.tau/sessions/<slug>-<sha256[:6]>/<id>.jsonl
Claude Code  ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
```

Tau's is the most careful: a readable slug *plus* six hex of a SHA-256 of the
resolved path, so two projects with the same folder name cannot collide.

### "Cross-session" — what it does and does not mean

**It does not mean sessions share context.** None of the four merges two
conversations, and none lets a session read another's history mid-run. Each session
is a closed transcript.

What the term actually covers is two narrower things:

**1 · Resume a specific session from anywhere.** Claude Code's `--resume <id>`
searches beyond the current project directory, so you can pick up a session started
elsewhere. omega's `--session <id>` only looks in the current project's `.omega/`,
because that is where it writes.

**2 · Move a session between machines.** Because the transcript is a plain JSONL
file, copying the file *is* the migration. Claude Code documents both routes: copy
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` onto the new host, or attach
a `sessionStore` adapter so the SDK mirrors transcripts to your own backend and
another host resumes from there.

**That is the whole trick, and it is why the format matters more than the feature.**
An append-only file of self-contained JSON lines is portable, greppable, diffable
and mergeable by hand. Any of these tools could add a session picker, a search index
or cross-host sync without changing what a session *is*. A session in a proprietary
binary format could not.

`parent_id` is the next step on the same road: once every entry names its
predecessor, a transcript stops being a list and becomes a tree — and *that* is what
makes branching (rewind, fork, try another approach) possible. omega writes it at
Tier 2 and does nothing with it, precisely so Tier 3 does not need a format change.

---

## Sources

Code read directly: `omega/src/**`, `research/pi/**`, `research/tau/**` — file and
line cited inline throughout.

Claude Code behaviour, from its documentation:

- [Create custom subagents](https://docs.anthropic.com/en/docs/claude-code/sub-agents)
- [Subagents in the SDK](https://docs.claude.com/en/docs/agent-sdk/subagents)
- [Work with sessions](https://docs.claude.com/en/docs/claude-code/sdk/sdk-sessions)
- [Session management](https://docs.claude.com/en/docs/agent-sdk/sessions)
- [Building agents with the Claude Agent SDK](https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk)
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

Third-party subagent implementations referenced by Tau's own docs:
[rian-dolphin/tau-subagents](https://github.com/rian-dolphin/tau-subagents),
[tintinweb/pi-subagents](https://github.com/tintinweb/pi-subagents).
