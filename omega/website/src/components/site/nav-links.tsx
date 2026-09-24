"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV, isCurrent } from "@/lib/nav";

/**
 * The header's page links, with the current page marked.
 *
 * A client island only because the current path is a browser fact; the rest of
 * the header stays on the server. `aria-current="page"` carries the state for
 * screen readers, and the underline carries it visually — not colour alone.
 */
export function NavLinks() {
  const pathname = usePathname();
  return (
    <>
      {NAV.map((item) => {
        const current = isCurrent(pathname, item.href);
        return (
          <Link
            key={item.label}
            href={item.href}
            aria-current={current ? "page" : undefined}
            className={`label cursor-pointer underline-offset-[6px] transition-colors duration-200 hover:text-oxblood ${
              current ? "text-ink underline decoration-oxblood decoration-1" : "text-ink-muted"
            }`}
          >
            {item.label}
          </Link>
        );
      })}
    </>
  );
}
