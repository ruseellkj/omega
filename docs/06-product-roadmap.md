# Product Roadmap — Tier 3 → Tau → Pi

A reference for later. **Do not act on this until Tiers 1–3 are built and the docs are
understood.** Its only job is to answer "what would it take to call this a product?" when that
becomes the question.

Three levels, and they are deliberately different in kind:

| Level | What it means | Size |
|---|---|---|
| **Tier 3** | A working coding agent. You'd use it daily; someone else could too. | ~4,000 lines |
| **Tau** | A *product*. Installable, configurable, subscription-auth, polished. | ~36,400 lines |
| **Pi** | A *platform*. Extensible, multi-front-end, 38 providers, its own TUI library. | ~109,000 lines |

**Tau is the next target. Pi is the horizon.** Note the jump sizes: Tier 3 → Tau is ~9×, and
Tau → Pi is ~3× on top of that. Most of the first jump is *polish and breadth*, not architecture —
which is the good news.

---

# SECTION A — Tier 3 → Tau (product parity)

Two kinds of gap. **A1 is missing entirely. A2 exists but is minimal.** A2 is the one people
forget, and it's where most of the 32,000-line difference actually lives.

## A1. Missing entirely — things to add

### A1.1 Subscription authentication ⭐ highest value

| Tau file | Purpose |
|---|---|
| `oauth.py` | the generic OAuth flow |
| `oauth_anthropic.py` | Claude subscription login |
| `oauth_github_copilot.py` | Copilot login |
| `oauth_device.py` | device-code flow (no browser) |
| `oauth_registry.py` | which provider uses which flow |
| `oauth_types.py` | shared types |
| `credentials.py` | on-disk token storage + refresh |

~1,500+ lines. **The only skipped feature with a direct financial payoff:** it lets you use a
Claude/Copilot *subscription* instead of paying per API token. On heavy use that's the difference
between a flat monthly fee and a per-token bill.

Plugs into: the auth-resolver callback you already have from Tier 1. No rearchitecting.

### A1.2 Provider catalog

| Tau file | Purpose |
|---|---|
| `provider_catalog.py` | which providers exist |
| `provider_config.py` | per-provider settings |
| `provider_runtime.py` | resolving a provider at call time |
| `catalog_loader.py` | loading catalog data |
| `model_limits.py` | context window per model |
| `data/` | the catalog itself |

Tier 3 hardcodes two models. A catalog turns "add a provider" into a data edit instead of a code
change, and gives you per-model context windows, pricing, and capability flags.

### A1.3 Three more provider adapters

`openai_codex.py` (1,054) · `google.py` (490) · `mistral.py` (522). Tier 3 has ~2.

Note `openai_codex.py` is the largest of the three — the Codex/subscription path is its own beast.

### A1.4 Extension system

`extensions/api.py`, `loader.py`, `runtime.py`. Tier 3 has hooks; this exposes them to third-party
code. Build it *after* you have hooks worth exposing, not before.

### A1.5 Slash commands
`commands.py` — `/help`, `/model`, `/compact`, `/resume`. The command surface users expect.

### A1.6 Skills
`skills.py` (239) — reusable instruction bundles loaded on demand.

### A1.7 Distribution & lifecycle

| Tau file | Purpose |
|---|---|
| `update_check.py`, `updater.py`, `version.py` | self-update |
| `reload.py` | hot reload during development |
| `self_docs.py` | self-documenting help |
| `resources.py`, `paths.py` | resource and config path resolution |
| `shell_config.py` | detect the user's shell |
| `py.typed` | ship type information |

Individually trivial, collectively the difference between "a repo" and "a program people install."

### A1.8 Diagnostics
`diagnostics.py` — structured diagnostic capture for support and bug reports.

### A1.9 Repo-level infrastructure
A real test suite · `dev-notes/` (Tau has 25 design notes) · a documentation website ·
`landing.html` · published packaging (`tau = "tau_coding.cli:app"` entry point).

---

## A2. Present but minimal — things to upgrade

This is the bigger half. Each row: what Tier 3 has → what Tau has.

### A2.1 Terminal UI — thin Textual app → a real TUI

**Tier 3: ~500 lines. Tau: 11,501.**

| Tau file | Lines | What Tier 3 lacks |
|---|---|---|
| `tui/app.py` | 6,808 | the full application: modals, screens, layout |
| `tui/widgets.py` | 2,260 | custom widgets for transcript, tools, diffs |
| `tui/state.py` | 552 | explicit UI state model |
| `tui/autocomplete.py` | 511 | **file/command autocomplete as you type** |
| `tui/themes/__init__.py` | 459 | multiple themes |
| `tui/config.py` | 207 | UI configuration |
| `tui/terminal_title.py` | 124 | window title reflects current task |
| `tui/terminal_notification.py` | 115 | bell/notification on completion |
| `tui/file_drop.py` | 83 | drag a file onto the terminal to reference it |
| `rendering/transcript.py` | 98 | transcript rendering separate from widgets |

Plus a **session sidebar** showing context %, tokens, cost, and cache hit rate.

*Caution from Stage 1: Tau's `app.py` at 6,808 lines is the one place Tau is less readable than Pi.
Grow yours in split files from the start.*

### A2.2 Session management — save/resume → full lifecycle

**Tier 3: save + resume. Tau: `session.py` is 2,704 lines** plus `session_manager.py` (333),
`session_export.py`, `session_stats.py`.

| Capability | Tier 3 | Tau |
|---|---|---|
| Save / resume | ✅ | ✅ |
| **List** past sessions | ❌ | ✅ |
| **Fork / branch** a session | `parent_id` stored, no UI | ✅ full tree navigation |
| **Export** (markdown/JSON) | ❌ | ✅ |
| **Stats** (tokens, cost, cache rate) | basic counters | ✅ aggregated + rendered |
| **Name / rename** sessions | ❌ | ✅ |
| Branch summarization | ❌ | ✅ `branch_summary.py` (214) |

### A2.3 Compaction — basic → complete

Tier 3 implements threshold + summarize-old-keep-recent. Tau adds:

- **branch summarization** (`branch_summary.py`) — summarizing an abandoned branch, distinct from
  compacting the active path
- **custom compaction instructions** ("focus on the auth work")
- **turn-prefix summarization** — when a *single* turn is too large to keep
- **bounded summarizer input** (4k/message, 60k total)

### A2.4 Prompt caching — 4 breakpoints → capability matrix

Tier 3 places the four breakpoints. Tau adds:
- `cache_retention` resolved from **auth mode** (OAuth → 1h, API key → 5min)
- three capability flags layered detected → provider → per-model
- `none` mode for gateway providers that reject `cache_control`
- **cache hit rate surfaced in the UI**, rendered as absent (not `0%`) when unsupported

### A2.5 Tools — functional → polished

| Capability | Tier 3 | Tau |
|---|---|---|
| read/write/edit/bash | ✅ | ✅ (`tools.py` 1,202) |
| Truncation + spillover | ✅ | ✅ |
| **Tool result renderers** | plain text | rich per-tool rendering |
| **Diff rendering** for edits | minimal | proper diffs |
| Image reading | basic | `image_processing.py` + Pillow pipeline |

### A2.6 Configuration — hardcoded → managed

Tier 3: env vars and constants. Tau: `provider_config.py`, `tui/config.py`, `resources.py`,
`paths.py`, `shell_config.py` — profiles, per-project overrides, XDG-correct config locations.

### A2.7 Prompt assembly — one prompt → templates

Tier 3: a system prompt string. Tau: `system_prompt.py` (205) + `prompt_templates.py` (212) +
`thinking.py` (reasoning-level control per request).

### A2.8 Provider layer — 2 adapters → infrastructure

Tier 3 has adapters and retry. Tau adds `http.py`, `http_errors.py` (typed error taxonomy),
`content.py`, and `retry.py` with per-error-class policy.

---

## A3. The Tier 3 → Tau checklist

Rough order, highest value first:

- [ ] **1. Subscription OAuth** + credential store (A1.1) — the money one
- [ ] **2. Session list / fork / export / stats** (A2.2) — the daily-use one
- [ ] **3. TUI: autocomplete, sidebar, themes, notifications, file drop** (A2.1)
- [ ] **4. Provider catalog + model metadata** (A1.2)
- [ ] **5. Slash commands** (A1.5)
- [ ] **6. Compaction: branch summaries, custom instructions, turn-prefix** (A2.3)
- [ ] **7. Caching: capability/intent matrix + hit-rate display** (A2.4)
- [ ] **8. Tool renderers + diff display** (A2.5)
- [ ] **9. Config management + profiles** (A2.6)
- [ ] **10. Packaging, versioning, self-update** (A1.7)
- [ ] **11. Extension API** (A1.4)
- [ ] **12. Skills** (A1.6)
- [ ] **13. Real test suite + `dev-notes/`** (A1.9)
- [ ] **14. Two more adapters** (A1.3)

**Nothing on this list requires rearchitecting** — that's the test of whether Tiers 1–3 were built
right. Every item plugs into a seam that already exists.

---

# SECTION B — Tier-by-tier features and gaps, vs Tau and Pi

## Tier 1 — "the loop works" (~700 lines)

**Has:** provider interface · 12 stream events · message/content model · the loop · stop condition
· tool schemas · tool dispatch · system prompt · turn limit · errors-as-data · read/write/bash ·
output truncation

**Gaps vs Tau:** everything else — no edit tool, no persistence, no cancellation, no approvals, no
path confinement, one provider, no compaction, no retry, no cost tracking, no caching, no TUI, no
sessions, no OAuth, no catalog, no skills, no commands, no extensions, no evals

**Gaps vs Pi:** all of the above **plus** no parallel tools, no three-tier core split
(`agent-loop`/`agent`/`agent-harness`), no session tree, no SQLite, no `protocol`/`server`/`client`,
no own TUI library, no quirk matrix, no image generation, no ~75 extensions

**Verdict:** proves the loop terminates. Scratch directory only.

## Tier 2 — "you'd use it daily"

**Adds:** edit tool · per-path lock · project instruction file · cancellation · orphaned-tool
repair · two views of history · session save/resume · context accounting · steering + follow-up
queues · retry/backoff · cost tracking · approval gates · path confinement · secret handling ·
headless harness + smoke eval · **second provider**

**Gaps vs Tau:** compaction · prompt caching · real TUI · search tools · image reading · session
list/fork/export/stats · OAuth · provider catalog · slash commands · skills · extensions ·
themes/autocomplete · packaging/self-update · diagnostics · 3 more adapters

**Gaps vs Pi:** all of the above **plus** parallel tool execution · session branching in storage ·
SQLite + cross-session search · `protocol`/`server`/`client` · own TUI library · settings manager ·
trust manager · resource loader · model resolver · ~75 extensions · `evals` package

**Verdict:** the tier where it becomes *yours*. Stopping here leaves something genuinely useful.

## Tier 3 — "survives long tasks" (~4,000 lines)

**Adds:** compaction · prompt caching · session branching · a real (thin) TUI · structured logging
· grep/find/ls · image reading · **one** of subagents-or-plan-mode

**Gaps vs Tau:** everything in Section A — i.e. OAuth, catalog, 3 adapters, extension API, slash
commands, skills, distribution/self-update, diagnostics, plus the eight A2 upgrade areas
(TUI depth, session lifecycle, compaction completeness, caching matrix, tool renderers, config
management, prompt templates, HTTP infrastructure)

**Gaps vs Pi:** all of Tau's gaps **plus** everything in Section C below

**Verdict:** complete for the purpose. Everything remaining is polish, breadth, or a different
product.

---

# SECTION C — Tau → Pi (the horizon)

What the ultimate goal adds on top of product parity. **Reference only** — most of it you may
never want.

| Pi capability | Package | Lines | Why Pi has it |
|---|---|---|---|
| **10 wire formats, 38 providers** | `ai` | 21,429 | universal provider coverage |
| **Provider quirk matrix** | `ai/types.ts` | ~140 flags | `thinkingFormat` alone has 10 variants |
| **6 OAuth flows** | `ai/auth/oauth/` | ~2,200 | Anthropic, Codex, Copilot, OpenRouter, Kimi, Radius |
| **Generated model catalog** | `ai/models.generated.ts` | 700+ | auto-generated from upstream |
| **Enterprise backends** | Bedrock, Azure, Vertex | ~2,100 | corporate deployment |
| **Image generation** | `ai/images*` | ~1,000 | not just reading images |
| **Three-tier core** | `agent` | 792+577+1,185 | loop / Agent class / harness as separate layers |
| **9 loop hooks** | `agent/types.ts` | — | Tau has 4; adds `prepareNextTurn`, `shouldStopAfterTurn`, `getApiKey`, `convertToLlm` |
| **Extensible transcript** | `CustomAgentMessages` | — | apps add message types via declaration merging |
| **Parallel tool execution** | `agent-loop.ts` | — | with "any sequential tool forces the batch sequential" |
| **SQLite storage + search** | `storage/sqlite-node` | — | migrations, materialized path view, cross-session search |
| **Own TUI library** | `tui` | 14,184 | separately publishable; includes a 2,351-line text editor |
| **Wire protocol** | `protocol` | 753 | length-prefixed CBOR frames |
| **Client/server** | `server`+`client` | 5,122 | **remote agent + multiple front-ends**, constant-time token auth, snapshot-then-deltas |
| **Eval package** | `evals` | 1,277 | task-based measurement |
| **~75 extensions** | `coding-agent/examples/` | — | subagent, plan-mode, sandbox, todo, permission-gate, git-checkpoint, games |
| **Package manager** | `coding-agent/core` | 2,625 | manages extension/skill installation |
| **Settings manager** | `coding-agent/core` | 1,260 | layered configuration |
| **Trust manager** | `coding-agent/core` | 244 | per-project trust decisions |
| **Model resolver** | `coding-agent/core` | 726 | alias/fallback resolution |
| **Unicode sanitization** | `ai/utils` | — | injection defence |

**Two Pi capabilities worth wanting even at product stage:**

1. **Client/server** (`protocol` + `server` + `client`) — a **remote agent**. Run it on a server,
   attach from your laptop, or attach an editor plugin and a TUI to the same session. If your Layer
   4 adapter stayed thin, this is additive rather than a rewrite.
2. **Parallel tool execution** — real speedup on multi-file reads, and Tau's is broken.

**Two things neither has, and you should still skip:** MCP and retrieval/RAG.

---

## How to use this file

1. **Build Tiers 1–3 first.** Ignore all of this until then.
2. **Then pick from Section A3 by what annoys you**, not by list order. If you're paying too much
   per token, do OAuth. If you keep losing sessions, do session management. Real friction beats a
   roadmap.
3. **Section C is a menu, not a plan.** Pi has 109,000 lines because it's a platform with users.
   You may want three things from it and none of the rest.

The measure of whether Tiers 1–3 were built correctly is simple: **every item in Section A should
be an addition, never a rewrite.** If something on that list would force you to change the loop,
the provider interface, or the event vocabulary, that's a signal the seam was wrong — go back to
`01-teardown/02-agent-loop-tools.md` §4 and check the hook shape.
