# omega — the product backlog

*Was `TIER-3-PLUS.md`. Renamed because "Tier 3+" described **when** this work was not happening
rather than **what** it is, and it stopped being true the moment part of it shipped: distribution
is done, and OAuth is shipped for Anthropic and for ChatGPT (Codex) with its cost recorded rather than declined. A backlog can hold a finished row and a
refused row; a tier number cannot.*

*"Backlog" rather than "features" on purpose — CI, packaging and trusted publishing are not
features anyone asks for, and calling them that would make the list read as a wish list instead of
the work it is.*

**Everything a product needs that the layer diagram never described.**

Tier 3 finishes the architecture: L4 stops being partial and the last two beginner failures close.
This file is what remains — and it is deliberately a *different kind* of list. Nothing here is
required for omega to be correct, complete as a design, or good to read. Every item is required for
omega to be something a stranger installs.

**Status: a backlog, not a commitment.** Items may never be built. The value of writing them down is
that the boundary becomes a decision instead of a vague sense that "there's more".

## How this list was built

Not from imagination. Every entry names a module in `research/tau/src/tau_coding/` that exists and
has been counted, so each line here is evidence that the category is real work rather than a guess
at what a product might want. Line counts are from the reference, not estimates for omega.

For scale, at the start of Tier 3:

| | Source lines | Files |
|---|---|---|
| omega | 6,247 | 38 |
| Tau | 36,414 | 90 |
| Pi | 133,800 | — |

omega is **17% of Tau** and **4.7% of Pi**. This file is most of the difference.

---

## 1 · Distribution and lifecycle — **done**

**This section shipped.** It is kept rather than deleted because the reasoning below is still the
reasoning, and because a plan that quietly loses its finished items stops being checkable.

| | Then | Now |
|---|---|---|
| name | unclaimed | `omega-coding-agent` in `pyproject.toml`; the command is still `omega` |
| version | no `--version` at all | `--version`, from `omega_coding/version.py` |
| installer | none | `website/public/install.sh`, 109 lines, run end to end |
| CI | **no `.github/` directory at all** | `ci.yml` and `publish.yml` |

Two things learned in the doing, neither of them in the plan:

- **`uv tool dir --bin` writes ANSI colour codes even through a pipe.** Verified with `od -c`. The
  first installer stored `\033[36m/path\033[39m` in a variable and then reported a missing shim it
  had just installed successfully. `NO_COLOR=1` on that one call is load-bearing. Tau's script has
  the same shape and would hit it on a uv new enough to colour that output.
- **`version.py` cannot live in `tui/`.** Importing `omega_coding.tui.banner` runs
  `tui/__init__.py`, which imports Textual — ~160ms that `omega --version` and every `-p` run in a
  script would pay for a string.

**Done on 2026-09-25.** `omega-coding-agent` 0.1.0 is on PyPI, and the Trusted Publisher is
registered on PyPI's side (repository `ruseellkj/omega`, workflow `publish.yml`, environment
"(Any)" — the workflow still names `pypi`, which "(Any)" accepts). `install.sh` now installs from
the index; `OMEGA_SOURCE=git+…` still installs from git, and both were run end to end.

Self-update and version *checking* — Tau's `updater.py` (387) and `update_check.py` (379) — remain
genuinely not done.

---

### The original reasoning, for reference

Today omega runs only through `uv run` from a clone. Every
other item below is optional; this one is what "install omega" means.

### Where omega already is

More is done than it looks. `pyproject.toml` already has:

```toml
[project.scripts]
omega = "omega_coding.cli:main"      # the console entry point exists

[build-system]
requires = ["hatchling"]             # the same backend Tau uses
build-backend = "hatchling.build"
```

So `uv build` produces a working wheel today. What is missing is a **name**, a **workflow**, and a
**front door**.

### The name

`omega` is taken on PyPI (v0.4.0, *"Symbolic algorithms for solving games of infinite duration"*).
`omega-ai` is taken (v0.1.0, an ML package). **`omega-coding-agent` was claimed on 2026-09-25**,
after being checked and returning 404. The name was `omega-coding` until that day and changed to
match the Trusted Publisher registered on PyPI — a publisher is bound to one exact project name.
Tau does the same thing in reverse, publishing `tau-ai` while its package is `tau_coding`.

**The command stays `omega` regardless.** The distribution name and the console script are
different things, so nothing user-facing changes.

### The workflow

omega has **no `.github/` directory at all** today — no CI, no publish. Tau's publish workflow is
25 lines and is the whole mechanism:

```yaml
on:
  release:
    types: [published]        # a release, not every push to main
permissions:
  id-token: write             # OIDC trusted publishing, no API token in secrets
steps:
  - uses: astral-sh/setup-uv@v8.3.2
  - run: uv build
  - run: uv publish
```

The design decision worth copying is in `research/tau/dev-notes/release-process.md`: publishing is
tied to **publishing a GitHub Release**, not to merging a version bump. A bumped `pyproject.toml`
that never gets a release simply does not publish, which makes the failure mode silence rather than
an accidental upload.

### The front door

The `curl … | sh` installers are a *second* layer on top of PyPI, not a replacement for it. Three
published examples, as given:

| | One-liner |
|---|---|
| Pi | `curl -fsSL https://pi.dev/install.sh \| sh` |
| Tau | `curl -LsSf https://twotimespi.dev/install.sh \| sh` |
| Claude Code | `curl -fsSL https://claude.ai/install.sh \| bash` |

For a Python tool the script does roughly: detect OS and architecture → ensure a runtime is present
(install `uv` if missing) → `uv tool install omega-coding-agent` → put the `omega` shim on `PATH` → print
what it did. It needs somewhere to be hosted at a stable URL, which means **a domain is a
prerequisite**, not a detail. That is a real decision and it has not been made.

### Also here

- **Self-update and version reporting** — Tau's `updater.py` (387), `update_check.py` (379),
  `version.py` (16). omega has no `--version` and no idea whether it is current.

---

## 2 · Authentication and providers

| Item | Tau's module | Lines | What omega has today |
|---|---|---|---|
| OAuth | `oauth.py` + 5 more | **1,388** | **Anthropic and ChatGPT (Codex) browser sign-in** |
| Provider catalog | `provider_catalog.py` | 110 | `models.py` — built-ins, a models.dev refresh, and your `models.json` on top |
| Provider config | `provider_config.py` | **2,366** | two hardcoded adapters |

The six OAuth modules (`oauth.py`, `oauth_anthropic.py`, `oauth_device.py`,
`oauth_github_copilot.py`, `oauth_registry.py`, `oauth_types.py`) are worth naming individually
because "add OAuth" sounds like one task and is demonstrably six.

### OAuth — shipped for Anthropic, with the identity borrowed

**Updated twice, and the second update reverses the first.** `omega_coding/oauth.py` implements the
whole flow: PKCE with S256, a loopback listener that checks `state` before it keeps a code, the
code-plus-verifier exchange, and refresh — now reached from `oauth.access_token()`, which renews a
token five minutes before it lapses.

`/login anthropic` offers the browser sign-in on a fresh install. It works by presenting Claude
Code's client id, exactly as both references do. **The earlier position — mechanism here, identity
left to the operator — was honest and meant the feature worked for nobody**, because Anthropic
registers no third-party OAuth apps. Matching the references was chosen over that.

`~/.omega/oauth.json` still exists and still wins: an entry there replaces the built-in outright,
which is the path for anyone holding a registration of their own.

**What it actually costs**, measured rather than assumed:

| | |
|---|---|
| authorisation server | told omega is client `9d1c250a-…`, i.e. Claude Code |
| scope requested | includes `user:sessions:claude_code` |
| every request header | `anthropic-beta: claude-code-20250219,oauth-2025-04-20`, `x-app: cli`, `user-agent: claude-cli/omega` |
| every request body | a leading system block: `You are Claude Code, Anthropic's official CLI for Claude` |

The last row is the one worth reading twice — the borrowed registration does not stop at the login
screen, it reaches the model on every turn. Anthropic rejects the token without it
(`anthropic-messages.ts:976` calls it MUST; `provider_runtime.py:83` sets the same string).

**Update — OpenAI sign-in shipped after all.** Rushil asked for it, and the cost below was paid
rather than avoided: `omega_ai/openai_codex.py` is a separate adapter for the ChatGPT subscription
backend, at **720 lines** against the ~1,050 estimated from Tau's. The argument that follows is the
one it was reversed against, kept for the same reason finished rows are kept — a plan that
quietly loses its reasoning stops being checkable.

*What this section said before:* **OpenAI is still not offered**, and that is unchanged. The reason is not squeamishness about a
second borrowed id — it is that the id opens a different product.

```
tau/src/tau_coding/oauth.py:33      OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
tau/src/tau_ai/openai_codex.py:53   DEFAULT_OPENAI_CODEX_BASE_URL = "https://chatgpt.com/backend-api"
pi/packages/ai/src/auth/oauth/openai-codex.ts:26   const CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
pi/packages/ai/src/providers/openai-codex.ts:11    baseUrl: "https://chatgpt.com/backend-api"
```

Both references carry Codex CLI's id, character for character, and both point it at
`chatgpt.com/backend-api/codex/responses` — **your ChatGPT subscription, not your API account**.
`api.openai.com` rejects the token outright. So the wire format is different, and it needs its own
adapter rather than a header change:

| | Anthropic OAuth | OpenAI OAuth |
|---|---|---|
| whose client id | Claude Code's | Codex CLI's |
| what it reaches | the normal Messages API | the ChatGPT subscription backend |
| adapter cost | **~2 lines** — swap `x-api-key` for `Authorization` | **~1,050 lines** — a second adapter |
| identity cost | a forced `You are Claude Code` system block | — |

| | lines |
|---|---|
| `tau_ai/openai_compatible.py` (API key) | 1,240 |
| `tau_ai/openai_codex.py` (OAuth) | **1,054** |

The asymmetry in that first table is the whole decision. Anthropic's account sign-in was cheap in
code and expensive in identity; OpenAI's is expensive in both. A menu entry that succeeds at login
and then fails every request is worse than no menu entry, so it does not exist.

### The evidence the decision was made against

**Both references sign in to Anthropic as Claude Code.** Read them:

```
Tau   oauth_anthropic.py:32   ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
      oauth_anthropic.py:36   scope = "org:create_api_key user:profile user:inference
                                       user:sessions:claude_code user:mcp_servers user:file_upload"
Pi    anthropic.ts:28-29      const decode = (s: string) => atob(s)
                              const CLIENT_ID = decode("OWQxYzI1MGEtZTYxYi00NGQ5LTg4ZWQtNTk0NGQxOTYyZjVl")
```

Pi's value decodes to Tau's, character for character. That id is Claude Code's own OAuth client, and
one of the scopes is literally named `user:sessions:claude_code`. Using it means presenting a
product's credentials to an authorisation server while being a different product.

Two details make it harder to read as an oversight on their part. The scope is explicit about whose
client it is. And **Pi base64-encodes a value that is not secret** — a client id is public by
design, so encoding it buys nothing except making it harder to find by searching.

**The API-key path is unchanged and claims nothing:** `~/.omega/auth.json` at `0600`, resolution
order `file → environment variable → not signed in` (`auth.py`, reversed to follow Pi). It reaches
the documented way to authenticate to both, and carries none of the identity above. Anyone who
would rather not present someone else's registration should use it — which is why `/login` asks
rather than defaulting.

**What would still unblock the clean version:** omega registered as its own OAuth client. That is
not something a study project can grant itself — Anthropic operates no form for it — and copying
someone else's registration is not a substitute for having one. It is now copied knowingly, with
the cost written down, rather than declined.

**The catalog is the honest answer to pricing.** A hardcoded dollars-per-million table is wrong the
moment prices change, which is why omega ships token counts always and dollars only when
`OMEGA_PRICE_INPUT`/`OMEGA_PRICE_OUTPUT` are set. A catalog was added once and deliberately removed
as Tau-parity work outside the nine failures. This is where it belongs if it ever returns.

---

## 3 · Extensibility

| Item | Tau | omega today |
|---|---|---|
| Extensions | `extensions/` | none — nothing loads at runtime |
| Skills | `skills.py` (239) | none |
| Prompt templates | `prompt_templates.py` (212) | none — the deferred half of the commands work |
| Themes | `tui/themes/` (4 files) | none, and nothing to theme until Tier 3 |
| Config file | — | `.env` and `OMEGA.md` are read, but neither configures *behaviour* |

**Prompt templates are the nearest item.** The commands work shipped seven built-ins and explicitly
deferred user-defined commands until those were proven. They now are.

---

## 4 · Session surface

| Item | Tau's module | Lines |
|---|---|---|
| Export | `session_export.py` | **1,429** |
| Stats | `session_stats.py` | 119 |
| Branch summaries | `branch_summary.py` | 214 |

`session_export.py` being 1,429 lines is the useful number here: "export a conversation" sounds
trivial and is not, once HTML, Markdown and transcript fidelity are all in scope.

---

## 5 · Safety

- **Sandboxing.** The seam already shipped: `prepare` exists on the shell tool (`hooks.py`,
  `builtin_tools.py`) specifically so this lands without surgery. Today containment is the approval
  gate, which is a policy, not a boundary.

---

## 6 · The prompt box

| Item | Reference | omega today |
|---|---|---|
| Multi-line prompt | Tau: `class PromptInput(TextArea)`, `tui/app.py:476`. Pi: `tui/src/components/editor.ts`, 2,351 lines | a one-row `Input` |
| Copy and paste | Tau: `auto_copy_selection`, `tui/app.py:3677-3689`. Pi: `utils/clipboard.ts` | **done**: auto-copy on selection, `ctrl+c` copies a selection first, `clipboard.py`, `/config` |

**The multi-line prompt is what copy and paste left undone.** Pasting the same text twice now
expands its `[paste #N …]` marker into the real text. In a one-row box that text is one long line
with its breaks drawn as `↵`: correct, but not pleasant to edit. Both references have a real editor
behind the prompt. A `TextArea` would change what Enter means, how up and down reach history, and
every `on_input_*` handler in `tui/app.py`, which is why it is its own item rather than part of the
paste work.

---

## Explicitly not planned, at any tier

- **MCP** — neither reference implements it.
- **Retrieval / RAG** — same.
- **A hosted service, accounts, or telemetry** — omega is a program you run.

---

## The boundary, in one sentence

**Tier 3 finishes the architecture; this list is where omega stops being a study project.**

Nothing in this file teaches you how a coding agent works. That is precisely why it is a separate
file, and why none of it blocks Tier 3.
