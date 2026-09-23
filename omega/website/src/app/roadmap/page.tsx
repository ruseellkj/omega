import type { Metadata } from "next";
import { Reveal } from "@/components/site/interactive";
import { GUTTER, PageHeader } from "@/components/site/primitives";

export const metadata: Metadata = {
  title: "Roadmap — omega",
  description: "What shipped, what is next, and what is deliberately never happening.",
};

type State = "shipped" | "next" | "planned";

const MARK: Record<State, { glyph: string; word: string; tone: string }> = {
  shipped: { glyph: "✓", word: "shipped", tone: "text-forest" },
  next: { glyph: "●", word: "up next", tone: "text-oxblood" },
  planned: { glyph: "○", word: "planned", tone: "text-ink-muted" },
};

/** Every entry is transcribed from TIER-1.md, TIER-2.md and TIER-3.md. Nothing aspirational. */
const TIERS: {
  tier: string;
  status: State;
  verdict: string;
  groups: { name: string; items: { name: string; note: string; state: State }[] }[];
}[] = [
  {
    tier: "Tier 3",
    status: "shipped",
    verdict: "Survives a task long enough to fill the context window.",
    groups: [
      {
        name: "The two failures it closed",
        items: [
          { name: "Compaction", note: "summarise the old prefix, keep the recent tail", state: "shipped" },
          { name: "Prompt caching", note: "four breakpoints on a byte-stable prefix", state: "shipped" },
        ],
      },
      {
        name: "Everything else",
        items: [
          { name: "Session branching", note: "parent_id is already on every entry", state: "shipped" },
          { name: "Search tools", note: "grep, find, ls — truncation and path resolution already exist", state: "shipped" },
          { name: "A real TUI", note: "the 10 agent events are already the contract", state: "shipped" },
          { name: "Structured logging", note: "a second listener on the same event stream", state: "shipped" },
          { name: "Image reading", note: "content blocks are a discriminated union", state: "shipped" },
          { name: "Subagents or plan mode", note: "a subagent is the headless driver, called from a tool", state: "shipped" },
        ],
      },
    ],
  },
  {
    tier: "Tier 2",
    status: "shipped",
    verdict: "Safe to point at a real repository.",
    groups: [
      {
        name: "Layer 1 · Provider",
        items: [
          { name: "A second adapter", note: "OpenAI Chat Completions — the exam, not a feature", state: "shipped" },
          { name: "Retry with backoff", note: "below the event boundary, so the loop never sees it", state: "shipped" },
          { name: "Auth resolver callback", note: "resolved per request, so a token can refresh mid-session", state: "shipped" },
        ],
      },
      {
        name: "Layer 2 · Agent core",
        items: [
          { name: "The 10 agent events", note: "a separate file from the 12 stream events", state: "shipped" },
          { name: "The harness", note: "owns messages, so persistence and queues have somewhere to live", state: "shipped" },
          { name: "Hook bundle", note: "before/after tool call, convert, transform, two queue sources", state: "shipped" },
          { name: "Cancellation", note: "Tier 1 threaded the signal everywhere and created none", state: "shipped" },
          { name: "Orphaned tool-result repair", note: "runs on interrupt and again on resume", state: "shipped" },
          { name: "Steering and follow-up queues", note: "type while it works; queue the next task", state: "shipped" },
          { name: "Context gauge and cost", note: "measures failure #1 without fixing it", state: "shipped" },
        ],
      },
      {
        name: "Layer 3 · Coding app",
        items: [
          { name: "edit tool", note: "exact-match replace, unique match required", state: "shipped" },
          { name: "Path resolution", note: "one resolver, symlinks resolved before the inside/outside verdict", state: "shipped" },
          { name: "Per-path write lock", note: "keyed on the resolved path, shared by write and edit", state: "shipped" },
          { name: "Approval gate", note: "asks about writes, the shell, and anything outside the working directory", state: "shipped" },
          { name: "Secret redaction", note: "key-shaped strings never reach the model or a log", state: "shipped" },
          { name: "Sessions", note: "append-only JSONL under ~/.omega, migrate-on-read, -c to continue", state: "shipped" },
        ],
      },
      {
        name: "Interface",
        items: [
          { name: "Headless driver", note: "prompt in, messages out — also the benchmark interface", state: "shipped" },
          { name: "Smoke eval", note: "not a test: checks the assembled agent still does something", state: "shipped" },
        ],
      },
    ],
  },
  {
    tier: "Tier 1",
    status: "shipped",
    verdict: "A working agent with real layers.",
    groups: [
      {
        name: "The foundation",
        items: [
          { name: "Provider interface", note: "one method, owned by the core, not the adapter", state: "shipped" },
          { name: "12 stream events", note: "one start, one terminal, errors as events", state: "shipped" },
          { name: "The loop", note: "stops on content, not on stop_reason", state: "shipped" },
          { name: "Three tools", note: "read, write, run_shell — errors as data", state: "shipped" },
          { name: "Output truncation", note: "keep the tail, say what was dropped, spill the rest", state: "shipped" },
          { name: "Turn limit", note: "one line, and the first thing worth adding", state: "shipped" },
        ],
      },
    ],
  },
];

const NEVER = [
  { name: "MCP", note: "neither reference has it" },
  { name: "Retrieval / RAG", note: "the model reads files; that is the retrieval" },
] as const;

export default function RoadmapPage() {
  const all = TIERS.flatMap((t) => t.groups.flatMap((g) => g.items));
  const counts = {
    shipped: all.filter((i) => i.state === "shipped").length,
    next: all.filter((i) => i.state === "next").length,
    planned: all.filter((i) => i.state === "planned").length,
  };

  return (
    <section className={`py-16 md:py-20 ${GUTTER}`}>
      <PageHeader eyebrow="roadmap" title="Three tiers closed.">
        <div className="mt-8 flex flex-wrap items-baseline gap-x-8 gap-y-3">
          {(["shipped", "next", "planned"] as const).map((k) => (
            <span key={k} className="flex items-baseline gap-2.5">
              <span aria-hidden="true" className={`text-sm ${MARK[k].tone}`}>
                {MARK[k].glyph}
              </span>
              <span className="tnum font-serif text-2xl">{counts[k]}</span>
              <span className="label text-ink-muted">{MARK[k].word}</span>
            </span>
          ))}
        </div>

        <p className="mt-8 max-w-[56ch] text-lg text-ink-muted">
          Each tier was written as a contract before the code, then corrected in place wherever
          reality disagreed. The estimates ran low three times running, and the notes still say so
          — the Tier 3 estimate was the first that did not, landing at 8,699 lines against a 9,000–12,000
          forecast.
        </p>
      </PageHeader>

      <div className="mt-16 grid gap-16">
        {TIERS.map((tier, ti) => (
          <Reveal key={tier.tier} delay={ti * 60}>
            <div className="border-t-2 border-rule-strong pt-7">
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <h2 className="m-0 font-serif text-3xl">{tier.tier}</h2>
                <span className={`label ${MARK[tier.status].tone}`}>{MARK[tier.status].word}</span>
              </div>
              <p className="m-0 mt-2 max-w-[52ch] text-xl text-ink-muted">{tier.verdict}</p>

              <div className="mt-9 grid gap-9">
                {tier.groups.map((group) => (
                  <div key={group.name} className="grid gap-x-12 gap-y-4 md:grid-cols-12">
                    <h3 className="label m-0 pt-1 text-oxblood md:col-span-3">{group.name}</h3>
                    <ul className="m-0 grid list-none gap-3 p-0 md:col-span-9">
                      {group.items.map((item) => (
                        <li
                          key={item.name}
                          className="grid grid-cols-[auto_1fr] items-baseline gap-3.5 border-b border-rule pb-3 last:border-0"
                        >
                          <span aria-hidden="true" className={`text-xs ${MARK[item.state].tone}`}>
                            {MARK[item.state].glyph}
                          </span>
                          <span>
                            <span className="leading-snug">
                              {item.name}
                              <span className="sr-only"> — {MARK[item.state].word}</span>
                            </span>
                            <span className="block text-sm text-ink-muted">{item.note}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </div>
          </Reveal>
        ))}

        <Reveal>
          <div className="border-t-2 border-rule-strong pt-7">
            <h2 className="m-0 font-serif text-3xl">Not happening</h2>
            <p className="m-0 mt-2 max-w-[52ch] text-xl text-ink-muted">
              Scope is defined as much by refusals as by plans.
            </p>
            <ul className="m-0 mt-7 grid list-none gap-3 p-0 sm:grid-cols-2">
              {NEVER.map((n) => (
                <li key={n.name} className="border-t border-rule pt-3">
                  <span className="leading-snug line-through decoration-rule-strong">{n.name}</span>
                  <span className="block text-sm text-ink-muted">{n.note}</span>
                </li>
              ))}
            </ul>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
