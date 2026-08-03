# The Beginner Architecture — and Exactly How It Breaks

The smallest thing that genuinely works, followed by an honest account of everywhere it falls
apart. Both halves matter: the code shows the idea is simple; the failures show why real ones
aren't.

---

## The whole thing, in one file

About 70 lines of Python. One provider, one loop, three tools, no persistence, no UI.

```python
# naive_agent.py — the smallest coding agent that actually works.
import json, os, subprocess, sys
from anthropic import Anthropic

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

TOOLS = [
    {"name": "read_file",
     "description": "Read a text file and return its contents.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
    {"name": "write_file",
     "description": "Write text to a file, replacing what was there.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["path", "content"]}},
    {"name": "run_shell",
     "description": "Run a shell command and return its output.",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
]

def run_tool(name, args):
    """Do the thing the model asked for. Return text describing what happened."""
    try:
        if name == "read_file":
            return open(args["path"]).read()
        if name == "write_file":
            open(args["path"], "w").write(args["content"])
            return f"Wrote {len(args['content'])} chars to {args['path']}"
        if name == "run_shell":
            done = subprocess.run(args["command"], shell=True,
                                  capture_output=True, text=True)
            return (done.stdout + done.stderr) or "(no output)"
        return f"Unknown tool: {name}"
    except Exception as exc:
        return f"Error: {exc}"

def agent(task: str) -> None:
    messages = [{"role": "user", "content": task}]

    while True:                                        # ← the agent loop
        reply = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=4096,
            system="You are a coding assistant. Use the tools to inspect and edit files.",
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": reply.content})

        # Print any text the model produced.
        for block in reply.content:
            if block.type == "text":
                print(block.text)

        # Find the tool requests.
        calls = [b for b in reply.content if b.type == "tool_use"]
        if not calls:                                  # ← the stop condition
            return

        results = []
        for call in calls:
            print(f"  → {call.name}({json.dumps(call.input)[:80]})")
            output = run_tool(call.name, call.input)
            results.append({"type": "tool_result",
                            "tool_use_id": call.id,
                            "content": output})
        messages.append({"role": "user", "content": results})

if __name__ == "__main__":
    agent(" ".join(sys.argv[1:]))
```

Run it:

```bash
uv run naive_agent.py "fix the typo in README.md"
```

**It works.** It will read the file, spot the typo, write the correction, and stop. That is a real
coding agent, and it's worth building this before anything else — it makes the loop concrete in a
way that reading about it doesn't.

## The three things it gets right

Worth naming, because the production version keeps all three:

1. **The loop's stop condition is `if not calls`** — no tool requests means done. Real agents use
   exactly this rule.
2. **Tool errors become text, not exceptions.** `run_tool` catches everything and returns
   `f"Error: {exc}"`, so the model reads the failure and can react. Crash instead and the run
   ends.
3. **The full conversation is re-sent every iteration.** `messages` only grows. That's not
   laziness — it's how the API works, and understanding it explains most later design decisions.

---

## Now: exactly how it breaks

Nine failures, in the order you'll actually hit them.

### 1. It fills up and dies

Ask for something real — "add tests for the auth module" — and after 15 or 20 round trips the API
returns an error: too many input tokens.

**Why.** `messages` never shrinks, and every entry is re-sent. File contents accumulate. Command
output accumulates.

**What's needed.** Measure the size before each request; when it approaches the limit, summarize
the old portion and keep the recent portion.
→ `../00-concepts/anatomy.md` #22–23

### 2. One big file ends the session

```
uv run naive_agent.py "why is the build failing"
```

The model runs your build. It prints 40,000 lines. `run_tool` returns all of them, they go into
`messages`, and you are instantly at the context limit — from a single tool call.

**Why.** No output limit anywhere.

**What's needed.** Cap tool output (both references use ~2,000 lines / 50 KB), keep the *tail*
because errors are at the end, write the full text to a temp file, and tell the model the path so
it can page through.
→ `../01-teardown/03-coding-tools.md` §3.4

### 3. Ctrl-C bricks the conversation

Press Ctrl-C while a command is running. The process dies. Fine — except if you'd been saving
`messages` anywhere and tried to resume, **every future request would fail permanently.**

**Why.** The last assistant message contains a `tool_use` block with no matching `tool_result`.
Providers reject that outright. The conversation is now invalid forever.

**What's needed.** A cancellation signal rather than process death, and on interrupt, scan for
tool calls with no result and synthesize one saying it was interrupted.
→ `../01-teardown/02-agent-loop-tools.md` §2.5

This is the single nastiest bug in the list, because it's silent until you add persistence, and
then it looks like data corruption.

### 4. `run_shell` will eventually delete something you wanted

There is no confirmation and no restriction. `run_shell("rm -rf build")` executes. If the model
misreads the working directory, that's your source tree.

**Why.** No approval gate. No path confinement. No sandbox.

**What's needed.** A hook consulted before every tool call that can block it, path validation for
file tools, and — most valuable — a git commit before each run so mistakes are reversible.
→ `../00-concepts/security.md` §3–5

### 5. A rate limit ends the run

One HTTP 429 and `client.messages.create` raises. The whole task is lost, including 20 round
trips of progress.

**Why.** No retry.

**What's needed.** Retry with backoff *inside* the provider layer, so the loop never sees it.
→ `../01-teardown/01-provider-stream.md` §3

### 6. Nothing appears until the model finishes

`client.messages.create` blocks. For a 30-second response you stare at nothing, then everything
appears at once.

**Why.** Not streaming.

**What's needed.** Stream the response and emit events as pieces arrive — which is also what makes
a real UI possible at all.
→ `../01-teardown/01-provider-stream.md` §2.2

### 7. Switching providers means a rewrite

Try to add OpenAI. You'll find `anthropic`-shaped assumptions in: the client call, `reply.content`,
`block.type == "tool_use"`, `tool_use_id`, `input_schema`, and the `{"role": "user", "content":
[tool_result...]}` convention. **Six places, spread across the file.**

**Why.** No provider abstraction. Vendor details leak into the loop.

**What's needed.** One neutral interface — messages in, a stream of neutral events out — with all
vendor translation behind it.
→ `../01-teardown/01-provider-stream.md` §2.1

This is the failure most worth feeling firsthand. Try the port, hit the six places, *then* read
why the abstraction is shaped the way it is.

### 8. Two edits to one file lose data

Ask for a change in two places at once. If you later make tool calls run in parallel, both read
the original file and both write — the second silently discards the first.

**Why.** No serialization.

**What's needed.** A lock per resolved file path, shared by write and edit. Both references have
one; Tau keeps it even though its loop is sequential.
→ `../01-teardown/03-coding-tools.md` §4.2

### 9. It costs more than it should

Every round trip re-bills the system prompt and the full tool schema block. Thirty round trips,
thirty payments for identical text.

**Why.** No prompt caching.

**What's needed.** Cache markers on a stable prefix — which imposes a real constraint: everything
early in the request must be byte-identical between calls.
→ `../01-teardown/03b-context-sessions-compaction.md` §5

### And two more, quietly

**No persistence.** Close the terminal, lose everything.
**No `max_turns`.** A confused model can loop until your budget is gone. One line, and it's the
first thing to add.

---

## What the failures tell you about the shape

Read the nine again and a pattern appears. Each fix wants to live in a *different* place:

| Failure | Where the fix belongs |
|---|---|
| provider lock-in (7) | below the loop |
| retries (5), streaming (6), caching (9) | below the loop |
| context size (1), interruption (3) | around the loop |
| approvals (4) | *called by* the loop |
| output limits (2), file locks (8) | inside the tools |

**Nothing on that list belongs *in* the loop.** That's the whole argument for layering — and it's
why the loop in a real implementation is still only ~300 lines. It didn't grow. The work went
around it.

The naive version has one layer, so every fix has to go in the same place. That's why it doesn't
scale: not because 70 lines is too few, but because there's nowhere to put anything.

---

## Should you build this first?

**Yes** — as a **Tier 0 spike**, and then throw most of it away.

Not Tier 1. Tier 1 is ~700 lines across eight files, *with* a provider interface, the 12 stream
events, output truncation, a turn limit, and an offline test double — see
`../06-product-roadmap.md` §"Tier 1" and `../04-folder-trees.md` §2. This file has none of those.
It's the warm-up that makes Tier 1's structure feel necessary rather than ceremonial.

What survives into Tier 1: the loop shape, the stop condition, errors-as-text. What gets replaced:
everything else.

The value isn't the code. It's that after hitting failures 1, 2, and 7 personally, the production
architecture stops looking over-engineered and starts looking like the minimum.

---

*Next: `03-production.md` — the same system with all nine fixed, and the technical vocabulary
for each.*
