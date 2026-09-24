import Link from "next/link";
import { EXTERNAL, NAV } from "@/lib/nav";
import { GUTTER } from "@/components/site/primitives";

/** The wordmark at full voice, the way Tau closes its page. */
export function SiteFooter() {
  return (
    <footer className={`border-t border-rule pb-10 pt-12 md:pt-16 ${GUTTER}`}>
      <div className="grid gap-10 md:grid-cols-12">
        <div className="md:col-span-7">
          <p
            aria-hidden="true"
            className="m-0 select-none font-serif text-[5.5rem] leading-[0.8] text-oxblood sm:text-[8rem] md:text-[10rem]"
          >
            &#937;
          </p>
          <p className="m-0 mt-5 max-w-[38ch] text-lg text-ink-muted">
            Built in layers, in public. Read it end to end — that was the point.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-x-6 gap-y-3 md:col-span-5 md:flex md:flex-col md:items-end md:justify-end">
          {NAV.map((item) => (
            <Link
              key={item.label}
              href={item.href}
              className="label cursor-pointer text-ink-muted transition-colors duration-200 hover:text-oxblood"
            >
              {item.label}
            </Link>
          ))}
          {EXTERNAL.map((item) => (
            <a
              key={item.label}
              href={item.href}
              className="label cursor-pointer text-ink-muted transition-colors duration-200 hover:text-oxblood"
            >
              {item.label}
            </a>
          ))}
          <p className="col-span-2 m-0 mt-4 text-sm text-ink-muted/80">MIT &middot; pull requests welcome</p>
        </div>
      </div>
    </footer>
  );
}
