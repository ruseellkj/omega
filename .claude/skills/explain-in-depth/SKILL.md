---
name: explain-in-depth
description: Explain something in this codebase so completely that the reader does not have to come back. Use when the question is conceptual rather than operational — how a thing works, what it is, why it is built that way, what goes wrong with it, how it compares to Pi/Tau/Claude Code — and especially when several questions arrive together, when the same ground is being covered a second time, or when the ask is to understand the whole shape at once rather than to get an instruction carried out. Prefer it whenever the honest answer is a mechanism, a derivation, or a trade-off rather than a fact.
---

# Explain in depth, first time

This skill exists because of a real failure. Rushil asked one question about session
persistence. It took **five rounds** before he understood it — not because the topic was
hard, but because each answer described *what things were* instead of *how they worked*,
used four terms interchangeably without defining any of them, and contradicted itself
across two paragraphs.

**The bar is a single reading.** Not "accurate", not "thorough" — an answer the reader
finishes without a follow-up question forming. If they have to come back to ask what a
word meant, why a step was necessary, or what happens in the other case, the answer
failed even if every sentence in it was true.

Rules 1–9 come from specific mistakes made in that exchange. Rules 10–13 are about the
part that turns a correct answer into a complete one: **not just where the answer landed,
but how anyone could have got there.**

---

## What a complete answer covers

Not a template to fill in order — a checklist of what must not be missing. A short
question needs three of these; a hard one needs all of them.

| | What it answers | Left out, the reader asks… |
|---|---|---|
| **What** | the thing itself, named precisely | "what *is* that?" |
| **Why** | what goes wrong without it | "why does this exist?" |
| **How** | the mechanism, in code | "but how does it actually do it?" |
| **Issue** | the failure it was built against | "when would this ever matter?" |
| **Solution** | what was done, and what it cost | "so what was the fix?" |
| **Perspective** | the framing that makes it obvious | "why is that the right way to see it?" |
| **Intuition** | how you could have guessed | "how would I ever have thought of that?" |
| **Arrival** | the steps from problem to answer | "how did you get from there to here?" |
| **Uniqueness** | what makes this one unusual | "isn't this just the normal thing?" |

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

## Three rules live in CLAUDE.md instead

**Verify before asserting · never invent a name · evidence for every reference claim.**

They were here first, then moved — because they are not about explaining. Each was broken while
*writing code*, when this skill was not loaded at all. `CLAUDE.md` loads in every session; a skill
loads only when its description matches what was asked. Rules that must always apply belong in the
file that is always read.

They apply to this skill's work too. They just do not live here.

---

## Rule 5 · Reconcile apparent contradictions in the same breath

Two true statements written near each other can read as a contradiction:

> "The agent only ever reads memory."
> "How does the RAM list get copied to disk, and back?"

Both true — one about *during the run*, one about *at startup*. Unsaid, it looks like an
error, and he has to ask.

When two statements could clash, name the dimension that separates them **immediately**:
*write-only during the run, read-once at startup.*

---

## Rule 6 · Say what it is NOT, where a wrong model is likely

Cheap, and prevents whole rounds:

- "**cross-session does not mean sessions share context.** None of the four merges
  conversations."
- "**there is no 'context too hard, delegate' detector.** A subagent is spawned by an
  ordinary tool call."
- "**location-checking only changes the answer for `read_file`.** Writes and shell commands
  are asked about everywhere."

---

## Rule 7 · Do not invent hypotheticals that cannot happen

A table comparing "JSONL without `--resume`" against "`--resume` without JSONL" was
nonsense: neither can occur. It taught nothing and cost a round.

Compare things that **actually differ** — omega vs Pi vs Tau vs Claude Code, before vs after
a change, the success path vs the failure path. If a cell describes an impossible world,
delete the table.

---

## Rule 8 · Explain jargon with mechanics, not more jargon

"Retry happens below the event boundary" means nothing on first reading.

Say instead: *the loop never learns a request was attempted three times — it sees one
stream. That is what lets retry be replaced without touching anything above the adapter.*

Pattern: **term → what physically happens → why that matters.**

---

## Rule 9 · Length is not the problem. Repetition is.

He has said both *"don't overexplain"* and *"these answers are too creamy"*. Not a
contradiction:

- **Wanted:** depth — mechanism, code, trace, measured output
- **Not wanted:** the same point restated three ways, hedging, throat-clearing

Every paragraph must add a fact. If it only rephrases the one above, cut it.

---

## Rule 10 · Lead with the perspective that makes the rest obvious

Most things here are confusing for one reason, and once that reason is named the detail
stops needing to be memorised. Find it and put it first.

| Topic | The sentence that unlocks it |
|---|---|
| the two views of history | *what you keep and what you send are two lists* |
| the nine failures | *every one is a correctness failure — nothing on the list is a feature* |
| layers vs tiers | *layers are a structure, tiers are milestones; only one of them can be "final"* |
| prompt caching | *the static prefix is the small part; the conversation is where the money is* |

**The test:** if the explanation that follows still needs the reader to hold five unrelated
facts in their head, the unlocking sentence has not been found yet. Keep looking before
writing.

---

## Rule 11 · Give the intuition — how someone could have guessed

The mechanism says what is true. The intuition says why it had to be. Without it an
explanation reads as trivia to memorise rather than something the reader could have
derived, and nothing transfers to the next problem.

Not this:

> Compaction cuts on turn boundaries.

This:

> An assistant message asking for a tool, and the result answering it, are **one unit** —
> a provider rejects a call with no result. So anything that removes messages has to cut
> *between* units, never inside one. Once you see them as units the rule is forced rather
> than chosen: there is nowhere else a cut can go.

The shape is: **the constraint → what it forces → why no alternative survives it.** When a
decision was genuinely free rather than forced, say that too, and say what the trade was.

---

## Rule 12 · Show the arrival, especially where it went wrong

The route to an answer teaches more than the answer, and in this project the route is
usually *a wrong version, then measurement, then a right one*. That history is the most
valuable part and is the first thing a tidy summary destroys.

> Caching was first added to the system prompt and tools — the fixed, small part of the
> request. That looked right and was a rounding error. Then the reference implementations
> showed a marker on the **conversation** as well, which is re-sent in full every turn.
> The first version was not a smaller fix than the second; it was a fix to the wrong half.

Say which step was measured and which was reasoned. *"Fuzzing 3,000 transcripts left 279
over budget"* is a different kind of claim from *"this should be enough"*, and the reader
is entitled to know which one they are being handed.

---

## Rule 13 · Name what makes this problem unusual

If a thing is genuinely the ordinary case, say so plainly and keep it short — pretending
otherwise wastes the reader's attention. But when it is *not* ordinary, the distinctive
part is the whole reason it needs explaining:

- **redaction** — the ordinary fix is a pattern list. The unusual part is that the
  attachment *point* was wrong, so five separate leaks all traced to one decision.
- **the orphaned tool call** — most bugs break one request. This one makes a conversation
  permanently invalid, so every future request fails and no retry helps.
- **a streaming delta** — normally you mask a secret by matching it. A key split across two
  chunks matches nothing in either half, so the whole approach is unavailable and the
  design has to route around it.

**The test:** could this paragraph be about any other codebase? If yes, it is background,
not explanation. Cut it to a line and get to the part that could not.

---

## The shape of a good answer

Not a form to fill. The order most answers want, with everything that is not load-bearing
dropped:

```markdown
# Q1 · <the question, in his words>

> *<verbatim quote>*

## The short answer
   two or three sentences, for someone who only reads this far

## Why it exists
   the failure it was built against — concrete, not abstract

## The perspective that makes it simple      [Rule 10]
   the one sentence the rest hangs off

## How it works                              [Rules 2, 3]
   real code, real names, file:line — and what kind of thing each term is

## Traced through one case                   [Rule 4]
   a table of states, or measured output

## Why it had to be this way                 [Rule 11]
   the constraint, what it forces, what has no alternative
   — or, if the choice was free, the trade that decided it

## How it got here                           [Rule 12]
   the wrong version first, and what measurement corrected it

## What is unusual about it                  [Rule 13]
   the part that could not be about another codebase

## What it is NOT                            [Rule 6]

## How the references differ
   evidence per row; "unknown" where it is unknown
```

---

## Before sending, check

**Coverage**
- [ ] Every question restated in his words, not as a number
- [ ] The nine columns above — each either covered or deliberately not needed
- [ ] The unlocking sentence appears early, not buried in paragraph six

**Substance**
- [ ] Every term's *kind* named the first time it appears
- [ ] At least one real code excerpt with `file:line`
- [ ] At least one concrete trace or measured output
- [ ] The intuition is present: the reader could re-derive this, not just recall it
- [ ] Measured claims and reasoned claims are visibly different

**Discipline**
- [ ] Said what it is NOT, wherever a wrong model is likely
- [ ] No hypothetical that cannot occur
- [ ] No paragraph that only restates the previous one
- [ ] Nothing that would read the same about an unrelated codebase

**The last one, and the one that matters**
- [ ] Read it back as someone who did not write it. Does a question form? If so, answer
      that question here rather than waiting to be asked it.

CLAUDE.md's three universal rules still apply on top of these: nothing asserted without running
it, nothing named that does not exist, no reference claim without a file and line.
