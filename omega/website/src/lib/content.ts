/**
 * Single source of truth for everything the page claims.
 *
 * Every number here is measured and every quote is attributable. Figures come
 * from omega/TIER-1.md and omega/TIER-2.md; commands come from omega/README.md.
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
  repo: "https://github.com/rushil-searce/cli-agent",
  x: "https://x.com/rushil_jariwala",
} as const;

/** dev-notes/03-architecture/01-plain.md — the one-sentence version. */
export const thesis =
  "A coding agent is a program that asks a model for help, does what the model asks for, tells it what happened, and repeats until it says it is finished.";

/** README.md, "Run it". Chosen before the conversation starts. */
export const commands = [
  { cmd: "uv run omega --fake", note: "scripted responses — no key, no network, no credits" },
  { cmd: "uv run omega", note: "Anthropic Messages" },
  { cmd: "uv run omega --provider openai", note: "OpenAI Chat Completions" },
  { cmd: "uv run omega -c", note: "continue the most recent session for this project" },
  { cmd: "uv run omega --sessions", note: "list saved sessions, newest first" },
] as const;

/**
 * README.md, "In the conversation" — a different kind of thing from the list
 * above, which is why it is a separate export rather than five more rows in it.
 * Those are chosen before the conversation starts; these work inside it.
 */
export const sessionCommands = [
  { cmd: "/help", note: "list these commands" },
  { cmd: "/sessions", note: "saved sessions for this project" },
  { cmd: "/resume <id>", note: "switch to another session, without restarting" },
  { cmd: "/clear", note: "start a fresh session; the old one is kept on disk" },
  { cmd: "/cost", note: "tokens and spend so far" },
  { cmd: "/context", note: "how full the context window is" },
  { cmd: "/exit", note: "leave omega" },
  { cmd: "!<command>", note: "run a shell command — no model, no tokens" },
] as const;

/** 03-production.md §1, 04-boundaries-and-layout.md §2. */
export const layers = [
  {
    n: 4,
    name: "Terminal UI",
    detail: "Print output today. The 10 agent events are already the contract a real TUI would use.",
    state: "partial",
  },
  {
    n: 3,
    name: "Coding app",
    detail: "Tools, approvals, path resolution, secret redaction, sessions.",
    state: "built",
  },
  {
    n: 2,
    name: "Agent core",
    detail: "The loop, the harness, the hook bundle, the between-turns queues.",
    state: "built",
  },
  {
    n: 1,
    name: "Provider",
    detail: "One interface, 12 stream events, retry swallowed below the boundary.",
    state: "built",
  },
] as const;

/** TIER-2.md, the comparison table at lines 14-19. */
export const measured = [
  { label: "Source lines", tier1: "1,577", tier2: "4,654", today: "6,247" },
  { label: "Test lines", tier1: "499", tier2: "4,257", today: "6,274" },
  { label: "Tests", tier1: "45", tier2: "289", today: "395" },
  { label: "loop.py", tier1: "151", tier2: "190", today: "190" },
] as const;

export const providers = [
  {
    name: "Anthropic Messages",
    file: "providers/anthropic.py",
    detail:
      "Content blocks, thinking blocks with signatures that must return verbatim, and tool-use ids that must be answered exactly once.",
  },
  {
    name: "OpenAI Chat Completions",
    file: "providers/openai.py",
    detail:
      "Not a feature — the exam. A genuinely different wire format, and the same adapter reaches Groq, Together, Ollama and vLLM. The format is the unit, not the vendor.",
  },
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
    status: "next" as const,
    headline: "Survives a task long enough to fill the context window.",
    body: "That is the whole of Tier 3 — and the two beginner failures still standing are the two it fixes.",
  },
] as const;

/** TIER-2.md Part 2 — "Things Tier 3 adds next". Each seam already exists. */
export const upcoming = [
  { name: "Session branching", seam: "parent_id is already on every entry" },
  { name: "Search tools", seam: "truncate_output() and paths.py both exist" },
  { name: "A real TUI", seam: "the 10 agent events are the UI contract" },
  { name: "Structured logging", seam: "a second listener on the same event stream" },
  { name: "Image reading", seam: "content blocks are a discriminated union" },
  { name: "Subagents or plan mode", seam: "a subagent is the headless driver, called from a tool" },
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

/** What the layering bought, stated as claims rather than adjectives. */
export const claims = [
  {
    title: "Adding OpenAI changed nothing above Layer 1",
    body: "Not a feature — the exam. Chat Completions is a genuinely different wire format, and the provider interface did not move. If it had, Tier 1 was wrong.",
  },
  {
    title: "The loop holds at 190 lines",
    body: "It hit 249 while the between-turns queues went in. Rather than let it grow, tool dispatch was extracted to its own file. Source has since grown by 758 lines and the loop has not moved.",
  },
  {
    title: "395 tests, none touching the network",
    body: "Every provider call is faked at the interface boundary, which is why omega_ai/fake.py was written before the real adapter. The suite runs in under three seconds with no key.",
  },
] as const;
