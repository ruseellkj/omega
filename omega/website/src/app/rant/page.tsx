import type { Metadata } from "next";
import { Reveal } from "@/components/site/interactive";
import { GUTTER, PageHeader } from "@/components/site/primitives";

export const metadata: Metadata = {
  title: "Rant — omega",
  description: "Why omega exists, and what building one from scratch is actually for.",
};

/**
 * Draft voice. The arguments come from the project's own design notes — the
 * tier contracts, the nine failures, the layering rules. Rewrite it in your
 * own words; none of this should stay in mine.
 */

/** dev-notes/03-architecture/02-beginner.md — the nine, and where each fix landed. */
const FAILURES = [
  { n: 1, name: "Context fills up", where: "L3 hook", tier: 3 },
  { n: 2, name: "One command floods it", where: "L3 tools", tier: 1 },
  { n: 3, name: "Ctrl-C bricks the transcript", where: "L2 harness", tier: 2 },
  { n: 4, name: "It deletes something", where: "L3 hook", tier: 2 },
  { n: 5, name: "A rate limit ends the run", where: "L1", tier: 2 },
  { n: 6, name: "Nothing appears until it finishes", where: "L1 → L4", tier: 1 },
  { n: 7, name: "Switching provider is a rewrite", where: "L1", tier: 1 },
  { n: 8, name: "Two edits lose data", where: "L3 tools", tier: 2 },
  { n: 9, name: "It costs more than it should", where: "L1", tier: 3 },
] as const;

const COMPARISON = [
  { aspect: "Lines", spike: "70", layered: "6,247" },
  { aspect: "Files", spike: "1", layered: "32" },
  { aspect: "The loop", spike: "the whole file", layered: "190 lines" },
  { aspect: "Add a provider", spike: "edit six places", layered: "add one file" },
  { aspect: "Interrupt it", spike: "transcript is dead", layered: "repaired on resume" },
  { aspect: "Tests", spike: "none", layered: "289, offline" },
] as const;

const SECTIONS = [
  {
    heading: "You cannot learn from a binary.",
    body: [
      "Coding agents went from novelty to daily tool in about a year, and almost all of them are closed. You can watch one work. You cannot watch it decide. The interesting part — what happens between your prompt and the diff — is exactly the part nobody ships.",
      "So the only way to understand it is to build one. Not a demo. One that runs shell commands on a real repository and has to be safe about it.",
    ],
  },
  {
    heading: "Write the contract before the code.",
    body: [
      "Each tier was specified before it was built — what would have to be true when it closed — so the work could be checked against a commitment instead of described after the fact. The file was then corrected in place wherever reality disagreed.",
      "That is why the notes still record the estimates that were wrong. Three times running they were 1.3–2× low. A document that only remembers its correct predictions is marketing.",
    ],
  },
  {
    heading: "Small is a constraint, not a virtue.",
    body: [
      "loop.py must not pass 250 lines. It hit 249, and the response was not to raise the limit — it was to notice that tool dispatch had stopped being the loop, and move it out.",
      "A rule you are willing to enforce against your own convenience is worth having. One you quietly relax is decoration.",
    ],
  },
] as const;

export default function RantPage() {
  return (
    <section className={`py-16 md:py-20 ${GUTTER}`}>
      <PageHeader
        eyebrow="rant"
        title="Why build one at all?"
        lede="There are better agents than this one. That was never the point."
      />

      {/* ── the spike, and what it costs ─────────────────────── */}
      <Reveal delay={60}>
        <div className="mt-16 grid gap-x-12 gap-y-8 border-t-2 border-rule-strong pt-7 md:grid-cols-12">
          <div className="md:col-span-5">
            <h2 className="m-0 max-w-[20ch] text-2xl leading-snug">
              The loop is easy. Everything else is the work.
            </h2>
            <p className="mt-4 max-w-[46ch] text-ink-muted">
              Ask a model, run what it asks for, report back, repeat. Seventy lines and it genuinely
              works — which is exactly what makes it misleading.
            </p>
          </div>

          <div className="overflow-x-auto md:col-span-7">
            <table className="w-full border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-rule-strong">
                  <th className="label py-3 pr-6 font-medium text-ink-muted">&nbsp;</th>
                  <th className="label py-3 pr-6 font-medium text-ink-muted">the spike</th>
                  <th className="label py-3 font-medium text-oxblood">layered</th>
                </tr>
              </thead>
              <tbody>
                {COMPARISON.map((row) => (
                  <tr key={row.aspect} className="border-b border-rule">
                    <td className="py-2.5 pr-6 text-ink-muted">{row.aspect}</td>
                    <td className="tnum py-2.5 pr-6 font-mono text-ink-muted">{row.spike}</td>
                    <td className="tnum py-2.5 font-mono">{row.layered}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Reveal>

      {/* ── the nine ─────────────────────────────────────────── */}
      <Reveal delay={90}>
        <div className="mt-16 border-t-2 border-rule-strong pt-7">
          <h2 className="m-0 max-w-[24ch] text-2xl leading-snug">
            Point it at something real and it fails in nine specific ways.
          </h2>
          <p className="mt-4 max-w-[56ch] text-ink-muted">
            Not one of the fixes belongs in the loop. That is the whole architectural argument, and
            you only believe it after you have felt the failures yourself.
          </p>

          <ol className="m-0 mt-9 grid list-none gap-x-8 gap-y-0 p-0 sm:grid-cols-2 lg:grid-cols-3">
            {FAILURES.map((f) => (
              <li
                key={f.n}
                className="grid grid-cols-[auto_1fr] items-baseline gap-3.5 border-b border-rule py-3.5"
              >
                <span className="tnum label text-oxblood">{String(f.n).padStart(2, "0")}</span>
                <span>
                  <span className="block leading-snug">{f.name}</span>
                  <span className="block text-sm text-ink-muted">
                    fixed at {f.where} &middot; Tier {f.tier}
                  </span>
                </span>
              </li>
            ))}
          </ol>
        </div>
      </Reveal>

      {/* ── prose ────────────────────────────────────────────── */}
      <div className="mt-16 grid gap-14">
        {SECTIONS.map((s, i) => (
          <Reveal key={s.heading} delay={i * 60}>
            <article className="grid gap-x-12 gap-y-4 border-t border-rule-strong pt-7 md:grid-cols-12">
              <h2 className="m-0 max-w-[20ch] text-2xl leading-snug md:col-span-5">{s.heading}</h2>
              <div className="grid gap-4 md:col-span-7">
                {s.body.map((p) => (
                  <p key={p.slice(0, 24)} className="m-0 max-w-[62ch] text-ink-muted">
                    {p}
                  </p>
                ))}
              </div>
            </article>
          </Reveal>
        ))}
      </div>

      {/* ── the closing box ──────────────────────────────────── */}
      <Reveal delay={60}>
        <div className="mt-20 border border-rule-strong bg-paper-raised px-8 py-10 md:px-12 md:py-12">
          <div className="grid gap-3">
            {["read the whole thing", "keep the loop small", "put the hard parts around it"].map(
              (line, i) => (
                <p
                  key={line}
                  className="m-0 flex items-baseline gap-4 font-serif text-2xl md:text-3xl"
                >
                  <span aria-hidden="true" className="text-oxblood">
                    {i === 0 ? "Ω" : "›"}
                  </span>
                  <span>{line}</span>
                </p>
              ),
            )}
          </div>
        </div>
      </Reveal>
    </section>
  );
}
