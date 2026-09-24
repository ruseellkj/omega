/**
 * Single source of truth for everything the page claims.
 *
 * Every number here is measured and every quote is attributable. Figures come
 * from omega/README.md and the tier documents; commands come from omega/README.md
 * and the command registry in omega_coding/commands.py.
 * If a claim is not in one of those files, it does not appear on the site.
 *
 * `measured` carries a third column because the tier figures are history. Tier 2
 * closed at 5,489 source lines; the work since is post-Tier-2 and would be a
 * different claim written into the same cell.
 */

export const site = {
  name: "omega",
  eyebrow: "A terminal coding agent",
  /** The argument, not the description. The figure is measured, and has not moved. */
  headline: "The loop is 190 lines.",
  headlineRest: "Everything else grew around it.",
  tagline: "A terminal coding agent, built from scratch in layers.",
  /**
   * Name-first, so a first-time visitor learns what omega *is* before
   * anything else on the page. Condensed from `thesis` below
   * (dev-notes/03-architecture/01-plain.md); the loop figure is the measured
   * one from TIER-2.md. Nothing here is a claim those files do not make.
   */
  definition:
    "is a Python coding agent small enough to read end to end. It asks a model for help, runs what it asks for, reports back, and repeats — until it says it is finished.",
  repo: "https://github.com/ruseellkj/omega",
  x: "https://x.com/rushil_jariwala",
} as const;

/** dev-notes/03-architecture/01-plain.md — the one-sentence version. */
export const thesis =
  "A coding agent is a program that asks a model for help, does what the model asks for, tells it what happened, and repeats until it says it is finished.";

/**
 * README.md, "Install it" — one line, and the only one a new user needs. The
 * script bootstraps `uv`, installs omega in an isolated environment, verifies
 * the command it created, and never edits a shell rc file.
 */
export const install = "curl -fsSL https://omega-coding-agent.vercel.app/install.sh | sh";

/**
 * The install box's tabs. Pi's landing page offers five ways in; omega has
 * three that actually work today, and each was run end to end into an empty
 * tool directory before it went on the page — `omega --version` answered
 * `omega 0.1.0` from all three.
 *
 * There is no `pip install` tab, because nothing is on PyPI yet. `uv` is the
 * install the script itself runs; `source` is one line, joined with `&&`, so
 * the copy button hands over something pasteable rather than four prompts.
 */
export const installMethods = [
  {
    id: "curl",
    label: "curl",
    cmd: install,
    note: "Installs uv if it is missing, then omega. Never edits a shell rc file.",
  },
  {
    id: "uv",
    label: "uv",
    cmd: 'uv tool install "git+https://github.com/ruseellkj/omega#subdirectory=omega"',
    note: "Already have uv? This is the install the script runs.",
  },
  {
    id: "source",
    label: "source",
    cmd: "git clone https://github.com/ruseellkj/omega.git && cd omega/omega && uv sync && uv run omega",
    note: "To read it, or change it. The package lives in the omega/ folder.",
  },
] as const;

/**
 * The home page's "what it does": six things a user gets, each with the one
 * command or flag that reaches it.
 *
 * Every claim was checked against the code, not against this file:
 * `DEFAULT_THRESHOLD = 0.8` in compact.py, `FILE_MODE = S_IRUSR | S_IWUSR` in
 * auth.py, `before_record` in redact.py, and the flags and commands against
 * `omega --help` and the `COMMANDS` registry in commands.py.
 */
export const features = [
  {
    title: "Asks before it acts",
    body: "Writes, shell commands and anything outside the working directory wait for your yes, and it remembers the answer. A short list it refuses outright, even with --yes.",
    cmd: "--confine",
  },
  {
    title: "Remembers every session",
    body: "Each turn is saved as it happens. Continue the last session, switch to another, or rewind to before a question, and the old branch is kept.",
    cmd: "omega -c",
  },
  {
    title: "Survives a long task",
    body: "At 80% of the context window it compacts the older turns into a summary, and prompt caching keeps the repeated prefix cheap.",
    cmd: "/compact",
  },
  {
    title: "Sign in your way",
    body: "A Claude or ChatGPT subscription in the browser, or an API key. Stored in ~/.omega/auth.json, readable by you alone.",
    cmd: "/login",
  },
  {
    title: "Any model, one interface",
    body: "Anthropic, OpenAI, or anything OpenAI-compatible through --base-url — Ollama, vLLM, Groq. Switch mid-conversation; each window comes from models.dev.",
    cmd: "/model",
  },
  {
    title: "Scriptable, and discreet",
    body: "One shot to stdout for a pipe or a script. Anything shaped like a key is masked before a message is stored.",
    cmd: 'omega -p "…"',
  },
] as const;

/**
 * README.md, "Run it". Chosen before the conversation starts.
 *
 * **`--provider` is gone from this list on purpose.** It used to be required for
 * OpenAI, defaulting to Anthropic — a flag whose value was already sitting in
 * the user's credentials file. omega now uses whichever provider you are signed
 * in to, and the flag survives only as an override.
 */
export const commands = [
  { cmd: "omega", note: "the terminal UI — this is the whole command" },
  { cmd: "omega --fake", note: "scripted responses — no key, no network, no credits" },
  { cmd: 'omega -p "fix the failing test"', note: "one shot: answer on stdout, exit — pipeable" },
  { cmd: "omega --repl", note: "the plain print/input prompt instead of the UI" },
  { cmd: "omega -c", note: "continue the most recent session for this project" },
  { cmd: "omega --sessions", note: "list saved sessions, newest first" },
  { cmd: "omega --version", note: "which omega this is — needs no credentials" },
] as const;

/**
 * The keys, which are a third kind of thing again: not chosen before the
 * conversation and not typed into it. Only meaningful in the terminal UI.
 */
export const keys = [
  { key: "ctrl+c", note: "stop the turn in progress — press twice to quit when nothing is running" },
  { key: "ctrl+d", note: "quit" },
  { key: "esc", note: "close the palette, or stop a turn and ask, or recall your last message" },
  { key: "↑ ↓", note: "walk back through what you typed — ↑ first recalls a message you queued mid-turn" },
  { key: "tab", note: "complete the highlighted command" },
  { key: "ctrl+o", note: "expand every tool row at once" },
  { key: "/", note: "open the command list, filtered as you type" },
  { key: "ctrl+q", note: "does not quit — it tells you which keys do" },
] as const;

/**
 * README.md, "In the conversation" — a different kind of thing from the list
 * above, which is why it is a separate export rather than five more rows in it.
 * Those are chosen before the conversation starts; these work inside it.
 */
export const sessionCommands = [
  { cmd: "/help", note: "list these commands" },
  { cmd: "/login [provider]", note: "sign in — a Claude or ChatGPT subscription in the browser, or an API key" },
  { cmd: "/logout [provider]", note: "remove a stored credential; exported variables are left alone" },
  { cmd: "/sessions", note: "saved sessions for this project" },
  { cmd: "/resume <id>", note: "switch to another session, without restarting" },
  { cmd: "/clear", note: "start a fresh session; the old one is kept on disk" },
  { cmd: "/rewind [n]", note: "go back before your last question; the old branch is kept" },
  { cmd: "/compact [pct]", note: "shrink the conversation now, rather than at 80%" },
  { cmd: "/theme [name]", note: "slate, oxblood-dark, oxblood-light, high-contrast" },
  { cmd: "/cost", note: "tokens and spend so far" },
  { cmd: "/context", note: "how full the window is, and what is filling it — system, messages, tools" },
  { cmd: "/model [name]", note: "switch model, keeping the conversation; the window follows it" },
  { cmd: "/model refresh", note: "fetch the current models and their windows from models.dev" },
  { cmd: "/exit", note: "leave omega" },
  { cmd: "!<command>", note: "run a shell command — no model, no tokens" },
] as const;

/** TIER-2.md, the comparison table at lines 14-19. */
export const measured = [
  { label: "Source lines", tier1: "1,577", tier2: "4,654", today: "14,226" },
  { label: "Test lines", tier1: "499", tier2: "4,257", today: "13,308" },
  { label: "Tests", tier1: "45", tier2: "289", today: "713" },
  { label: "loop.py", tier1: "151", tier2: "190", today: "190" },
] as const;

/** TIER-1.md and TIER-2.md — "The one-line summary" of each. */
export const timeline = [
  {
    tier: "Tier 1",
    status: "closed" as const,
    headline: "A working agent with real layers.",
    body: "It streams, it calls tools, it stops correctly, and its provider is swappable. Not safe, not persistent, not interruptible — and each of those is a Tier 2 addition to a seam that already existed, not a rewrite.",
  },
  {
    tier: "Tier 2",
    status: "closed" as const,
    headline: "Safe to point at a real repository.",
    body: "It can be interrupted without corruption, it remembers, it asks before it destroys, and the provider abstraction is no longer a claim but a measured result.",
  },
  {
    tier: "Tier 3",
    status: "closed" as const,
    headline: "Survives a task long enough to fill the context window, and has a face.",
    body: "Compaction and prompt caching closed the last two beginner failures. A Textual UI made steering reachable by a human rather than only by a test, and the ten agent events turned out to be the contract a real UI needed.",
  },
  {
    tier: "Beyond the tiers",
    status: "shipped" as const,
    headline: "Installable, and signed in to.",
    body: "A curl installer, CI and a release workflow. /login signs in with a Claude or ChatGPT subscription in the browser, or stores an API key in a 0600 file. The model list refreshes from models.dev. Nothing is on PyPI yet — the release workflow waits on the name being claimed.",
  },
] as const;

/**
 * PRODUCT-BACKLOG.md — what is left, now that Tier 3 has closed. Every row that
 * used to be here shipped, which is why the list is shorter and less certain:
 * these are product concerns, and the seam column is honest about the two that
 * do not have one yet.
 */
export const upcoming = [
  { name: "Claim omega-coding on PyPI", seam: "the workflow and the installer are already written" },
  { name: "Self-update and version checking", seam: "--version exists; nothing compares it to a remote" },
  { name: "Settings file", seam: "the theme already persists to ~/.omega/tui.json" },
  { name: "Extensions and skills", seam: "none yet — nothing loads at runtime" },
  { name: "Sandboxing", seam: "the prepare seam shipped in Tier 2" },
  { name: "An approval modal for --yes-free print mode", seam: "there is no one to ask in a pipe" },
] as const;

/**
 * Tags, newest first. Every figure is measured from the tag itself
 * (`git ls-tree -r <tag>`), not from the tier documents, so a row cannot drift
 * from the code it points at.
 *
 * These are git tags and GitHub Releases. They are **not** downloads — omega is
 * not on any package index yet, and the page says so rather than implying a
 * `pip install` that does not exist.
 */
export const releases = [
  {
    tag: "tier-2-final",
    date: "2026-09-16",
    title: "Commands, a shell escape, and one event loop",
    body: "Everything Tier 2 closed with, plus the work that followed it: a location-aware approval gate in place of the hard path fence, seven slash commands and a ! shell escape, and a single REPL event loop that removed a long-standing traceback.",
    lines: "6,247",
    files: "38",
    tests: "395",
  },
  {
    tag: "tier-2",
    date: "2026-08-24",
    title: "Safe to point at a real repository",
    body: "It can be interrupted without corrupting the conversation, it remembers across restarts, it asks before it destroys anything, and the provider abstraction stopped being a claim and became a measured result.",
    lines: "4,654",
    files: "32",
    tests: "289",
  },
  {
    tag: "tier-2-pre-exam",
    date: "2026-08-24",
    title: "The checkpoint before the provider exam",
    body: "The headless driver and the smoke eval, tagged deliberately before a second provider was added — so the claim that adding one changed nothing above Layer 1 could be checked against something rather than asserted.",
    lines: "4,160",
    files: "31",
    tests: "—",
  },
  {
    tag: "tier-1",
    date: "2026-08-22",
    title: "A working agent with real layers",
    body: "It streams, it calls tools, it stops correctly, and its provider is swappable. Not safe, not persistent, not interruptible — each of which became a Tier 2 addition to a seam that already existed, not a rewrite.",
    lines: "1,577",
    files: "12",
    tests: "45",
  },
] as const;
