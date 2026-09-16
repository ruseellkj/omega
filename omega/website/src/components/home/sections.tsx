import { Eyebrow, RuleCard, Section, StatusLabel } from "@/components/site/primitives";
import { claims, layers, providers, timeline } from "@/lib/content";

/* ── the loop ─────────────────────────────────────────────────── */

const STEPS = [
  { label: "Ask", detail: "the transcript, the tools, the standing instructions" },
  { label: "It asks back", detail: "read this file, run this command" },
  { label: "Do it", detail: "checked, budgeted, approved if it matters" },
  { label: "Report", detail: "worked or failed — both are just text" },
] as const;

/**
 * A full-width band, deliberately not another split. Three earlier attempts —
 * a circle, a railed card, the source itself — were all the hero's shape with
 * something else on the right, and a repeated layout is invisible whatever sits
 * inside it.
 */
export function Loop() {
  return (
    <Section>
      <Eyebrow>the loop</Eyebrow>
      <h2 className="m-0 max-w-[24ch] text-3xl md:text-[2.5rem] md:leading-[1.15]">
        Four steps, on repeat. That is the entire agent.
      </h2>

      <div className="mt-10">
        <ol className="m-0 grid list-none border border-rule bg-paper-raised p-0 md:grid-cols-4">
          {STEPS.map((s, i) => (
            <li
              key={s.label}
              className="border-b border-rule px-6 py-7 last:border-b-0 md:border-b-0 md:border-r md:last:border-r-0"
            >
              <span className="tnum label text-oxblood">{String(i + 1).padStart(2, "0")}</span>
              <h3 className="m-0 mt-3 text-xl">{s.label}</h3>
              <p className="m-0 mt-1.5 text-sm text-ink-muted">{s.detail}</p>
            </li>
          ))}
        </ol>

        <div className="flex flex-col gap-2 border-x border-b border-rule px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
          <span className="flex items-baseline gap-2.5 text-sm text-ink-muted">
            <span aria-hidden="true" className="text-oxblood">
              &#8635;
            </span>
            Repeat for as long as it keeps asking.
          </span>
          <span className="flex items-baseline gap-2.5 text-sm">
            <span className="label text-forest">stop</span>
            <span className="text-ink-muted">it stops asking</span>
          </span>
        </div>
      </div>
    </Section>
  );
}

/* ── the boundary ─────────────────────────────────────────────── */

const PARTS = [
  {
    role: "the brain",
    symbol: "Harness",
    line: "Owns the transcript, the queues and cancellation. Has never heard of a file or a terminal.",
  },
  {
    role: "the environment",
    symbol: "build_tools()",
    line: "Tools, approvals, path checks, secret redaction. Everything that touches your machine.",
  },
  {
    role: "the face",
    symbol: "cli · headless",
    line: "Two frontends already, because neither can call the loop — both only subscribe to events.",
  },
] as const;

export function BoundarySection() {
  return (
    <Section>
      <Eyebrow>the boundary</Eyebrow>
      <h2 className="m-0 max-w-[26ch] text-3xl md:text-[2.5rem] md:leading-[1.15]">
        Separate the brain, the environment, and the face.
      </h2>
      <p className="mt-5 max-w-[56ch] text-lg text-ink-muted">
        Each one is a package, and each is defined as much by what it is forbidden to know.
      </p>

      <div className="mt-10 grid gap-x-10 gap-y-8 md:grid-cols-3">
        {PARTS.map((p) => (
          <div key={p.symbol} className="border-t-2 border-oxblood pt-5">
            <h3 className="m-0 font-serif text-2xl">{p.role}</h3>
            <p className="m-0 mt-1 font-mono text-xs text-ink-muted">{p.symbol}</p>
            <p className="m-0 mt-4 text-ink-muted">{p.line}</p>
          </div>
        ))}
      </div>

      <div className="mt-10 overflow-x-auto border border-rule bg-term px-6 py-6">
        <pre className="m-0 font-mono text-[13px] leading-[1.95] text-ink">
          <code>
            <span className="text-ink-muted">omega — design split</span>
            {"\n\n"}
            <span className="text-oxblood">Harness</span>
            {`         reusable agent brain
`}
            <span className="text-oxblood">build_tools()</span>
            {`   coding-agent environment
`}
            <span className="text-oxblood">cli · headless</span>
            {`  two possible frontends

`}
            <span className="text-ink-muted">dependency direction</span>
            {"\n"}
            {`omega_coding `}
            <span className="text-forest">&#8594;</span>
            {` omega_agent `}
            <span className="text-forest">&#8594;</span>
            {` omega_ai`}
          </code>
        </pre>
      </div>
    </Section>
  );
}

/* ── what the layering bought ─────────────────────────────────── */

export function Claims() {
  return (
    <Section>
      <Eyebrow>what the layering bought</Eyebrow>
      <ul className="m-0 grid list-none gap-x-10 gap-y-9 p-0 md:grid-cols-3">
        {claims.map((c, i) => (
          <li key={c.title} className="border-t border-rule-strong pt-5">
            <span className="tnum label text-ink-muted">{String(i + 1).padStart(2, "0")}</span>
            <h3 className="m-0 mt-3 text-xl leading-snug">{c.title}</h3>
            <p className="m-0 mt-3 text-sm text-ink-muted">{c.body}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}

/* ── the stack ────────────────────────────────────────────────── */

function DownArrow() {
  return (
    <div aria-hidden="true" className="flex justify-center py-1.5">
      <svg width="11" height="20" viewBox="0 0 11 20" className="text-rule-strong">
        <line x1="5.5" y1="0" x2="5.5" y2="14" stroke="currentColor" strokeWidth="1" />
        <path d="M1.5 12 L5.5 18 L9.5 12" fill="none" stroke="currentColor" strokeWidth="1" />
      </svg>
    </div>
  );
}

export function Stack() {
  return (
    <Section>
      <Eyebrow>the stack</Eyebrow>
      <div className="grid gap-x-12 gap-y-8 md:grid-cols-12">
        <div className="md:col-span-5">
          <h2 className="mt-0 text-3xl">Dependencies point one way.</h2>
          <p className="mt-5 max-w-[42ch] text-ink-muted">
            An arrow means <em>this layer knows the other exists</em> — knowledge, not data. Nothing
            above Layer&nbsp;2 can reach in and change the loop.
          </p>
        </div>

        <ol className="m-0 list-none p-0 md:col-span-7">
          {layers.map((layer, i) => (
            <li key={layer.n}>
              <div className="grid grid-cols-[auto_1fr_auto] items-baseline gap-4 border border-rule bg-paper-raised px-5 py-4">
                <span className="tnum label text-oxblood">L{layer.n}</span>
                <div>
                  <h3 className="m-0 text-xl">{layer.name}</h3>
                  <p className="m-0 mt-1 text-sm text-ink-muted">{layer.detail}</p>
                </div>
                <StatusLabel state={layer.state} />
              </div>
              {i < layers.length - 1 && <DownArrow />}
            </li>
          ))}
        </ol>
      </div>
    </Section>
  );
}

/* ── two providers ────────────────────────────────────────────── */

export function Providers() {
  return (
    <Section>
      <Eyebrow>two providers, one interface</Eyebrow>
      <div className="grid gap-10 md:grid-cols-2">
        {providers.map((p) => (
          <RuleCard key={p.name} title={p.name} meta={p.file}>
            {p.detail}
          </RuleCard>
        ))}
      </div>
    </Section>
  );
}

/* ── where it is ──────────────────────────────────────────────── */

export function Timeline() {
  return (
    <Section>
      <Eyebrow>where it is</Eyebrow>
      <div>
        {timeline.map((t, i) => (
          <div
            key={t.tier}
            className={`grid gap-x-8 gap-y-2 py-6 md:grid-cols-12 ${
              i < timeline.length - 1 ? "border-b border-rule" : ""
            }`}
          >
            <div className="flex items-baseline gap-3 md:col-span-3">
              <h3 className="m-0 font-serif text-2xl">{t.tier}</h3>
              <StatusLabel state={t.status} />
            </div>
            <p className="m-0 text-xl md:col-span-9">{t.headline}</p>
          </div>
        ))}
      </div>
      <a
        href="/roadmap"
        className="label mt-8 inline-block cursor-pointer border-b border-rule-strong pb-0.5 text-ink transition-colors duration-200 hover:border-oxblood hover:text-oxblood"
      >
        The full roadmap
      </a>
    </Section>
  );
}

/* ── the origin ───────────────────────────────────────────────── */

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
    line: "Python. A coding agent small enough to read like a textbook, with the reasoning kept in the open.",
  },
] as const;

export function Origin() {
  return (
    <Section last>
      <Eyebrow>the origin</Eyebrow>
      <h2 className="m-0 max-w-[26ch] text-3xl md:text-[2.5rem] md:leading-[1.15]">
        Inspired by Pi and Tau, written as a Python learning path.
      </h2>
      <p className="mt-6 max-w-[58ch] text-lg text-ink-muted">
        Pi is the exemplar and Tau is the Python mirror. Omega shares no code with either — it was
        read, then rebuilt from scratch, because the only way to understand a coding agent is to
        write one.
      </p>

      <div className="mt-10 grid gap-x-10 gap-y-8 sm:grid-cols-2">
        {ORIGINS.map((o) => (
          <a
            key={o.name}
            href={o.href}
            className="group cursor-pointer border-t-2 border-rule-strong pt-5 no-underline transition-colors duration-200 hover:border-oxblood"
          >
            <h3 className="m-0 font-serif text-2xl transition-colors duration-200 group-hover:text-oxblood">
              {o.name}
            </h3>
            <p className="m-0 mt-2 text-ink-muted">{o.line}</p>
            <span className="label mt-3 inline-block text-ink-muted">{o.host}</span>
          </a>
        ))}
      </div>
    </Section>
  );
}
