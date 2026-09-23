import type { Metadata } from "next";
import { Reveal } from "@/components/site/interactive";
import { GUTTER, PageHeader } from "@/components/site/primitives";
import { measured } from "@/lib/content";

export const metadata: Metadata = {
  title: "Lessons — omega",
  description: "What building omega in tiers actually taught, one note per thing.",
};

/**
 * Tau's dev-notes are the model: one short note per thing learned, written
 * after it happened, kept honest. Every figure here is measured — the sources
 * are TIER-1.md, TIER-2.md and TIER-3.md.
 */
const LESSONS = [
  {
    title: "Three estimates ran low. The fourth did not.",
    body: "Tier 1 was predicted at ~700 lines and came in at 1,577. Tier 2 predicted 3,400–3,800 and came in at 4,654. The direction never varied, so it stopped being noise and became a correction factor: assume any estimate in these notes is 1.3–2× low. Tier 3 applied it to a naive ~4,000, forecast 9,000–12,000, and came in at 8,699 — the first estimate in the project not to run low. The factor was the useful part, not the guess.",
  },
  {
    title: "The tripwire fired, and it was right.",
    body: "The rule was that loop.py must not pass ~250 lines. Adding the between-turns queues took it to 249. Rather than raise the limit, tool dispatch moved to its own file and the loop came back to 190. The rule did not block the feature — it identified which part of the file had stopped being the loop.",
  },
  {
    title: "Tests grew almost three times faster than source.",
    body: "Source ×3.0, tests ×8.5. Most of the difference is adversarial: symlinked parents, `..` smuggled back through a rebuilt path, indented secrets, a failure arriving after output. Each is three lines of fix behind twenty lines of test proving the fix matters.",
  },
  {
    title: "The second provider was the exam, not a feature.",
    body: "Chat Completions is a genuinely different wire format. Adding it changed no file above Layer 1. That is the only real evidence the abstraction was worth having — before that it was a claim.",
  },
  {
    title: "An unanswered tool call is permanent.",
    body: "Interrupt while a tool runs and the transcript holds a request with no reply. Providers reject that conversation outright, every time, forever. It costs nothing while sessions live in memory and becomes data corruption the day you persist them — which is why cancellation and persistence had to ship in the same tier.",
  },
  {
    title: "Write the fake before the real adapter.",
    body: "A fake provider is not a testing convenience, it is a design check. If it is hard to write, the interface is too big. Writing it first is also why the whole suite runs offline — 713 tests today, no key, no network.",
  },
] as const;

export default function LessonsPage() {
  return (
    <section className={`py-16 md:py-20 ${GUTTER}`}>
      <PageHeader
        eyebrow="lessons"
        title="What it actually taught."
        lede="Written after the fact, kept honest. Where a number appears it was measured — including the ones that made the plan look wrong."
      />

      {/* The measurements the lessons are drawn from. */}
      <Reveal delay={60}>
        <div className="mt-14 grid gap-x-12 gap-y-8 border-t-2 border-rule-strong pt-7 md:grid-cols-12">
          <div className="md:col-span-5">
            <h2 className="m-0 max-w-[18ch] text-2xl leading-snug">Tier 1 to Tier 2, measured.</h2>
            <p className="mt-4 max-w-[44ch] text-ink-muted">
              Tests grew almost three times faster than source. That ratio is the single most useful
              number here — it is what safety actually costs. The last column is the work since
              Tier 2 closed, where the only figure that did not move is the loop.
            </p>
          </div>

          <div className="overflow-x-auto md:col-span-7">
            <table className="w-full border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-rule-strong">
                  <th className="label py-3 pr-6 font-medium text-ink-muted">&nbsp;</th>
                  <th className="label py-3 pr-6 font-medium text-ink-muted">Tier 1</th>
                  <th className="label py-3 pr-6 font-medium text-oxblood">Tier 2</th>
                  <th className="label py-3 pr-6 font-medium text-ink-muted">×</th>
                  <th className="label py-3 font-medium text-ink-muted">Today</th>
                </tr>
              </thead>
              <tbody>
                {measured.map((m) => {
                  const a = Number(m.tier1.replace(/,/g, ""));
                  const b = Number(m.tier2.replace(/,/g, ""));
                  return (
                    <tr key={m.label} className="border-b border-rule">
                      <td className="py-2.5 pr-6 text-ink-muted">{m.label}</td>
                      <td className="tnum py-2.5 pr-6 font-mono text-ink-muted">{m.tier1}</td>
                      <td className="tnum py-2.5 pr-6 font-mono">{m.tier2}</td>
                      <td className="tnum py-2.5 pr-6 font-mono text-ink-muted">
                        {(b / a).toFixed(1)}
                      </td>
                      <td className="tnum py-2.5 font-mono text-ink-muted">{m.today}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </Reveal>

      <ol className="m-0 mt-16 grid list-none gap-0 p-0">
        {LESSONS.map((lesson, i) => (
          <Reveal key={lesson.title} delay={i * 50}>
            <li className="grid gap-x-12 gap-y-3 border-t border-rule py-8 md:grid-cols-12">
              <span className="tnum label m-0 text-oxblood md:col-span-2">
                {String(i + 1).padStart(2, "0")}
              </span>
              <h2 className="m-0 max-w-[22ch] text-2xl leading-snug md:col-span-4">
                {lesson.title}
              </h2>
              <p className="m-0 max-w-[58ch] text-ink-muted md:col-span-6">{lesson.body}</p>
            </li>
          </Reveal>
        ))}
      </ol>
    </section>
  );
}
