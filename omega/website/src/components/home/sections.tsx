import Link from "next/link";
import { LoopSteps } from "@/components/home/loop-steps";
import { SessionSnapshot } from "@/components/home/session-snapshot";
import { InstallBox } from "@/components/site/install-box";
import { ExternalArrow, Eyebrow, Section, StatusLabel } from "@/components/site/primitives";
import { Terminal } from "@/components/site/terminal";
import { features, measured, site, timeline } from "@/lib/content";

/** The one link style every section closes with, so "read more" always looks the same. */
function MoreLink({ href, children }: { href: string; children: React.ReactNode }) {
  const external = href.startsWith("http");
  const className =
    "label inline-block cursor-pointer border-b border-rule-strong pb-0.5 text-ink transition-colors duration-200 hover:border-oxblood hover:text-oxblood";
  return external ? (
    <a href={href} className={className}>
      {children}
      <ExternalArrow className="ml-1.5" />
    </a>
  ) : (
    <Link href={href} className={className}>
      {children}
    </Link>
  );
}

/* ── what it does ─────────────────────────────────────────────── */

/**
 * The question a visitor asks once they have seen a turn: *what else does it do
 * for me?* Until this section existed the page answered how omega is built
 * before it said what it is for.
 *
 * Text, not terminals. Each item names the one command or flag that reaches the
 * feature, set as inline code, so the commands are discoverable without six
 * more dark boxes competing with the one real terminal above. The full list
 * lives in the docs, which is where the link goes.
 */
export function Features() {
  return (
    <Section>
      <Eyebrow>what it does</Eyebrow>
      <h2 className="m-0 text-3xl md:max-w-[24ch] md:text-[2.5rem] md:leading-[1.15]">
        What you get when you run it.
      </h2>

      <ul className="m-0 mt-10 grid list-none gap-x-10 gap-y-9 p-0 sm:grid-cols-2 lg:grid-cols-3">
        {features.map((f) => (
          <li key={f.title} className="border-t border-rule-strong pt-5">
            <h3 className="m-0 text-xl leading-snug">{f.title}</h3>
            <p className="m-0 mt-2 text-ink-muted">{f.body}</p>
            <code className="mt-3 inline-block rounded-[4px] bg-term px-1.5 py-0.5 font-mono text-[13px] text-oxblood">
              {f.cmd}
            </code>
          </li>
        ))}
      </ul>

      <div className="mt-10">
        <MoreLink href="/docs#run">Every command, flag and key</MoreLink>
      </div>
    </Section>
  );
}

/* ── how it works ─────────────────────────────────────────────── */

const STEPS = [
  { label: "Ask", detail: "the transcript, the tools, the standing instructions" },
  { label: "It asks back", detail: "read this file, run this command" },
  { label: "Do it", detail: "checked, budgeted, approved if it matters" },
  { label: "Report", detail: "worked or failed — both are just text" },
] as const;

/**
 * Two sides that say the same thing twice, on purpose: the steps in words on
 * the left, one real turn on the right. The turn is the steps, performed — the
 * question is step one, the two tool rows are steps two and three, the answer
 * is step four — so the terminal is evidence for the list beside it rather
 * than a picture floating on its own.
 *
 * It follows the hero directly because "what does using it look like" is the
 * next thing a visitor wants once they know what it is.
 */
export function Loop() {
  return (
    <Section id="how-it-works">
      <div className="grid gap-x-12 gap-y-12 lg:grid-cols-12 lg:items-center">
        <div className="lg:col-span-5">
          <Eyebrow>how it works</Eyebrow>
          <h2 className="m-0 text-3xl md:text-[2.5rem] md:leading-[1.15]">
            Four steps, on repeat. That is the entire agent.
          </h2>
          <div className="mt-9">
            <LoopSteps steps={STEPS} />
          </div>
          <p className="m-0 mt-8 flex flex-wrap items-baseline gap-x-2.5 gap-y-1 text-sm text-ink-muted">
            <span aria-hidden="true" className="text-oxblood">
              &#8635;
            </span>
            Repeat for as long as it keeps asking;
            <span className="label text-forest">stop</span>
            when it stops.
          </p>
        </div>

        <div className="min-w-0 lg:col-span-7">
          <SessionSnapshot />
        </div>
      </div>
    </Section>
  );
}

/* ── how it's built ───────────────────────────────────────────── */

/**
 * CLAUDE.md's architecture table, verbatim in meaning: what each package knows
 * about. One framing only — the page used to describe the same code three ways
 * (brain / environment / face, then L1–L4, then two providers), and a reader
 * meeting three decompositions of one system cannot tell they are the same.
 */
const PACKAGES = [
  ["omega_agent", "messages, events, tools, turns, sessions"],
  ["omega_ai", "one vendor's wire format each"],
  ["omega_coding", "files, shells, policy, the screen"],
] as const;

/**
 * Tau's layout for the same idea (research/tau/website/layouts/index.html, "the
 * boundary"): the argument beside one terminal. Mirrored from "how it works" —
 * terminal left, text right on wide screens — so the two terminal sections
 * alternate sides instead of repeating one shape. On a phone the text comes
 * first in both, because the argument is what makes the terminal legible.
 */
export function Architecture() {
  return (
    <Section>
      <Eyebrow>how it&apos;s built</Eyebrow>
      <div className="grid items-center gap-x-12 gap-y-10 lg:grid-cols-12">
        <div className="lg:order-2 lg:col-span-5">
          <h2 className="m-0 text-3xl md:text-[2.5rem] md:leading-[1.15]">
            Three packages. Arrows point one way.
          </h2>
          <p className="mt-5 text-lg text-ink-muted md:max-w-[46ch]">
            Each package is defined as much by what it may not know. The loop lives in{" "}
            <code className="font-mono text-[0.85em] text-ink">omega_agent</code> and has never heard
            of a file, a vendor or a terminal — which is why a second wire format went in without the
            provider interface moving.
          </p>
          <div className="mt-6">
            <MoreLink href={`${site.repo}/blob/main/dev-notes/03-architecture/04-boundaries-and-layout.md`}>
              The boundaries, in depth
            </MoreLink>
          </div>
        </div>

        <div className="min-w-0 lg:order-1 lg:col-span-7">
          {/* Rows rather than one <pre>: the longest line is too wide for a
              phone, and a grid keeps the names aligned while each description
              wraps under itself. */}
          <Terminal title="omega — design split" bodyClassName="px-5 py-5 text-[12px] sm:text-[13px] md:px-6">
            <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
              {PACKAGES.map(([name, knows]) => (
                <div key={name} className="contents">
                  <dt className="whitespace-nowrap text-shell-key">{name}</dt>
                  <dd className="m-0">= {knows}</dd>
                </div>
              ))}
            </dl>
            <p className="m-0 mt-5 text-shell-dim">dependency direction — who imports whom</p>
            <p className="m-0 whitespace-nowrap text-white">omega_coding → omega_ai → omega_agent</p>
            <p className="m-0 whitespace-nowrap text-white">omega_coding → omega_agent</p>
            <p className="m-0 mt-5 text-shell-dim">
              tests/test_layers.py parses every import and fails if one points up
            </p>
          </Terminal>
        </div>
      </div>
    </Section>
  );
}

/* ── measured, not claimed ────────────────────────────────────── */

const today = (label: string) => measured.find((m) => m.label === label)?.today ?? "—";
const tier = (label: string, t: "tier1" | "tier2") => measured.find((m) => m.label === label)?.[t] ?? "—";

/**
 * Each figure with the one sentence that makes it mean something. These used to
 * be two sections — a strip of bare numbers under the hero and a "what the
 * layering bought" list further down — that stated the same three facts twice.
 *
 * It sits after the architecture on purpose: "190 lines in the loop" is a
 * number before you have seen the loop and evidence after. The hero's headline
 * states the claim; this is where it is backed.
 *
 * Figures come from `measured`, the same rows the lessons table reads.
 */
const PROOF = [
  {
    value: today("loop.py"),
    label: "lines in the loop",
    note: "It reached 249 while the between-turns queues went in. Tool dispatch moved to its own file rather than the limit of 250 moving.",
  },
  {
    value: today("Tests"),
    label: "tests, no network",
    note: "Every provider call is faked at the interface, which is why the fake adapter was written before the real one.",
  },
  {
    value: today("Source lines"),
    label: "lines of source",
    note: `Up from ${tier("Source lines", "tier1")} at Tier 1 and ${tier("Source lines", "tier2")} at Tier 2. The loop has not grown since.`,
  },
  {
    value: "3",
    label: "wire formats, one interface",
    note: "Anthropic Messages, OpenAI Chat Completions, and the Responses API behind a ChatGPT subscription.",
  },
] as const;

/**
 * Left padding per cell, because the column a cell starts in changes with the
 * grid: one column on a phone, two at `sm`, four from `lg`. A cell that opens a
 * row sits flush with the gutter; one that follows a divider gets clear of it.
 */
const INSET = ["sm:pl-0", "sm:pl-6", "sm:pl-0 lg:pl-6", "sm:pl-6"] as const;

export function Proof() {
  return (
    <Section>
      <Eyebrow>measured, not claimed</Eyebrow>
      <h2 className="m-0 text-3xl md:max-w-[26ch] md:text-[2.5rem] md:leading-[1.15]">
        Four numbers, and what each one proves.
      </h2>
      <dl className="m-0 mt-10 grid gap-px bg-rule sm:grid-cols-2 lg:grid-cols-4">
        {PROOF.map((p, i) => (
          <div key={p.label} className={`flex flex-col bg-paper py-6 pr-4 sm:py-2 ${INSET[i]}`}>
            {/* dt first for the markup, the figure first on screen. */}
            <dt className="label order-2 mt-3 text-oxblood">{p.label}</dt>
            <dd className="tnum order-1 m-0 font-serif text-5xl leading-none text-ink">{p.value}</dd>
            <dd className="order-3 m-0 mt-2 text-pretty text-sm text-ink-muted">{p.note}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-10">
        <MoreLink href="/lessons">What building it taught</MoreLink>
      </div>
    </Section>
  );
}

/* ── how it got here ──────────────────────────────────────────── */

const ORIGINS = [
  {
    name: "Pi",
    href: "https://pi.dev",
    host: "pi.dev",
    line: "TypeScript. The exemplar — a minimal agent harness you adapt to your workflow.",
  },
  {
    name: "Tau",
    href: "https://twotimespi.dev",
    host: "twotimespi.dev",
    line: "Python. A coding agent small enough to read like a textbook.",
  },
] as const;

/**
 * Where it is and where it came from, as one section: both are the project's
 * history, and apart they were two more stops on an already long page. The
 * timeline takes the wide column because it is the part that changes.
 */
export function Story() {
  return (
    <Section>
      <Eyebrow>how it got here</Eyebrow>
      <h2 className="m-0 text-3xl md:max-w-[26ch] md:text-[2.5rem] md:leading-[1.15]">
        Built one tier at a time, from two references.
      </h2>

      <div className="mt-10 grid gap-x-12 gap-y-12 lg:grid-cols-12">
        <div className="lg:col-span-7">
          <ol className="m-0 list-none p-0">
            {timeline.map((t) => (
              <li
                key={t.tier}
                className="grid gap-x-6 gap-y-1 border-b border-rule py-4 first:pt-0 sm:grid-cols-[14rem_minmax(0,1fr)]"
              >
                <span className="flex items-baseline gap-3">
                  <span className="font-serif text-xl">{t.tier}</span>
                  <StatusLabel state={t.status} />
                </span>
                <span className="text-ink-muted">{t.headline}</span>
              </li>
            ))}
          </ol>
          <div className="mt-6">
            <MoreLink href="/roadmap">The full roadmap</MoreLink>
          </div>
        </div>

        <div className="lg:col-span-5">
          <p className="label m-0 text-ink-muted">where it came from</p>
          <ul className="m-0 mt-4 grid list-none gap-6 p-0">
            {ORIGINS.map((o) => (
              <li key={o.name}>
                <a href={o.href} className="group cursor-pointer no-underline">
                  <span className="font-serif text-xl transition-colors duration-200 group-hover:text-oxblood">
                    {o.name}
                  </span>
                  <span className="label ml-3 text-ink-muted">{o.host}</span>
                  <ExternalArrow className="ml-1 text-ink-muted" />
                </a>
                <p className="m-0 mt-1 text-ink-muted">{o.line}</p>
              </li>
            ))}
          </ul>
          <p className="m-0 mt-6 text-sm text-ink-muted">
            omega shares no code with either. Both were read, then it was written from scratch —
            the only way to understand a coding agent is to write one.
          </p>
        </div>
      </div>
    </Section>
  );
}

/* ── the close ────────────────────────────────────────────────── */

/**
 * The install box again, at the bottom, for the reader who scrolled the whole
 * page and arrives having decided. Tau closes the same way (the `closing`
 * section of research/tau/website/layouts/index.html).
 *
 * Twice on the page and no more: once where a visitor first decides (the hero),
 * once where a reader finishes. A third copy mid-page would interrupt the
 * explanation it sits inside. No second big Ω either — the footer directly
 * below already sets one.
 */
export function Closing() {
  return (
    <Section last className="md:pt-20">
      <div className="mx-auto max-w-2xl sm:text-center">
        <Eyebrow>start here</Eyebrow>
        <h2 className="m-0 text-3xl md:text-[2.5rem] md:leading-[1.15]">
          Read it end to end. Then run it.
        </h2>
        <p className="mt-4 text-lg text-ink-muted sm:mx-auto sm:max-w-[46ch]">
          One line installs it, and{" "}
          <code className="font-mono text-[0.9em] text-oxblood">omega --fake</code> needs no key, no
          network and no credits.
        </p>
        <InstallBox centered className="mt-9 sm:mx-auto sm:max-w-xl sm:text-left" />
        <div className="mt-8 flex flex-wrap items-center gap-x-6 gap-y-3 sm:justify-center">
          <Link
            href="/docs"
            className="label cursor-pointer border-b border-oxblood pb-0.5 text-oxblood transition-colors duration-200 hover:border-ink hover:text-ink"
          >
            Read the docs →
          </Link>
          <Link
            href="/roadmap"
            className="label cursor-pointer border-b border-rule-strong pb-0.5 text-ink transition-colors duration-200 hover:border-oxblood hover:text-oxblood"
          >
            The roadmap
          </Link>
        </div>
      </div>
    </Section>
  );
}
