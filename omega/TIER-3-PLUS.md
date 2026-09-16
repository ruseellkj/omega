# omega — Tier 3+

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

## 1 · Distribution and lifecycle

**The one the whole list hangs off.** Today omega runs only through `uv run` from a clone. Every
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
`omega-ai` is taken (v0.1.0, an ML package). **`omega-coding` is unclaimed** — checked, returns 404
— and it matches the top-level package name, exactly as Tau publishes `tau-ai` while its package is
`tau_coding`.

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
(install `uv` if missing) → `uv tool install omega-coding` → put the `omega` shim on `PATH` → print
what it did. It needs somewhere to be hosted at a stable URL, which means **a domain is a
prerequisite**, not a detail. That is a real decision and it has not been made.

### Also here

- **Self-update and version reporting** — Tau's `updater.py` (387), `update_check.py` (379),
  `version.py` (16). omega has no `--version` and no idea whether it is current.

---

## 2 · Authentication and providers

| Item | Tau's module | Lines | What omega has today |
|---|---|---|---|
| OAuth | `oauth.py` + 5 more | **1,388** | API keys from `.env` only |
| Provider catalog | `provider_catalog.py` | 110 | nothing |
| Provider config | `provider_config.py` | **2,366** | two hardcoded adapters |

The six OAuth modules (`oauth.py`, `oauth_anthropic.py`, `oauth_device.py`,
`oauth_github_copilot.py`, `oauth_registry.py`, `oauth_types.py`) are worth naming individually
because "add OAuth" sounds like one task and is demonstrably six.

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

## Explicitly not planned, at any tier

- **MCP** — neither reference implements it.
- **Retrieval / RAG** — same.
- **A hosted service, accounts, or telemetry** — omega is a program you run.

---

## The boundary, in one sentence

**Tier 3 finishes the architecture; Tier 3+ is where omega stops being a study project.**

Nothing in this file teaches you how a coding agent works. That is precisely why it is a separate
file, and why none of it blocks Tier 3.
