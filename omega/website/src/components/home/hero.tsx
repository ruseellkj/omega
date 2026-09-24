import Link from "next/link";
import { SessionSnapshot } from "@/components/home/session-snapshot";
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
 * The one split layout on the site. Every other section has its own shape.
 *
 * Left: what it is, and the install command — where both references put it,
 * because the hero is the one place every visitor reads. Right: the program
 * itself, one finished turn. The old right column was a list of flags, which
 * told you omega has a command line and showed nothing of what using it is like.
 *
 * The split starts at `lg`, not `md`. Half of a 768px screen squeezes both the
 * install command and the terminal; stacked, each gets the full width.
 */
export function Hero() {
  return (
    <section className={`relative overflow-hidden border-b border-rule py-14 md:py-20 ${GUTTER}`}>
      <GridPaper />

      <Reveal className="relative">
        <div className="grid items-center gap-x-12 gap-y-12 lg:grid-cols-12">
          <div className="min-w-0 lg:col-span-5">
            <h1 className="m-0 max-w-[16ch] text-4xl leading-[1.08] md:text-[3.25rem]">
              {site.tagline}
            </h1>

            {/* The name carries the definition, so the hero needs no wordmark
                of its own — the header already holds the mark. */}
            <p className="mt-7 max-w-[48ch] text-lg text-ink-muted">
              <strong className="font-medium text-ink">omega</strong> {site.definition}
            </p>

            <p className="mt-4 max-w-[42ch] text-lg text-ink-muted">
              <span className="text-ink">{site.headline}</span> {site.headlineRest}
            </p>

            <InstallBox className="mt-9" />

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

          <div className="min-w-0 lg:col-span-7">
            <SessionSnapshot />
          </div>
        </div>
      </Reveal>
    </section>
  );
}
