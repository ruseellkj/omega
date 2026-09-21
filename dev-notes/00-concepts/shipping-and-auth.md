# Shipping it, and signing in

Phase 3 was about one question: **how does a program on your machine get onto
someone else's?** Phase 2 answered a smaller one that turns out to be tangled
with it — *how does it know who you are?* Both are here, because the answers only
make sense together: an installed omega has no `.env` beside it and no clone to
read, so distribution is what forced the credential store.

---

## 0 · The sentence the rest hangs off

> **A distribution name, a command name, and a repository are three different
> things, and every confusing part of this is a place where two of them get
> mixed up.**

| | Kind of thing | omega's | Tau's | Pi's |
|---|---|---|---|---|
| **distribution** | a name in an index | `omega-coding` | `tau-ai` | `pi-coding-agent` |
| **command** | a file on your `PATH` | `omega` | `tau` | `pi` |
| **repository** | where the source lives | `rushil-searce/cli-agent` | huggingface/tau | earendil-works/pi |

`omega` the command has nothing to do with `omega` the PyPI name — which is why
it is fine that the latter belongs to someone else's games library.

---

## 1 · `curl … | sh` — what that one line actually does

### It is two programs, not one

`curl` and `sh` are separate processes joined by a pipe. `curl` fetches bytes and
writes them to stdout; `sh` reads stdin and executes it. Nothing is saved to disk
in between — `sh` is reading the download as it arrives.

So "creating the installer" is two independent jobs:

1. **Write a shell script** — `omega/website/public/install.sh`.
2. **Make one URL return it.** Next.js copies `public/` into the static build
   verbatim, and Vercel serves that directory. Measured after a build:

   ```
   $ ls -l out/install.sh
   -rwxr-xr-x  4124  out/install.sh
   ```

   No route, no handler, no server. **The file's path is the URL.**

The flags are all about failing loudly: `-f` exits non-zero on a 404 instead of
piping an HTML error page into your shell, `-sS` hides the progress bar but keeps
errors, `-L` follows redirects.

### What the script does, in order

```
find uv  ──not found──▶  install uv from astral.sh  ──▶  find uv again
   │                                                          │
   └──found──────────────────────────────────────────────────┬─┘
                                                             ▼
                                            uv tool install <source>
                                                             ▼
                                     ask uv where the shim went
                                                             ▼
                                     run `omega --version` to prove it works
                                                             ▼
                                     is that directory on PATH?  say so if not
```

**Why bootstrap `uv`.** omega needs Python 3.14, which almost nothing ships yet.
uv downloads an interpreter itself, so the script need not care what Python the
machine has — or whether it has one.

**Why `uv tool install` and not `pip install`.** It builds a private virtual
environment and puts a single shim on `PATH`. `pip install --user` drops omega's
dependencies into a shared site-packages where the next tool you install can
conflict with them.

### The bug worth keeping

```sh
tool_bin=$(NO_COLOR=1 "$uv_bin" tool dir --bin)
```

`NO_COLOR=1` is not tidiness. **uv writes that path with ANSI colour codes even
through a pipe** — measured:

```
$ uv tool dir --bin | od -c | head -1
0000000  033   [   3   6   m   /   U   s   e   r   s   /   r   u   s   h
```

Without it, `tool_bin` holds `\033[36m/path\033[39m`, every test on it fails, and
the script reports a missing shim it had *just installed successfully*. The first
run did exactly that. Tau's installer has the same shape and would hit it on a uv
new enough to colour that output.

**The transferable rule:** any `$(command)` whose output you will compare, join or
test needs `NO_COLOR=1` when the command is a modern CLI. They increasingly
colour by capability rather than by whether stdout is a terminal.

### What it deliberately does not do

**It never edits `~/.zshrc`.** uv owns `PATH`; the script only *reports* when the
bin directory is not on it. A tool that appends to your shell rc is one you cannot
cleanly uninstall, and you would not find out until something else broke.

---

## 2 · PyPI — measured, and whether it is needed

```
PyPI omega-coding   HTTP 404          ← not published; the name is free
PyPI omega          HTTP 200  0.4.0   ← taken, unrelated games library
PyPI tau-ai         HTTP 200  0.4.4   ← Tau is published
npm  pi-coding-agent          0.73.1  ← Pi is published, on npm not PyPI
```

**No, omega-coding is not on PyPI.** Both references are published on their
language's index.

### Does omega work like a package already?

Yes — everything except the index entry. Verified end to end:

```
$ uv tool install <local tree>
Installed 1 executable: omega
$ omega --version
omega 0.1.0
$ python -c "from importlib.metadata import version; print(version('omega-coding'))"
0.1.0
```

That is the whole of "being a package". PyPI adds **one** thing: resolution by
name.

### Is it needed?

**No, and the installer proves it** — it installs from git today:

```sh
OMEGA_SOURCE="${OMEGA_SOURCE:-git+https://github.com/rushil-searce/cli-agent#subdirectory=omega}"
```

| | git install | PyPI install |
|---|---|---|
| works now | **yes** | no |
| needs a build toolchain | yes, builds on the machine | no, downloads a wheel |
| speed | slower — clone, then build | faster |
| pinning | a tag or commit | `omega-coding==0.2.0` |
| corporate proxies, mirrors | usually blocked | usually allowed |
| `uv tool upgrade` | re-clones | resolves properly |

**So PyPI is a convenience, not a requirement — but it is the convenience that
makes upgrades and pinning work.** For a project one person installs, git is
genuinely sufficient. The reason to claim the name anyway is that names are
first-come, and `omega` is already gone.

**What it is NOT:** publishing does not make omega reviewed, trustworthy or
stable. Anyone can publish anything. It moves a name into an index.

---

## 3 · The two workflows, in plain terms

### What a workflow is

A **workflow** is a YAML file in `.github/workflows/`. GitHub reads it, and when
something happens in the repository it rents a fresh computer, runs your commands
on it, and throws it away. That is the whole idea.

| Term | What it is | omega's |
|---|---|---|
| **workflow** | a file saying when and what to run | `ci.yml`, `publish.yml` |
| **trigger** (`on:`) | the event that starts it | a push, or a published release |
| **job** | steps on one machine | `gates`, `publish` |
| **runner** | the rented machine | `ubuntu-latest` |
| **step** | one command, or one reusable action | `uv run pytest -q` |
| **action** | someone else's packaged step | `astral-sh/setup-uv@v5` |
| **OIDC** | how a job proves who it is | below |

**A workflow is not a build system.** It does not know how to test omega. It runs
the same four commands you run by hand, on a machine that has never seen your
laptop's state — which is the only thing it adds, and it is the whole point: it
catches "works on my machine".

### Why there are two

```
        you push a commit                    you publish a GitHub Release
                │                                        │
                ▼                                        ▼
        ┌───────────────┐                        ┌────────────────┐
        │    ci.yml     │                        │  publish.yml   │
        │ sync --locked │                        │ sync --locked  │
        │ pytest        │                        │ pytest         │
        │ mypy --strict │                        │ mypy --strict  │
        │ ruff check    │                        │ ruff check     │
        │ evals         │                        │ evals          │
        └───────────────┘                        │ uv build       │
          green / red on                         │ uv publish ────┼──▶ PyPI
          the commit and PR                      └────────────────┘
```

**Their correlation is that `publish.yml` repeats `ci.yml`'s gates before
uploading.** That looks like duplication and is not:

- `ci.yml` answers *"is main healthy?"* — cheap, constant, and its answer is a
  tick you can ignore.
- `publish.yml` answers *"is this specific thing safe to make permanent?"* —
  because **a PyPI release can be yanked but never replaced.** 0.1.0 is 0.1.0
  forever. So the gates run again against the exact commit being shipped, not
  against whatever was green last week.

With only `ci.yml` you could publish broken code by tagging a commit that never
ran it. With only `publish.yml` you would not learn anything was wrong until it
was too late to be cheap.

### OIDC, and why no password exists anywhere

The old way was a PyPI API token in the repository's secrets. That token is a
password: it does not expire, it works from anywhere, and anyone who can add a
workflow can print it.

**Trusted Publishing** inverts it. PyPI is told once, through its web UI:

> the repository `rushil-searce/cli-agent`, workflow `publish.yml`, environment
> `pypi`, may publish `omega-coding`

At publish time GitHub mints a short-lived signed statement — *"this job really is
that workflow in that repository"* — and uv exchanges it for a token that lives
minutes. That statement is the OIDC token; `id-token: write` is permission to
request one.

```yaml
permissions:
  contents: read      # read the code
  id-token: write     # ask GitHub to vouch for this job
environment: pypi     # the name PyPI was told to trust
```

**The property that matters:** there is no long-lived credential to leak, and a
fork cannot publish, because a fork is a different repository and the statement
says which one it is.

---

## 4 · `uv run omega` → `omega`, and what a release sets in motion

### The switch is one table in one file

```toml
[project.scripts]
omega = "omega_coding.cli:main"
```

That line has been there since Tier 1. It says: *when this package is installed,
create an executable called `omega` that imports `omega_coding.cli` and calls
`main()`.*

What differs is who reads it:

| | What runs | Where Python comes from | Needs the clone? |
|---|---|---|---|
| `uv run omega` | the same entry point | the project's `.venv` | **yes** — resolved from `pyproject.toml` in the current directory |
| `omega` | the same entry point | a private venv uv made for the tool | no |

**Nothing about omega changed to enable this.** `uv run` needs to be inside the
project because that is how it finds the environment; the installed shim has the
interpreter baked into its first line:

```
$ head -1 ~/.local/bin/omega
#!/Users/…/.local/share/uv/tools/omega-coding/bin/python
```

That is the entire mechanism — a three-line Python file with an absolute
interpreter path, which is why it works from any directory.

### What a release sets in motion

```
1.  edit pyproject.toml   version = "0.2.0"
2.  commit + push          ──▶ ci.yml runs the four gates       (nothing published)
3.  create a git tag       v0.2.0
4.  publish a Release      ──▶ publish.yml fires
                                ├─ the four gates, again
                                ├─ uv build   → dist/*.whl, dist/*.tar.gz
                                └─ uv publish → PyPI, via OIDC
5.  (nothing)              install.sh needs no change — it names no version
```

**Step 5 is the one people expect to exist and it does not.** The installer asks
for `omega-coding` with no version, so a new release is picked up by the next
person who runs it. Nothing is redeployed or regenerated.

Three things must agree, and only two are checked automatically:

| Must agree | Checked by |
|---|---|
| `pyproject.toml` version and the git tag | **nobody** — this is the one you can get wrong |
| `pyproject.toml` and `uv.lock` | `uv sync --locked`, in both workflows |
| the code and the four gates | both workflows |

The decision worth naming, copied from Tau: **the trigger is a published Release,
not a push to main.** A version bump that never gets a release simply does not
publish. The failure mode is silence rather than an accidental upload, and
silence is recoverable.

---

## 5 · Signing in

### omega: API keys, and OAuth refused rather than pending

```
resolution order:   environment variable  →  ~/.omega/auth.json  →  not signed in
```

```json
{ "anthropic": { "kind": "apikey", "key": "sk-ant-REDACTED" } }
```

File `0600`, directory `0700`, written atomically. `/login` stores, `/logout`
removes.

**Environment first, always.** `.env` files are loaded into the environment before
anything reads a key, so every setup that worked before still works, and a stored
credential can never silently shadow one exported on purpose. That ordering is
also what makes `/logout` honest — it says plainly it cannot remove an exported
variable, because without that line it looks broken.

### What "the key is safe" does and does not mean

Measured, not asserted. The key you paste into `/login`:

| | |
|---|---|
| shown while typing | **no** — `password=True` on the input |
| written to the session file | **no** — it never travels through the transcript |
| written to `~/.omega/logs/*.jsonl` | **no** |
| echoed back by `/login` | **no** |
| sent to the provider | **yes** — that is its job |
| **encrypted on disk** | **no. It is plain text at `0600`.** |

So the honest sentence is *"nothing omega writes will contain it, and nothing
omega displays will show it"* — not *"it cannot be read"*. Anyone who can read
your user account can read `~/.omega/auth.json`: root, a restored backup, a
synced home directory, a process running as you. There is no keyring and no
encryption, and neither reference has one either.

**And a second, sharper limit.** If a key appears in *tool output* — a tool reads
a config file, or `env` is run — masking is **pattern-based**, so it covers the
shapes `redact.py` knows and nothing else. Measured, with a tool genuinely
reading a file:

```
anthropic  sk-ant-…      → {"key": "[redacted Anthropic API key]"}   masked
openai     sk-proj-…     → {"key": "[redacted OpenAI API key]"}      masked
google     AIza…         → {"key": "[redacted Google API key]"}      masked
groq       gsk_…         → {"key": "gsk_BBBBBBBB…"                   NOT masked
xai        xai-…         → {"key": "xai-DDDDDDDD…"                   NOT masked
```

That matters because `--base-url` points omega at Groq, Together and xAI, whose
key shapes are not in the list. **This is a real gap, not a theoretical one**, and
the fix is a line per provider in `_PATTERNS` — recorded here rather than done,
because guessing at shapes nobody has tested is how a pattern list gets a false
sense of completeness.

### How the references do it

| | Store | Mode | Keyring | OAuth |
|---|---|---|---|---|
| **Tau** | `~/.tau/credentials.json` (`credentials.py:151`) | `0600`, atomic | none | PKCE + loopback `:53692` |
| **Pi** | `~/.pi/agent/auth.json` (`config.ts:535`) | `0600`, dir `0700`, lockfile | none | same, same port |
| **Claude Code** | closed source — **unknown** | unknown | unknown | it *is* the first party |
| **omega** | `~/.omega/auth.json` | `0600`, dir `0700`, atomic | none | **declined** |

Neither reference uses a keyring; neither touches a shell rc. Two independent
implementations landing on the same shape is the strongest signal available.

`/login` and `/logout` exist in both, and both carry the caveat omega now carries.
Tau's wording: *"/logout only removes credentials saved by /login; environment
variables and providers.json config are unchanged."*

### Why omega declines OAuth

```
Tau   oauth_anthropic.py:32   ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
      oauth_anthropic.py:36   scope = "… user:sessions:claude_code …"
Pi    anthropic.ts:28-29      const CLIENT_ID = decode("OWQxYzI1MGEt…")   ← the same id
```

Pi's base64 decodes to Tau's, character for character. **That is Claude Code's own
OAuth client id**, and one scope is literally named for it. Using it means
presenting a product's credentials to an authorisation server while being a
different product.

Two details make it hard to read as an accident: the scope names whose client it
is, and **Pi encodes a value that is not secret** — a client id is public by
design, so encoding it accomplishes nothing except making it harder to grep for.

**What would unblock it:** omega registered as its own OAuth client with the
provider. A study project cannot grant itself that, and copying someone else's
registration is not a substitute. `PRODUCT-BACKLOG.md` §2 records it as declined.

### The provider is no longer a flag

`--provider` used to default to `"anthropic"`, so plain `omega` always tried
Anthropic and an OpenAI user typed `--provider openai` every time — **a flag whose
value was already sitting in their credentials file.**

```
$ omega --sessions        # signed in to nothing
omega (not signed in - run /login)

$ omega --sessions        # signed in to openai only
omega (gpt-5 via openai)

$ omega --sessions        # signed in to both
omega (claude-sonnet-5)               ← ENV_VARS order breaks the tie

$ omega --provider openai --sessions  # the override still wins
omega (gpt-5 via openai)
```

`auth.choose_provider` decides, and it is re-asked on every call rather than
captured once — after `/login openai` the answer changes, and a value read at
startup would still say "signed in to nothing".

---

## 6 · The approval gap, once more — and it is no longer in the TUI

| Surface | Can it ask? | Without `--yes` |
|---|---|---|
| **TUI** | yes — a `ModalScreen` (`tui/approval.py`) | asks, and waits |
| **REPL** | yes — `input()` on a thread | asks, and waits |
| **print (`-p`)** | **no** | **declines everything** |

### What was broken, and is not now

`--tui` used to *require* `--yes`. The approval asker called `input()`, Textual
paints over that, so the first tool call hung on a prompt nobody could see; the
old code refused to start rather than hang.

Defensible while the TUI was opt-in. It stopped being defensible the moment the
TUI became the **default**, because `--yes` means *approve every write and every
shell command without asking*. Flipping the default with that still in place
would have turned a deliberate opt-in into a silent one. So the modal was built
first — that is why Phase 1 started with something invisible.

### What remains, measured

```
$ omega --fake -p "write to target.txt"
note: running without --yes, so tools that change anything will be declined -
      there is no one to ask.
  allow? [y]es / [a]lways / [N]o:   no input available - declining.
```

**Fail-safe, not fail-open** — `_ask_in_terminal` reads EOF and returns `"deny"`,
and `ApprovalPolicy` denies when it has no asker at all (`approval.py:319`).
Nothing is approved by accident.

But `-p` cannot change anything unless you pass `--yes`, which is why the warning
prints up front rather than letting the model discover it one denial at a time.

**Why there is no third asker.** A pipe has nobody in it. The options are a policy
file saying what is allowed without asking, or `--yes`. A policy file is config,
omega has none, and inventing one here would be building the general case before
there is a second instance of it. The backlog entry reads *"an approval modal for
--yes-free print mode"*, seam: *"there is no one to ask in a pipe"* — which is the
truthful entry rather than a plan.
