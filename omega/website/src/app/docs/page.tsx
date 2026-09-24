import type { Metadata } from "next";
import Link from "next/link";
import { InstallBox } from "@/components/site/install-box";
import { Reveal } from "@/components/site/interactive";
import { ExternalArrow, GUTTER, PageHeader } from "@/components/site/primitives";
import { CommandLine, Terminal } from "@/components/site/terminal";
import { commands, keys, sessionCommands, site } from "@/lib/content";

export const metadata: Metadata = {
  title: "Docs — omega",
  description:
    "Everything omega has, section by section — and an honest list of what it does not.",
};

const BLOB = `${site.repo}/blob/main`;
const TREE = `${site.repo}/tree/main`;

type Item = { name: string; href: string; where?: string; note: string };

/**
 * Organised the way a documentation site is, rather than as a reading order —
 * but every entry points at something that exists today. Nothing is listed as
 * a placeholder; what omega lacks is in NOT_YET below instead, which is the
 * more useful half of the page.
 */
const SECTIONS: { title: string; lede: string; items: Item[] }[] = [
  {
    title: "Start here",
    lede: "Three files, in this order. About an hour.",
    items: [
      {
        name: "What is a coding agent?",
        href: `${BLOB}/dev-notes/03-architecture/01-plain.md`,
        where: "dev-notes/03-architecture/01-plain.md",
        note: "No jargon. The whole shape in fifteen minutes.",
      },
      {
        name: "The seventy-line version",
        href: `${BLOB}/dev-notes/03-architecture/02-beginner.md`,
        where: "dev-notes/03-architecture/02-beginner.md",
        note: "An agent that genuinely works, and the nine specific ways it breaks on a real repository.",
      },
      {
        name: "Quickstart",
        href: `${BLOB}/README.md`,
        where: "README.md",
        note: "One line to install, or clone it and run it. `omega --fake` needs no key, no network and no credits.",
      },
    ],
  },
  {
    title: "Guides",
    lede: "The seven things you can actually do with it today.",
    items: [
      {
        name: "The interactive session",
        href: `${BLOB}/omega/src/omega_coding/cli.py`,
        where: "omega_coding/cli.py",
        note: "The prompt loop, plus the two between-turns queues: steer it mid-task, or line up what comes next.",
      },
      {
        name: "Commands in the conversation",
        href: `${BLOB}/omega/src/omega_coding/commands.py`,
        where: "omega_coding/commands.py",
        note: "A leading / addresses the program, a leading ! addresses the shell. Thirteen commands plus !cmd — which runs through the same run_shell tool the model uses, so it meets the same approval gate.",
      },
      {
        name: "Sessions",
        href: `${TREE}/omega/src/omega_agent/session`,
        where: "omega_agent/session/",
        note: "Append-only JSONL under ~/.omega/sessions, migrated on read. Continue the last one with -c, a specific one with --resume, and list them with --sessions.",
      },
      {
        name: "Providers and models",
        href: `${TREE}/omega/src/omega_ai`,
        where: "omega_ai/",
        note: "Anthropic Messages, OpenAI Chat Completions, and a ChatGPT subscription over the Responses API. --base-url points OpenAI at Groq, Together, Ollama or vLLM. /model switches within a provider; every window was read from models.dev.",
      },
      {
        name: "Approvals and safety",
        href: `${BLOB}/omega/src/omega_coding/approval.py`,
        where: "omega_coding/approval.py",
        note: "It asks before changing anything, remembers the answer, and refuses a short list outright — which --yes does not skip.",
      },
      {
        name: "Headless and scripting",
        href: `${BLOB}/omega/src/omega_coding/headless.py`,
        where: "omega_coding/headless.py",
        note: "Prompt in, messages out. The same entry point the smoke eval drives.",
      },
      {
        name: "Watching the context",
        href: `${BLOB}/omega/src/omega_coding/context.py`,
        where: "omega_coding/context.py",
        note: "A gauge, a cost meter, and a breakdown of what fills the window — system, messages, tools, free. It says so when a window is a fallback rather than a known figure.",
      },
    ],
  },
  {
    title: "Reference",
    lede: "Look these up as you hit them.",
    items: [
      {
        name: "CLI flags",
        href: `${BLOB}/omega/src/omega_coding/cli.py`,
        where: "omega_coding/cli.py",
        note: "--fake, --yes, --confine, -c/--continue, --resume, --sessions, --tui, --repl, -p/--print, --no-log, --no-save, --provider, --base-url, --model, --max-turns, --context-window, --compact-threshold, --version. That is all of them.",
      },
      {
        name: "Built-in tools",
        href: `${BLOB}/omega/src/omega_coding/builtin_tools.py`,
        where: "omega_coding/builtin_tools.py",
        note: "read_file, write_file, edit_file, read_image, list_files, find_files, search_files, run_shell — eight, plus run_subagent, wired in cli.py. Every one returns its errors as data.",
      },
      {
        name: "Folder trees",
        href: `${BLOB}/dev-notes/04-folder-trees.md`,
        where: "dev-notes/04-folder-trees.md",
        note: "How the five layers map onto three packages.",
      },
      {
        name: "Glossary",
        href: `${BLOB}/dev-notes/04-glossary.md`,
        where: "dev-notes/04-glossary.md",
        note: "Every term the notes use, defined once.",
      },
    ],
  },
  {
    title: "How omega works",
    lede: "The design, and the arguments behind it.",
    items: [
      {
        name: "Architecture overview",
        href: `${BLOB}/dev-notes/03-architecture/03-production.md`,
        where: "dev-notes/03-architecture/03-production.md",
        note: "The same system as the seventy-line version, with all nine failures fixed, and the vocabulary for each fix.",
      },
      {
        name: "Boundaries and layout",
        href: `${BLOB}/dev-notes/03-architecture/04-boundaries-and-layout.md`,
        where: "dev-notes/03-architecture/04-boundaries-and-layout.md",
        note: "The four boundaries, and the rule that arrows only point down.",
      },
      {
        name: "The loop and its events",
        href: `${BLOB}/omega/src/omega_agent/loop.py`,
        where: "omega_agent/loop.py",
        note: "190 lines, against a limit of 250. Twelve stream events below it, ten agent events above.",
      },
      {
        name: "Teardown, one file per layer",
        href: `${TREE}/dev-notes/01-teardown`,
        where: "dev-notes/01-teardown/",
        note: "Six files. Read one when you build that layer — not before.",
      },
      {
        name: "Anatomy",
        href: `${BLOB}/dev-notes/00-concepts/anatomy.md`,
        where: "dev-notes/00-concepts/anatomy.md",
        note: "Forty-two components, tiered. The summary table is the part to read.",
      },
      {
        name: "Security",
        href: `${BLOB}/dev-notes/00-concepts/security.md`,
        where: "dev-notes/00-concepts/security.md",
        note: "Because it runs shell commands on your machine.",
      },
    ],
  },
  {
    title: "The tier contracts",
    lede: "Each written before the code, then corrected where reality disagreed.",
    items: [
      {
        name: "Tier 1 — the loop works",
        href: `${BLOB}/omega/TIER-1.md`,
        where: "omega/TIER-1.md",
        note: "What it had, what it lacked, and where Tier 2 put each gap.",
      },
      {
        name: "Tier 2 — safe on a real repository",
        href: `${BLOB}/omega/TIER-2.md`,
        where: "omega/TIER-2.md",
        note: "Including the estimates that were wrong, which are still in the file.",
      },
      {
        name: "Tier 3 — survives a long task",
        href: `${BLOB}/omega/TIER-3.md`,
        where: "omega/TIER-3.md",
        note: "Written before the code, like the two before it. Compaction, prompt caching, and a Textual TUI — with the seam each one plugs into, and the single row whose seam does not exist yet.",
      },
      {
        name: "The product backlog",
        href: `${BLOB}/omega/PRODUCT-BACKLOG.md`,
        where: "omega/PRODUCT-BACKLOG.md",
        note: "What shipped and what is left: distribution, sign-in and the model catalog are done; extensions, session surface and safety are open. Every entry names the Tau module that proves the category is real work rather than a guess.",
      },
      {
        name: "Roadmap",
        href: "/roadmap",
        note: "All three tiers, and what each one closed.",
      },
      {
        name: "Releases",
        href: "/releases",
        note: "Every tag, with the measured size of the code at each one.",
      },
    ],
  },
];

/**
 * The honest half. Each of these is something a mature agent has and omega does
 * not — named so the gap is countable rather than vague.
 */
const NOT_YET: { name: string; note: string; when: string }[] = [
  {
    name: "A settings file",
    note: "Only the theme persists, to ~/.omega/tui.json, and models to ~/.omega/models.json. Nothing else is configurable.",
    when: "backlog",
  },
  { name: "Skills and prompt templates", note: "No reusable prompt library loads at runtime.", when: "backlog" },
  {
    name: "Extensions",
    note: "Nothing loads at runtime. Themes shipped, but as four JSON files, not a plugin point.",
    when: "backlog",
  },
  {
    name: "Plan mode",
    note: "Subagents shipped as run_subagent; a read-only planning mode did not.",
    when: "unplanned",
  },
];

function ItemLink({ item }: { item: Item }) {
  const internal = item.href.startsWith("/");
  const heading = (
    <h3 className="m-0 text-xl transition-colors duration-200 group-hover:text-oxblood">
      {item.name}
      {!internal && (
        <ExternalArrow className="ml-1.5 text-ink-muted transition-colors duration-200 group-hover:text-oxblood" />
      )}
    </h3>
  );

  return (
    <li>
      {internal ? (
        <Link href={item.href} className="group inline-block cursor-pointer no-underline">
          {heading}
        </Link>
      ) : (
        <a href={item.href} className="group inline-block cursor-pointer no-underline">
          {heading}
        </a>
      )}
      <p className="m-0 mt-1 max-w-[62ch] text-ink-muted">{item.note}</p>
      {item.where && <p className="m-0 mt-1 font-mono text-xs text-ink-muted/70">{item.where}</p>}
    </li>
  );
}

export default function DocsPage() {
  const total = SECTIONS.reduce((n, s) => n + s.items.length, 0);

  return (
    <section className={`py-16 md:py-20 ${GUTTER}`}>
      <PageHeader
        eyebrow="docs"
        title="Everything omega has."
        lede="The notes live in the repository as Markdown, so they cannot drift from the code. This page is the map — organised by what you would want to do, not by directory."
      >
        <div className="mt-8 flex flex-wrap items-baseline gap-x-8 gap-y-3">
          <span className="flex items-baseline gap-2.5">
            <span className="tnum font-serif text-2xl">{total}</span>
            <span className="label text-ink-muted">entries</span>
          </span>
          <span className="flex items-baseline gap-2.5">
            <span className="tnum font-serif text-2xl">{NOT_YET.length}</span>
            <span className="label text-ink-muted">not here yet</span>
          </span>
        </div>
      </PageHeader>

      <div className="mt-16 grid gap-x-12 gap-y-14">
        {/* One line, first. Everything below it assumes omega is already
            installed, and until this section existed the page assumed a clone. */}
        <Reveal>
          <div className="grid gap-x-12 gap-y-6 border-t-2 border-rule-strong pt-7 md:grid-cols-12">
            <div className="md:col-span-3">
              <h2 className="label m-0 text-oxblood">Install</h2>
              <p className="m-0 mt-2.5 max-w-[28ch] text-sm text-ink-muted">
                Three routes, each run end to end before it went on this page. Nothing is on PyPI
                yet, so there is no <code className="font-mono">pip install</code>.
              </p>
            </div>
            {/* `min-w-0` because a grid item will not shrink below its content by
                default, and the install command is one unbreakable line: without it
                the column widens past a phone screen instead of letting the command
                scroll. Same fix as the hero's columns. */}
            <div className="min-w-0 md:col-span-9">
              <InstallBox />
              <p className="m-0 mt-3 text-sm text-ink-muted">
                Then <code className="font-mono text-oxblood">omega</code> — no provider flag.
                It uses whichever provider you are signed in to, and{" "}
                <code className="font-mono text-oxblood">/login</code> is how you sign in.
              </p>
            </div>
          </div>
        </Reveal>

        {/* The flags, as a terminal you can copy from. They were only on the
            home page before, as a list you could read and not copy. */}
        <Reveal>
          <div className="grid gap-x-12 gap-y-6 border-t-2 border-rule-strong pt-7 md:grid-cols-12">
            <div className="md:col-span-3">
              <h2 className="label m-0 text-oxblood">Run it</h2>
              <p className="m-0 mt-2.5 max-w-[28ch] text-sm text-ink-muted">
                Chosen before the conversation starts. The first is the whole command; the rest are
                for scripts, for trying it without a key, and for coming back.
              </p>
            </div>
            <div className="min-w-0 md:col-span-9">
              <Terminal title="your shell">
                {commands.map((c) => (
                  <CommandLine key={c.cmd} cmd={c.cmd} note={c.note} />
                ))}
              </Terminal>
            </div>
          </div>
        </Reveal>

        {/* The keys. A third kind of thing from the two command lists: not
            chosen before the conversation, not typed into it. */}
        <Reveal delay={50}>
          <div className="grid gap-x-12 gap-y-6 border-t-2 border-rule-strong pt-7 md:grid-cols-12">
            <div className="md:col-span-3">
              <h2 className="label m-0 text-oxblood">In the terminal UI</h2>
              <p className="m-0 mt-2.5 max-w-[28ch] text-sm text-ink-muted">
                <code className="font-mono">ctrl+c</code> stops the turn rather than the program —
                with nothing running, it takes a second press to quit.
              </p>
            </div>
            <ul className="m-0 grid list-none gap-x-10 gap-y-2.5 p-0 sm:grid-cols-2 md:col-span-9">
              {keys.map((k) => (
                <li key={k.key} className="grid grid-cols-[4.25rem_minmax(0,1fr)] items-baseline gap-x-3">
                  <kbd className="justify-self-start rounded-[3px] border border-b-2 border-rule-strong bg-paper-raised px-1.5 py-0.5 font-mono text-[12.5px] text-oxblood">
                    {k.key}
                  </kbd>
                  <span className="text-sm text-ink-muted">{k.note}</span>
                </li>
              ))}
            </ul>
          </div>
        </Reveal>

        {/* The in-session commands, spelled out rather than linked. Typing /
            in the terminal UI lists them too, but only once you are inside —
            this is the list for deciding whether to install. */}
        <Reveal>
          <div className="grid gap-x-12 gap-y-6 border-t-2 border-rule-strong pt-7 md:grid-cols-12">
            <div className="md:col-span-3">
              <h2 className="label m-0 text-oxblood">In the conversation</h2>
              <p className="m-0 mt-2.5 max-w-[28ch] text-sm text-ink-muted">
                A leading <code className="font-mono">/</code> addresses the program, a leading{" "}
                <code className="font-mono">!</code> addresses the shell. Everything else goes to
                the model.
              </p>
            </div>
            <ul className="m-0 grid list-none gap-x-10 gap-y-4 p-0 sm:grid-cols-2 md:col-span-9">
              {sessionCommands.map((c) => (
                <li key={c.cmd} className="grid content-start gap-y-1">
                  <code className="justify-self-start rounded-[3px] bg-term px-1.5 py-0.5 font-mono text-[13px] text-oxblood">
                    {c.cmd}
                  </code>
                  <span className="text-sm text-ink-muted">{c.note}</span>
                </li>
              ))}
            </ul>
          </div>
        </Reveal>

        {SECTIONS.map((section, si) => (
          <Reveal key={section.title} delay={si * 50}>
            <div className="grid gap-x-12 gap-y-6 border-t-2 border-rule-strong pt-7 md:grid-cols-12">
              <div className="md:col-span-3">
                <h2 className="label m-0 text-oxblood">{section.title}</h2>
                <p className="m-0 mt-2.5 max-w-[28ch] text-sm text-ink-muted">{section.lede}</p>
              </div>
              <ul className="m-0 grid list-none gap-6 p-0 md:col-span-9">
                {section.items.map((item) => (
                  <ItemLink key={item.name} item={item} />
                ))}
              </ul>
            </div>
          </Reveal>
        ))}

        {/* The gap, named. A docs page that lists only what exists reads as
            complete; this is the half that keeps it honest. */}
        <Reveal>
          <div className="grid gap-x-12 gap-y-6 border-t-2 border-rule-strong pt-7 md:grid-cols-12">
            <div className="md:col-span-3">
              <h2 className="label m-0 text-ink-muted">Not here yet</h2>
              <p className="m-0 mt-2.5 max-w-[28ch] text-sm text-ink-muted">
                Named so the gap is countable. Everything Tier 3 promised shipped; what is left is
                product work, tracked in the backlog or not planned.
              </p>
            </div>
            <ul className="m-0 grid list-none gap-3 p-0 sm:grid-cols-2 md:col-span-9">
              {NOT_YET.map((n) => (
                <li key={n.name} className="border-t border-rule pt-3">
                  <span className="flex flex-wrap items-baseline gap-x-2.5">
                    <span className="leading-snug text-ink-muted line-through decoration-rule-strong">
                      {n.name}
                    </span>
                    <span
                      className={`label ${n.when === "unplanned" ? "text-ink-muted/70" : "text-oxblood"}`}
                    >
                      {n.when}
                    </span>
                  </span>
                  <span className="mt-0.5 block text-sm text-ink-muted">{n.note}</span>
                </li>
              ))}
            </ul>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
