import { site } from "@/lib/content";

/**
 * Primary navigation. Shared by the header, the phone menu and the footer so
 * the three cannot drift.
 *
 * Ordered by what a visitor comes for: how to use it (Docs), where it stands
 * (Roadmap, Releases), then the essays (Lessons, Rant). The old order put the
 * rant second, so the first thing after the manual was an argument.
 */
export const NAV = [
  { label: "Rant", href: "/rant" },
  { label: "Docs", href: "/docs" },
  { label: "Lessons", href: "/lessons" },
  { label: "Releases", href: "/releases" },
  { label: "Roadmap", href: "/roadmap" },
] as const;

/** Off-site links. Kept apart from NAV so both surfaces can set them off. */
export const EXTERNAL = [
  { label: "GitHub", href: site.repo },
  { label: "X", href: site.x },
] as const;

/**
 * Is `href` the page being shown?
 *
 * The site is a static export with `trailingSlash: true`, so the same page can
 * arrive as `/docs/` or `/docs`. Both are trimmed before comparing.
 */
export function isCurrent(pathname: string | null, href: string): boolean {
  const trim = (p: string) => p.replace(/\/+$/, "") || "/";
  const here = trim(pathname ?? "/");
  const target = trim(href);
  return here === target || here.startsWith(`${target}/`);
}
