import { Reveal } from "@/components/site/interactive";

/**
 * The shared vocabulary every page is built from.
 *
 * These live together because they are one system, not five unrelated widgets:
 * change the section gutter here and it changes everywhere, which is the whole
 * reason the pages stopped repeating themselves.
 */

/** Horizontal gutter. One definition, used by every section and page header. */
export const GUTTER = "px-6 md:px-12";

/**
 * The mark on a link that leaves the site.
 *
 * Was `&#8599;` — a font glyph, so its size, weight and baseline were whatever
 * the rendering font happened to give it, and in the serif face it came out
 * long and thin. A path has no font to disagree with: `size-[0.55em]` is the
 * size, on every platform.
 *
 * The geometry is Lucide's `arrow-up-right` (`lucide-react@1.34.0`, ISC),
 * inlined rather than imported — two paths do not need a component, and this
 * keeps the icon out of the bundle. ISC also asks nothing of the page; the
 * Flaticon and Vecteezy free tiers want visible attribution, and Font Awesome
 * Free is CC BY 4.0, which is a licence footer on every page in exchange for
 * an arrow.
 */
export function ExternalArrow({ className = "" }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`inline-block size-[0.55em] shrink-0 align-[0.06em] ${className}`}
    >
      <path d="M7 7h10v10" />
      <path d="M7 17 17 7" />
    </svg>
  );
}

/**
 * A page section: standard gutter, standard rhythm, revealed on scroll.
 * `last` drops the bottom rule and adds the closing space.
 */
export function Section({
  children,
  last = false,
  className = "",
}: {
  children: React.ReactNode;
  last?: boolean;
  className?: string;
}) {
  return (
    <section
      className={[
        last ? "pb-20" : "border-b border-rule",
        GUTTER,
        "py-16",
        className,
      ].join(" ")}
    >
      <Reveal>{children}</Reveal>
    </section>
  );
}

/** The small-caps label that opens a section. */
export function Eyebrow({ children }: { children: React.ReactNode }) {
  return <p className="label m-0 mb-6 text-ink-muted">{children}</p>;
}

/** The opening block of a sub-page: eyebrow, title, optional lede. */
export function PageHeader({
  eyebrow,
  title,
  lede,
  children,
}: {
  eyebrow: string;
  title: string;
  lede?: string;
  children?: React.ReactNode;
}) {
  return (
    <Reveal>
      <Eyebrow>{eyebrow}</Eyebrow>
      <h1 className="m-0 max-w-[20ch] text-4xl leading-[1.1] md:text-5xl">{title}</h1>
      {lede && <p className="mt-6 max-w-[56ch] text-lg text-ink-muted">{lede}</p>}
      {children}
    </Reveal>
  );
}

/** Semantic state. Coloured, but never colour-only — the word is always present. */
export function StatusLabel({ state, className = "" }: { state: string; className?: string }) {
  const done = state === "built" || state === "closed" || state === "shipped";
  return (
    <span className={`label ${done ? "text-forest" : "text-oxblood"} ${className}`}>{state}</span>
  );
}

/** A card ruled along its top edge — the page's single card treatment. */
export function RuleCard({
  title,
  meta,
  children,
  accent = true,
}: {
  title: string;
  meta?: string;
  children?: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <article className={`border-t-2 pt-5 ${accent ? "border-oxblood" : "border-rule-strong"}`}>
      <h3 className="m-0 text-2xl">{title}</h3>
      {meta && <p className="m-0 mt-1 font-mono text-xs text-ink-muted">{meta}</p>}
      {children && <div className="mt-4 max-w-[46ch] text-ink-muted">{children}</div>}
    </article>
  );
}
