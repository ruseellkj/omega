---
name: explain-in-depth
description: Answer a conceptual question about this codebase — how something works, why it is built that way, how it compares to Pi/Tau/Claude Code — at full depth on the first attempt. Use whenever Rushil asks "how does X work", "what is Y", "why is it done this way", or asks several numbered questions at once.
---

# Explain in depth, first time

This skill exists because of a real failure. Rushil asked one question about session
persistence. It took **five rounds** before he understood it — not because the topic was
hard, but because each answer described *what things were* instead of *how they worked*,
used four terms interchangeably without defining any of them, and contradicted itself
across two paragraphs.

Every rule below comes from a specific mistake made in that exchange. Follow them and the
first answer lands.

---

## Rule 1 · Restate the question. Never answer a number.

Write the question out before answering it:

```markdown
# Q3 · Where do temp files live, and is the cost tracking real?

> *What about cross-session persistence? Where are temp files stored per session?
> How do I restart a session? Is cost an actual implementation or just shown on
> the terminal?*
```

**Why:** he asks 5–7 questions in one message. "Q3" means nothing when scrolling back a day
later, and when an answer drifts off-target he cannot tell *which* part it missed. The
restated question is the anchor that makes a wrong answer diagnosable.

Quote his own words in the blockquote. Do not tidy them.

---

## Rule 2 · Answer *how*, not *what*. The mechanism is the answer.

A description is not an explanation.

| Not this | This |
|---|---|
| "messages are saved to a JSONL file" | the `_persisted` counter, the `while` loop that closes the gap, when `_flush()` is called, and why it is called *then* |
| "the shell isn't fenced" | `cwd=base` sets the starting directory; `cd ..` leaves it; the fence function is never called from `run_shell` |
| "retry uses backoff" | 0.5 → 1 → 2s, ceiling 8s, three attempts, and the `emitted == 0` rule forbidding retry of a stream that already printed |

**Show the actual code.** Five lines with the real variable names beat a paragraph of prose:

```python
def _flush(self) -> None:
    while self._persisted < len(self.messages):
        self.store.append(self.session_id, self.messages[self._persisted])
        self._persisted += 1
```

Cite `file.py:line`. He reads the file afterwards; the citation is how he gets there.

---

## Rule 3 · Name what *kind* of thing each term is, the first time

The five-round failure was caused by using four words as if they were two:

| Word | Kind of thing |
|---|---|
| memory | a **place** — RAM |
| session | a **place** — a file on disk |
| JSONL | a **format** — how text is arranged in that file |
| append-only | a **rule** — how the file may be written |

Once that table existed, the confusion evaporated. It should have been in the first answer.

Before using a term, ask: *is this a place, a format, a rule, a command, a file, or a
concept?* Say which. Two terms of different kinds are never in competition, and saying so
removes the question before it is asked.

---

## Rule 4 · Trace a concrete example, step by step, with real values

Not "it appends as it goes". This:

```
                                 messages                  _persisted   file
you type "read loop.py"          [User]                    0            (none)
  _flush() → appends User        [User]                    1            1 line
model asks for a tool            [User, Asst]              1
  _flush() → appends Asst        [User, Asst]              2            2 lines
tool returns                     [User, Asst, Result]      2
  _flush() → appends Result      [User, Asst, Result]      3            3 lines
```

Then state the property the trace proves: *at any instant, at most one message is in RAM
but not on disk.*

**Show real file contents**, not a description of them:

```
{"kind":"header","version":1,"session_id":"20260903T142211-a3f9c1","model":"claude-sonnet-5"}
{"kind":"entry","version":1,"id":"4b1c2d3e4f5a","parent_id":null,"message":{"role":"user",...}}
```

---

## Rule 5 · Run it. Paste the output.

This project's convention is measurement over assertion, and it has caught real bugs:

- claiming always-allow could not break the fence — **disproved by running it**
- claiming redaction covered failures — **disproved by running it**
- claiming outside-root grants were safe — **a recursive-grant bug found by running it**

Before asserting behaviour, write a ten-line script that exercises it and paste the result.
If the output contradicts the explanation, the explanation was wrong.

---

## Rule 6 · Never introduce a name that does not exist without saying so

Two invented names (`before_record`, a hook; "Tier 2.5", a tier) sat beside six real hook
names and two real tier names in the same answer, and cost an entire round of confusion.

If naming something hypothetical, mark it in the same sentence: *"a hook that does not
exist — call it X for now"*. Better: describe it without naming it at all.

Before citing any symbol, flag, or file: **grep for it.** If it is not there, say so.

---

## Rule 7 · Reconcile apparent contradictions in the same breath

Two true statements written near each other can read as a contradiction:

> "The agent only ever reads memory."
> "How does the RAM list get copied to disk, and back?"

Both true — one about *during the run*, one about *at startup*. Unsaid, it looks like an
error, and he has to ask.

When two statements could clash, name the dimension that separates them **immediately**:
*write-only during the run, read-once at startup.*

---

## Rule 8 · Say what it is NOT, where a wrong model is likely

Cheap, and prevents whole rounds:

- "**cross-session does not mean sessions share context.** None of the four merges
  conversations."
- "**there is no 'context too hard, delegate' detector.** A subagent is spawned by an
  ordinary tool call."
- "**location-checking only changes the answer for `read_file`.** Writes and shell commands
  are asked about everywhere."

---

## Rule 9 · Do not invent hypotheticals that cannot happen

A table comparing "JSONL without `--resume`" against "`--resume` without JSONL" was
nonsense: neither can occur. It taught nothing and cost a round.

Compare things that **actually differ** — omega vs Pi vs Tau vs Claude Code, before vs after
a change, the success path vs the failure path. If a cell describes an impossible world,
delete the table.

---

## Rule 10 · Explain jargon with mechanics, not more jargon

"Retry happens below the event boundary" means nothing on first reading.

Say instead: *the loop never learns a request was attempted three times — it sees one
stream. That is what lets retry be replaced without touching anything above the adapter.*

Pattern: **term → what physically happens → why that matters.**

---

## Rule 11 · Compare against the references, with evidence

`research/pi/` and `research/tau/` are on disk. Read them — never recall them.

| | Evidence |
|---|---|
| Pi | file and line from `research/pi/` |
| Tau | file and line from `research/tau/` |
| Claude Code | **closed source** — its docs, or "unknown", never a guess |

Being unable to verify Claude Code is a fact worth stating plainly. Say "I cannot read it,
so I will not guess" rather than producing a plausible row.

---

## Rule 12 · Length is not the problem. Repetition is.

He has said both *"don't overexplain"* and *"these answers are too creamy"*. Not a
contradiction:

- **Wanted:** depth — mechanism, code, trace, measured output
- **Not wanted:** the same point restated three ways, hedging, throat-clearing

Every paragraph must add a fact. If it only rephrases the one above, cut it.

---

## The shape of a good answer

```markdown
# Q1 · <the question, in his words>

> *<verbatim quote>*

## Step 1 — <the distinction that unlocks it>
   a table naming what kind of thing each term is

## Step 2 — <the mechanism>
   real code, real variable names, file:line

## Step 3 — <traced through one concrete case>
   a table of states, or measured output

## Step 4 — <what it is NOT>

## Step 5 — <how the references differ>
   evidence per row; "unknown" where it is unknown
```

---

## Before sending, check

- [ ] Every question restated in his words, not as a number
- [ ] Every term's *kind* named the first time it appears
- [ ] At least one real code excerpt with `file:line`
- [ ] At least one concrete trace or measured output
- [ ] Every symbol, flag and file verified to exist
- [ ] Anything invented explicitly flagged as invented
- [ ] No hypothetical that cannot occur
- [ ] Claude Code claims marked as docs or unknown
- [ ] No paragraph that only restates the previous one
