# Folder Trees

How the architecture maps onto directories. Four trees: the two references as they actually are,
a minimal agent, a production agent, and this repo.

---

## 1. The references, side by side

The clearest thing about these two is that **the same architecture produces the same layout in
two different languages.**

```
pi/  (TypeScript, Bun, MIT)                tau/  (Python, MIT)
├── packages/                              ├── src/
│   ├── ai/            21,429  L1          │   ├── tau_ai/          4,891  L1
│   ├── agent/         10,148  L2          │   ├── tau_agent/       1,752  L2
│   ├── coding-agent/  55,895  L3          │   └── tau_coding/     29,771  L3+L4
│   ├── tui/           14,184  L4          ├── tests/
│   ├── protocol/         753  L5          ├── docs/
│   ├── client/           956  L5          ├── dev-notes/    ← 25 design notes
│   ├── server/         4,166  L5          ├── examples/
│   ├── storage/                L5         ├── website/
│   │   └── sqlite-node/                   ├── pyproject.toml
│   └── evals/          1,277              └── uv.lock
├── scripts/
├── package.json          (workspaces)
├── tsconfig.base.json
├── biome.json            (lint + format)
└── AGENTS.md             ← instructions for agents working on Pi
```

**What to notice:**

- **Package boundaries are layer boundaries.** You can tell the architecture from `ls`.
- **Pi separates the TUI into its own package; Tau nests it inside the app.** That's the
  build-your-own vs use-a-framework split — Pi's TUI is independently publishable.
- **Tau has `dev-notes/` and Pi doesn't.** 25 files of design reasoning. For learning, it's Tau's
  single biggest advantage.
- **Both have an `AGENTS.md`-style file** — these repos expect agents to work on them.
- **`storage/` is a directory containing a package** (`storage/sqlite-node/`), not a package
  itself. This is what made my first line count report zero.

### Zoomed in: the two cores

```
pi/packages/agent/src/                     tau/src/tau_agent/
├── agent-loop.ts        792               ├── loop.py            318   ← the loop
├── agent.ts             577               ├── harness.py         244   ← state
├── types.ts             437               ├── messages.py        277
├── proxy.ts             367               ├── tools.py           118
├── stream-fn.ts          20               ├── events.py           87   ← 10 agent events
├── index.ts              64               ├── provider_events.py 107   ← 12 stream events
└── harness/                               ├── provider.py         32   ← the contract
    ├── agent-harness.ts  1,185            ├── types.py             8
    ├── types.ts            967            └── session/
    ├── compaction/       1,287                ├── memory.py       136
    ├── session/          1,161                ├── entries.py      114
    ├── tools/            1,190                ├── jsonl.py        111
    ├── skills.ts           375                ├── storage.py       42
    ├── env/                675                └── tree.py         40
    ├── prompt-templates.ts 267
    └── utils/              545
```

**Tau's entire core is 1,752 lines and fits on one screen.** That's why it's the better entry
point, and why the plan reads Tau first per layer.

---

## 2. A minimal agent (Tier 1)

Everything needed for a working loop. One package, no monorepo.

```
minimal-agent/
├── src/agent/
│   ├── types.py          message + content types            ~80
│   ├── events.py         the 12 stream events               ~60
│   ├── provider.py       the one-method interface           ~30
│   ├── providers/
│   │   ├── anthropic.py  one adapter                       ~150
│   │   └── fake.py       test double — write it now         ~40
│   ├── tools.py          Tool + ToolResult                  ~60
│   ├── builtin_tools.py  read_file, write_file, run_shell  ~120
│   └── loop.py           the loop                          ~150
├── tests/
│   ├── test_loop.py      uses fake.py — runs offline
│   └── test_tools.py
├── main.py               print-based CLI                     ~40
├── pyproject.toml
└── README.md
```

**~700 lines.** Notes on the choices:

- **`provider.py` sits above `providers/`** — the interface is owned by the core, implemented by
  adapters. Get this direction right on day one; reversing it later is painful.
- **`fake.py` ships with the first adapter, not later.** It's how the whole suite runs offline, and
  it's the proof the interface is small enough.
- **`events.py` separate from `types.py`** because Layer 2 will add its own event file, and you
  want the distinction visible.
- **No TUI directory.** Print statements. A TUI at Tier 1 hides whether the loop works.

---

## 3. A production CLI coding agent (Tier 3)

The shape to grow into. Three packages, mirroring the reference layering.

```
coding-agent/
├── packages/                       (or src/<pkg>/ in Python)
│   ├── ai/                         ── LAYER 1 · provider ──
│   │   ├── types.*                 messages, content blocks, usage
│   │   ├── events.*                the 12 stream events
│   │   ├── provider.*              re-export of the core contract
│   │   ├── api/                    one adapter per WIRE FORMAT, not per vendor
│   │   │   ├── anthropic-messages.*
│   │   │   └── openai-completions.*
│   │   ├── auth/                   api-key + oauth resolvers
│   │   ├── models.*                catalog: context window, pricing, capabilities
│   │   ├── retry.*                 backoff — invisible above this layer
│   │   └── fake.*                  test double
│   │
│   ├── agent/                      ── LAYER 2 · portable core ──
│   │   ├── provider.*              THE CONTRACT lives here (dependency inversion)
│   │   ├── provider_events.*       12 events
│   │   ├── events.*                10 agent events
│   │   ├── messages.*
│   │   ├── tools.*                 Tool + ToolResult + hooks types
│   │   ├── loop.*                  ~300 lines. Should not grow.
│   │   ├── harness.*               state, queues, cancellation, orphan repair
│   │   └── session/
│   │       ├── entries.*           SessionEntry with parent_id
│   │       ├── tree.*              path_to_entry, cycle detection
│   │       ├── jsonl.*             append-only + migrate-on-read
│   │       └── store.*             the storage interface
│   │
│   └── coding/                     ── LAYER 3 + 4 · the app ──
│       ├── tools/
│       │   ├── read.* write.* edit.* bash.*        the four
│       │   ├── grep.* find.* ls.*                  optional (Tau ships none)
│       │   ├── truncate.*                          2k lines / 50 KB budget
│       │   ├── file_lock.*                         per-resolved-path
│       │   └── paths.*                             confinement — ONE place
│       ├── context/
│       │   ├── estimate.*          chars/4, counts tool schemas
│       │   └── compaction.*         fills transform_context
│       ├── prompts/
│       │   ├── system.*            byte-stable — caching depends on it
│       │   └── templates.*
│       ├── policy/
│       │   ├── approval.*          fills before_tool_call
│       │   └── redact.*            fills after_tool_call
│       ├── config/                 settings, provider selection, keybindings
│       ├── session_manager.*       resume, list, export
│       ├── tui/
│       │   ├── app.*               SPLIT THIS EARLY (Tau's is 6,808 lines)
│       │   ├── transcript.*
│       │   ├── input.*
│       │   ├── status.*            context %, tokens, cost, cache hit rate
│       │   └── adapter.*           agent events → widgets. Target ~100 lines.
│       └── cli.*                   arg parsing, entry point
│
├── harness/                        headless driver — tests AND terminal-bench
├── evals/
│   ├── smoke.*                     ~17 lines: start, instruct, tool-call, stop
│   └── tasks/
├── tests/
└── docs/
```

**Justification for each top-level entry:**

| Entry | Why it's separate |
|---|---|
| `ai/` | so a second wire format costs one file, not six edits |
| `agent/` | so the loop stays domain-agnostic and testable without a filesystem |
| `coding/` | all policy, all filesystem knowledge, all UI |
| `harness/` | headless driver — the prerequisite for both tests and benchmarks |
| `evals/` | task-based measurement, distinct from unit tests |

**Deliberately absent:** `retrieval/`, `embeddings/`, `mcp/` — neither reference has them.
`protocol/`, `server/`, `client/` are Tier 3+, only if you want a second front-end.

---

## 4. This repo

```
cli-agent/
├── README.md              public face
├── LICENSE                MIT
├── CLAUDE.md              instructions for agents working here
├── .gitignore             excludes research/ and docs/dist/
│
├── docs/                  ← your study notes (Stages 1–3)
│   ├── 00-concepts/
│   │   ├── anatomy.md         42 components, four groups, tiered
│   │   └── security.md        because it runs shell commands
│   ├── 01-teardown/
│   │   ├── 01-provider-stream.md
│   │   ├── 02-agent-loop-tools.md
│   │   ├── 03-coding-tools.md
│   │   ├── 03b-context-sessions-compaction.md
│   │   ├── 04-terminal-ui.md
│   │   └── 05-beyond-the-core.md
│   ├── 03-architecture/
│   │   ├── 01-plain.md        zero jargon
│   │   ├── 02-beginner.md     70 lines + 9 failures
│   │   └── 03-production.md   the real thing
│   ├── 04-glossary.md
│   ├── 04-folder-trees.md     ← this file
│   ├── 05-language-notes.md   (Stage 5) architecture vs language accident
│   └── dist/                  generated PDF — gitignored
│
├── scripts/
│   └── build-pdf.sh       docs → one PDF
│
├── python/                ← Stage 4. Independent. Tier 1 → 2 → 3.
├── typescript/            ← Stage 5. Independent. Shares NO code.
│
└── research/              ← gitignored. Reference input, not deliverable.
    ├── pi/                cloned, MIT
    ├── tau/               cloned, MIT
    ├── transcripts/       19 .srt files
    └── video-urls.txt
```

| Entry | Why |
|---|---|
| `docs/` | the durable asset — the code is practice, the understanding is the point |
| `python/`, `typescript/` | same architecture twice; the diff isolates concept from language |
| `research/` | gitignored: 31 MB of third-party code and other people's transcripts |
| `docs/dist/` | gitignored: generated output |
| `scripts/` | one place for build tooling |

**Why `python/` and `typescript/` share nothing:** if they shared code, the comparison would be
meaningless. The only thing in common is the layer contract — and `05-language-notes.md` records
where they diverge and why. Anything that *had* to change is language accident; anything identical
is the real architecture.

---

## 5. Four layout rules worth keeping

1. **One directory per layer, named after the layer.** Someone should infer the architecture from
   `ls`.
2. **Interfaces live with the consumer, implementations in a subdirectory.** `agent/provider.*`
   defines it; `ai/api/*` implements it. This is the single most important rule here.
3. **Split the TUI app file before it needs splitting.** Tau's `app.py` reached 6,808 lines — the
   one place Tau is less readable than Pi.
4. **The loop gets its own file and should not grow.** If `loop.*` is getting longer, something
   belongs in a hook instead.

---

## 6. Monorepo tooling — do you need it?

Pi uses npm workspaces. Tau uses a single `pyproject.toml` with three packages under `src/`.

**For this project: no monorepo tooling.** Turborepo, Nx, and friends solve build-caching problems
across many packages with shared dependencies. You have two independent implementations that share
nothing.

- `python/` → one `pyproject.toml`, `uv` workspace if you later split packages
- `typescript/` → one `package.json` with workspaces if needed, Bun as runtime

Add tooling when a build gets slow, not before.
