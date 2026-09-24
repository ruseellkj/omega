import Link from "next/link";
import { EXTERNAL } from "@/lib/nav";
import { MobileNav } from "@/components/site/mobile-nav";
import { NavLinks } from "@/components/site/nav-links";
import { GUTTER } from "@/components/site/primitives";

/**
 * Sticky, on a solid paper ground rather than a blur — the design is opaque
 * paper and glass would fight it.
 *
 * The boundary rails are borders on the layout container, so they sit outside
 * this element's box. The opaque background cannot reach them, and `border-b`
 * below terminates exactly where they run.
 */
export function SiteHeader() {
  return (
    <header
      className={`sticky top-0 z-30 flex flex-wrap items-center justify-between gap-y-3 border-b border-rule bg-paper py-4 md:py-5 ${GUTTER}`}
    >
      {/* The wordmark is set in the display serif, not the `.label` utility the
          nav links use — sharing that class made the brand a peer of "Docs".
          Family and case separate them; size alone would not. */}
      <Link href="/" className="group flex items-center gap-2 no-underline">
        <span className="font-serif text-[1.7rem] leading-none text-oxblood">&#937;</span>
        <span className="font-serif text-[1.35rem] leading-none tracking-[0.005em] text-ink transition-colors duration-200 group-hover:text-oxblood">
          omega
        </span>
      </Link>

      {/* Below md the links move into the hamburger panel instead. */}
      <nav
        aria-label="Primary"
        className="hidden items-center gap-x-5 md:flex md:gap-x-6"
      >
        <NavLinks />

        {/* A hairline sets the outbound links off from the pages. */}
        <span aria-hidden="true" className="h-3 w-px bg-rule-strong" />

        {EXTERNAL.map((item) => (
          <a
            key={item.label}
            href={item.href}
            className="label cursor-pointer text-ink underline decoration-rule-strong underline-offset-4 transition-colors duration-200 hover:text-oxblood hover:decoration-oxblood"
          >
            {item.label}
          </a>
        ))}
      </nav>

      <MobileNav />
    </header>
  );
}
