"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useState } from "react";
import { ExternalArrow } from "@/components/site/primitives";
import { EXTERNAL, NAV, isCurrent } from "@/lib/nav";

/**
 * The phone navigation: a hamburger that discloses the pages beneath it.
 *
 * Six links will not share a row with the wordmark on a 390px screen, and
 * wrapping them onto a second row cost more header height than the content
 * could spare. This trades that for one control.
 *
 * The panel closes on link click rather than by watching `usePathname` in an
 * effect — the header never unmounts between routes, so something has to close
 * it, and doing that in the click handler keeps `setState` out of an effect
 * body entirely.
 */
export function MobileNav() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const panelId = useId();

  // Escape is the expected way out of any disclosure.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const bar = "absolute left-0 block h-px w-full bg-ink transition-all duration-200";

  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={open ? "Close menu" : "Open menu"}
        onClick={() => setOpen((v) => !v)}
        className="-mr-1 flex h-10 w-10 cursor-pointer items-center justify-center md:hidden"
      >
        {/* Three hairlines, the same weight as every other rule on the page. */}
        <span aria-hidden="true" className="relative block h-3 w-5">
          <span className={`${bar} ${open ? "top-1/2 rotate-45" : "top-0"}`} />
          <span className={`${bar} top-1/2 ${open ? "opacity-0" : "opacity-100"}`} />
          <span className={`${bar} ${open ? "top-1/2 -rotate-45" : "top-full"}`} />
        </span>
      </button>

      {open && (
        <div id={panelId} className="w-full border-t border-rule pt-2 md:hidden">
          <ul className="m-0 grid list-none gap-0 p-0">
            {NAV.map((item) => (
              <li key={item.label} className="border-b border-rule">
                <Link
                  href={item.href}
                  onClick={() => setOpen(false)}
                  aria-current={isCurrent(pathname, item.href) ? "page" : undefined}
                  className={`label block cursor-pointer py-3.5 transition-colors duration-200 hover:text-oxblood ${
                    isCurrent(pathname, item.href) ? "text-ink" : "text-ink-muted"
                  }`}
                >
                  {item.label}
                </Link>
              </li>
            ))}
            {EXTERNAL.map((item, i) => (
              <li
                key={item.label}
                className={i === EXTERNAL.length - 1 ? "" : "border-b border-rule"}
              >
                <a
                  href={item.href}
                  onClick={() => setOpen(false)}
                  className="label block cursor-pointer py-3.5 text-ink transition-colors duration-200 hover:text-oxblood"
                >
                  {item.label}
                  <ExternalArrow className="ml-1.5 text-ink-muted" />
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
