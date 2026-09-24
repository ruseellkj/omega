import Link from "next/link";
import { Reveal } from "@/components/site/interactive";
import { InstallBox } from "@/components/site/install-box";
import { ExternalArrow, GUTTER } from "@/components/site/primitives";
import { site } from "@/lib/content";

/**
 * Graph paper across the hero, easing off only at the very end.
 *
 * The horizontals are deliberately much fainter than the verticals. A full
 * strength horizontal rule landing on a line of prose reads as a strikethrough
 * — the verticals never do, because they cross letterforms rather than run
 * along them. Same grid, no struck-out text.
 */
function GridPaper() {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 mask-[linear-gradient(to_bottom,black_0%,black_72%,transparent_100%)]"
      style={{
        backgroundImage: [
          "linear-gradient(to right, var(--rule) 1px, transparent 1px)",
          "linear-gradient(to bottom, color-mix(in srgb, var(--rule) 32%, transparent) 1px, transparent 1px)",
        ].join(", "),
        backgroundSize: "64px 64px",
      }}
    />
  );
}

/**
 * The scroll cue: a floating chevron saying there is more below.
 *
 * Real navigation, not decoration — it is a link to `#how-it-works`
 * (`Section`'s `id`, with `scroll-mt-24` so the sticky header does not cover
 * the heading it lands on), so it works with the keyboard and without JS.
 *
 * It sits inside the hero's own `relative` box, not fixed to the viewport: on
 * a wide screen the hero is exactly one screen tall (`min-h` in `Hero`), so
 * "near the bottom of the section" and "near the bottom of the fold" are the
 * same point. On a phone the hero is taller than the fold and the cue simply
 * marks the end of it, which is still where a reader wants to know more
 * follows.
 *
 * The glow is `currentColor`, not a fixed hex: the chevron's own colour
 * transitions to oxblood on hover, and the drop-shadow reads that colour back,
 * so the glow always matches what is on screen rather than a colour chosen
 * separately from it. `animate-bounce` is Tailwind's own keyframe; nothing
 * bespoke was needed for a two-pixel float.
 */
function ScrollCue() {
  return (
    <a
      href="#how-it-works"
      aria-label="Scroll to how it works"
      className="group absolute inset-x-0 bottom-5 mx-auto hidden w-fit cursor-pointer rounded-full p-2 text-ink-muted transition-colors duration-300 hover:text-oxblood motion-reduce:animate-none md:flex md:animate-bounce"
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="size-6 transition-[filter] duration-300 group-hover:drop-shadow-[0_0_9px_currentColor]"
      >
        <path d="M5 9l7 7 7-7" />
      </svg>
    </a>
  );
}

/**
 * The first screen, and only the first screen.
 *
 * ## One job per fold
 *
 * The hero answers two questions — what is this, how do I get it — and stops.
 * The previous version also started the session terminal inside the fold, so
 * the screen ended on the top edge of a window: a second focal point, cut off,
 * competing with the install command. The terminal now lives in the next
 * section, beside the steps it illustrates. `min-h` holds the fold for the
 * hero alone from `md` up; on a phone the content is already a screen tall.
 * The padding is lopsided on purpose — more below than above — so the block
 * sits a little above the optical centre, where a centred block reads as
 * sagging, and the headline lands in the upper half of the screen.
 *
 * ## The headline takes the width
 *
 * An editorial layout: the headline set large across the whole column, then an
 * asymmetric row beneath it — the pitch on the left, the one action on the
 * right. Centring everything in a narrow column wasted the width and made the
 * headline three short lines instead of two long ones. The headline and its
 * status pill are centred; the prose and the install command below stay
 * left-aligned in their columns, because centred paragraphs are slower to read
 * — every line starts somewhere new — and a headline is two lines, not a paragraph.
 */
export function Hero() {
  return (
    <section className={`relative overflow-hidden border-b border-rule ${GUTTER}`}>
      <GridPaper />

      <Reveal className="relative">
        <div className="flex flex-col justify-center pb-14 pt-10 md:min-h-[calc(100svh-69px)] md:pb-36 md:pt-8">
          <Link
            href="/roadmap"
            className="group inline-flex w-fit items-center gap-2 self-center rounded-full border border-rule-strong bg-paper-raised/80 py-1 pl-2.5 pr-3.5 no-underline transition-colors duration-200 hover:border-oxblood"
          >
            <span aria-hidden="true" className="size-1.5 rounded-full bg-forest" />
            <span className="label text-ink-muted transition-colors duration-200 group-hover:text-ink">
              v0.1.0 · Tier 3 complete
            </span>
            <span aria-hidden="true" className="text-ink-muted transition-transform duration-200 group-hover:translate-x-0.5">
              →
            </span>
          </Link>

          <h1 className="m-0 mt-8 text-center text-[clamp(2.75rem,6.6vw,5.5rem)] leading-[1.02] tracking-[-0.02em]">
            {site.tagline}
          </h1>

          <div className="mt-12 grid gap-x-12 gap-y-10 lg:mt-16 lg:grid-cols-12 lg:items-end">
            <div className="lg:col-span-6">
              {/* The name carries the definition, so the hero needs no wordmark
                  of its own — the header already holds the mark. */}
              <p className="m-0 text-xl leading-relaxed text-ink-muted md:max-w-[46ch]">
                <strong className="font-medium text-ink">omega</strong> {site.definition}
              </p>
              <p className="m-0 mt-4 text-xl text-ink-muted">
                <span className="text-ink">{site.headline}</span> {site.headlineRest}
              </p>
            </div>

            <div className="min-w-0 lg:col-span-6 lg:col-start-7">
              <InstallBox />
              <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-3">
                <Link
                  href="/docs"
                  className="label cursor-pointer border-b border-oxblood pb-0.5 text-oxblood transition-colors duration-200 hover:border-ink hover:text-ink"
                >
                  Read the docs →
                </Link>
                <a
                  href={site.repo}
                  className="label cursor-pointer border-b border-rule-strong pb-0.5 text-ink transition-colors duration-200 hover:border-oxblood hover:text-oxblood"
                >
                  Source on GitHub
                  <ExternalArrow className="ml-1.5" />
                </a>
              </div>
            </div>
          </div>
        </div>
      </Reveal>

      <ScrollCue />
    </section>
  );
}
