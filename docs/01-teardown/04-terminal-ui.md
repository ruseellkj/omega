# Layer 4 — Terminal UI

`pi/packages/tui` (14,184) ↔ `tau/src/tau_coding/tui/` + `rendering/` (11,501)

> This layer contains the single largest architectural disagreement between the two
> codebases — and it is not about agents at all.

---

## 1. The problem this layer solves

Your notes reduced a coding agent to a two-part formula:

> every coding agent is what ? -> agent layer + terminal UI

Layers 1–3 were the agent layer. This is the other half, and the line counts say it is not the
smaller half: Pi's TUI (14,184) is larger than its agent package (10,148), and Tau's TUI
(11,501) is over six times its portable core (1,752).

That ratio is the first lesson. **The terminal is harder than the agent loop.** The loop is 318
readable lines; making a terminal behave is thousands.

What actually has to work:

- Text streams in token by token and must appear without redrawing the world every keystroke.
- The user types *while* the agent works (Layer 2's steering queue needs somewhere to come from).
- Markdown — code fences, lists, emphasis — has to render in a grid of monospace cells.
- A tool running for 30 seconds needs live, throttled progress.
- Ctrl-C must interrupt the agent, not kill the process.
- Resizing the window must reflow, not corrupt.
- Arrow keys, Alt-Enter, bracketed paste, and mouse events arrive as ambiguous escape sequences.
- A CJK character occupies two columns; an emoji may occupy two or one depending on the terminal.

None of this is AI work. It is 1970s terminal protocol wrestling, and it is where the bulk of a
CLI agent's code goes.

---

## 2. The central divergence: build it or use a framework

### Pi builds its own

`pi/packages/tui/package.json` — the complete runtime dependency list:

```json
"dependencies": {
    "get-east-asian-width": "1.6.0",
    "marked": "18.0.5"
}
```

Two packages. One computes how many columns a Unicode codepoint occupies; the other parses
Markdown. **Everything else — the event loop, layout, focus, rendering, key decoding — is
Pi's own code**, and it is why the package is 14,184 lines:

| File | Lines | What it is |
|---|---|---|
| `components/editor.ts` | 2,351 | a full multi-line text editor |
| `keys.ts` | 1,401 | terminal key/escape-sequence decoding |
| `utils.ts` | 1,303 | text measurement, wrapping, ANSI handling |
| `tui.ts` | 1,223 | the core class — *"differential rendering"* |
| `components/markdown.ts` | 861 | Markdown → terminal cells |
| `TuiAltScreen.ts` | 805 | alternate-screen mode |
| `autocomplete.ts` | 786 | completion UI |
| `terminal-image.ts` | 559 | images in a terminal |
| `TuiMainScreen.ts` | 552 | inline (non-alt-screen) mode |
| `terminal.ts` | 531 | raw mode, capabilities |
| `stdin-buffer.ts` | 434 | byte-level input buffering |
| `layout.ts` | 398 | box layout |
| `keybindings.ts` | 269 | binding resolution |

### Tau uses Textual

`tau/pyproject.toml` — the complete runtime dependency list for the *entire agent*:

```toml
dependencies = [
    "anyio>=4.0",
    "httpx[socks]>=0.27",
    "packaging>=24.0",
    "pillow>=11.0",
    "pydantic>=2.11",
    "pygments>=2.18",
    "rich>=13.0",
    "textual>=8.2.8",
    "typer>=0.12",
]
```

Nine dependencies total, and `tau_coding/tui/app.py:17-39` shows what that buys:

```python
from rich.console import Console, Group
from rich.style import Style
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingsMap
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Key, Resize
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import (…)
from textual.worker import Worker
```

Widgets, containers, CSS-style queries, themes, modal screens, timers, background workers,
key bindings, and a resize event — all inherited. Tau's TUI is **application code on a
framework**; Pi's is **the framework and the application**.

### What the trade actually costs

**Verified:** Pi implements differential rendering itself (`tui.ts:2` — *"Minimal TUI
implementation with differential rendering"*; `tui.ts:248` — *"Main class for managing terminal
UI with differential rendering"*), with a `lastRenderAt` timestamp and elapsed-time check
(`tui.ts:331`, `758`, `772-780`) throttling repaints. Grepping Tau's `app.py` for
`diff`/`last_render`/`dirty` returns **nothing** — Tau does no diffing of its own, because
Textual's compositor already does it.

So the same capability exists in both, obtained two different ways:

| | Pi (build) | Tau (framework) |
|---|---|---|
| Control over rendering | total | whatever Textual exposes |
| Dependency risk | ~zero | a major framework's release cadence |
| Lines to maintain | 14,184 | ~11,501, but at a higher level |
| Startup cost | minimal | framework init |
| Time to first working UI | weeks | days |
| Unusual terminal behaviour | fixable | file an upstream issue |

Neither is wrong. **Pi's choice makes sense because Pi ships a TUI *library* as a product**
(`packages/tui` is separately published). **Tau's choice makes sense because Tau is a teaching
codebase** — 6,808 lines of `app.py` you can read beats 14,184 lines of terminal internals you
must first understand.

For your own build the implication is direct: **use a framework.** Textual (Python) or a
comparable TS library gets you to a working agent in Tier 3 instead of Tier 5. Writing your own
key decoder teaches you about escape sequences, not about agents.

---

## 3. The concepts that transfer regardless of framework

These are the ideas worth extracting; they apply whether you hand-roll or not.

**Differential rendering.** Never repaint the whole screen. Compute the desired frame, compare
against the last one, emit only the changed cells. Without it, streaming text at 50 tokens/sec
means 50 full-screen repaints per second and visible flicker. This is the one concept in Layer 4
that is genuinely *necessary* — you either write it (Pi) or inherit it (Tau), but you cannot skip
it.

**Render throttling on top of diffing.** `lastRenderAt` + elapsed check (`tui.ts:772-780`).
Diffing reduces bytes written; throttling reduces how often you compute a frame at all. Note
this is the same pattern as Layer 3a's `BASH_UPDATE_THROTTLE_MS = 100` — the whole stack
throttles at every streaming boundary, and that repetition is itself the lesson.

**Alt-screen vs main-screen as separate implementations.** `TuiAltScreen.ts` (805) and
`TuiMainScreen.ts` (552) are distinct classes over a shared `TuiBase`
(`TuiMainScreen.ts:47`: `export class TuiMainScreen extends TuiBase implements TUI`). Alt-screen
takes over the terminal like `vim` and restores it on exit; main-screen prints inline so your
scrollback survives. Coding agents want inline output — you want to scroll back through what
happened after quitting — which is why both exist and why the inline one is not an afterthought.

**Key decoding is genuinely hard.** 1,401 lines in `keys.ts` plus 434 in `stdin-buffer.ts`. A
terminal delivers keys as bytes: `Escape` and `Alt+key` and the start of an arrow-key sequence
are ambiguous until more bytes arrive (or don't). This is the strongest argument for a framework.

**Character width is not string length.** `get-east-asian-width` is one of only two Pi runtime
dependencies, which tells you it isn't optional. A CJK glyph is two columns wide; using
`str.length` for layout corrupts every box border on the screen.

**The UI is an event consumer, nothing more.** This is where Layers 1–4 close the circle: the
TUI subscribes to Layer 2's ten `AgentEvent`s and renders them. `tau_coding/tui/adapter.py` is
**99 lines** — the entire bridge from agent to UI. That is only possible because the event
vocabulary was designed for it: `MessageUpdateEvent` carries both the accumulated message and the
raw provider delta (Layer 2 §2.2), so the renderer picks whichever granularity it needs.

**Background work must not block the UI.** Tau imports `textual.worker.Worker`; Pi manages its
own async loop. Either way the agent runs off the render thread, or typing freezes while the
model streams.

**Terminal niceties are separable and cheap.** `terminal_title.py` (124),
`terminal_notification.py` (115), `file_drop.py` (83) — window title updates, bell/notification
on completion, drag-and-drop file paths. Small files, disproportionate perceived quality.

---

## 4. Pi vs Tau — concept versus accident

### Identical (concept)

| Concern | Evidence |
|---|---|
| The UI is a subscriber to agent events, not a participant | `adapter.py` (99), Pi's subscribe pattern |
| Differential rendering is mandatory | `tui.ts:2`; Textual's compositor |
| Render throttling above diffing | `tui.ts:772-780`; Textual timers |
| Markdown rendered to terminal cells | `markdown.ts` (861); `rich` |
| Syntax highlighting | `marked` + own; `pygments` |
| Autocomplete as a distinct subsystem | `autocomplete.ts` (786) ↔ `autocomplete.py` (511) |
| Explicit UI state object | `state.ts` ↔ `tui/state.py` (552) |
| Images in the terminal | `terminal-image.ts` (559) ↔ `pillow` |
| Themes | `keybindings.ts`/styles ↔ `themes/__init__.py` (459) |
| Window title + completion notification | both |

### Genuinely different

**1. Build vs framework** (§2). The defining difference of this layer.

**2. Monolith vs components.** Tau's `app.py` is **6,808 lines** — the largest single file in
either repository — plus `widgets.py` (2,260). Pi spreads equivalent work across 37 files with
no file over 2,351.

Tau's monolith is a real cost, and it's the one place where Tau is *less* readable than Pi
despite being the teaching codebase. Framework-driven UIs tend to accrete this way: the `App`
subclass becomes the place where everything is wired, and nothing forces you to split it.

**3. Pi ships a reusable TUI library; Tau does not.** `packages/tui` is independently
publishable with its own dependency list. `tau_coding/tui/` is internal.

**4. Pi implements a full text editor** (`components/editor.ts`, 2,351). Multi-line editing with
its own cursor and selection model. Tau uses Textual's input widgets.

---

## 5. If I rebuilt this layer from scratch

**Tier 1 — no TUI at all.** `print()`. Seriously. A TUI at Tier 1 obscures whether your loop
works. Print each `AgentEvent` as one line and you have a debugger for Layers 1–2.

**Tier 2 — still no TUI.** Add colour and a spinner (Rich in Python, picocolors/chalk in TS)
without taking over the screen. Do add:

1. **Ctrl-C wired to Layer 2's cancellation token** — not to `SIGINT`-kills-process. This is a
   correctness issue, not a UI one: Layer 2's orphaned-tool-result repair depends on graceful
   cancellation.
2. **Throttled tool-output printing**, ~100 ms, matching Layer 3a.

**Tier 3 — a real TUI, on a framework.**

3. **Use Textual (Python) / a comparable TS library.** Do not hand-roll. Pi's 14,184 lines are
   justified because the TUI *is* one of its products; that is not your goal.
4. **Keep the adapter thin.** Target Tau's 99-line `adapter.py`: subscribe to agent events,
   translate to widget updates, nothing else. Any agent logic that leaks into the UI is a bug —
   the agent must remain runnable headless (which Layer 5's `server`/`client` split proves is
   possible).
5. **Inline output, not alt-screen.** Scrollback after exit is worth more than a full-screen
   layout for a coding agent.
6. **Split the app file early.** Learn from Tau's 6,808-line `app.py`: separate transcript
   rendering, input handling, status/footer, and modals from the start.
7. **Run the agent in a worker**, off the render path.
8. **Show what Layer 3b measured**: context usage, token counts, cost, cache hit rate. You built
   the accounting; surfacing it is what makes it useful.

**Tier 3+, cheap and high-impact:** window title updates, a completion notification, and
drag-and-drop file paths — a few hundred lines total.

**The test that proves this layer:** stream a long response while typing a steering message, then
Ctrl-C mid-tool-execution. Text should render smoothly, your keystrokes shouldn't be dropped, the
steering message should be queued (not injected mid-turn), and the interrupt should produce a
labelled interrupted-tool-result rather than a dead session.

---

## 6. Honest scope note

**This layer is documented at survey level, not traced like Layers 1–3.** I verified structure,
dependencies, file sizes, the differential-rendering claim in both directions, and the framework
divergence — but I did not read `editor.ts` (2,351), `keys.ts` (1,401), or `app.py` (6,808) line
by line.

That is a deliberate allocation, and the reasoning is worth recording: the transferable content
of Layer 4 is *concepts* (diffing, throttling, thin adapter, worker threads), and the plan's
recommendation is to use a framework rather than reimplement any of it. Reading 4,000 lines of
hand-rolled escape-sequence decoding would not change a single decision in the Python or
TypeScript builds. Layers 1–3 get traced because you are going to rewrite them; Layer 4 you are
going to delegate.

If Layer 4 later becomes load-bearing — a custom widget Textual can't express — `tui.ts` and
`app.py` are where to start.

---

## Files examined for this layer

| File | Lines | Depth |
|---|---|---|
| `pi/packages/tui/package.json` | — | read (dependency list) |
| `tau/pyproject.toml` | — | read (dependency list) |
| `pi/packages/tui/src/tui.ts` | 1,223 | targeted (diffing, throttle) |
| `pi/packages/tui/src/TuiMainScreen.ts` | 552 | targeted (class hierarchy) |
| `tau/src/tau_coding/tui/app.py` | 6,808 | targeted (imports, absence of diffing) |
| whole-package inventories | 25,685 | measured, not read |

Verification greps run: differential-rendering references in both repos; alt-screen vs
main-screen class structure; TUI framework imports; runtime dependency lists.

---

*Next: Layer 5 — `protocol`, `storage`, `server`, `client`, `evals`, and the extension system,
where ~75 examples (including `subagent/`, `plan-mode/`, and `todo.ts`) turn out to carry most
of what my notes assumed was missing.*
