import { site } from "@/lib/content";

/** Primary navigation. Shared by the header and the footer so the two cannot drift. */
export const NAV = [
  { label: "Docs", href: "/docs" },
  { label: "Rant", href: "/rant" },
  { label: "Lessons", href: "/lessons" },
  { label: "Roadmap", href: "/roadmap" },
  { label: "Releases", href: "/releases" },
] as const;

/** Off-site links. Kept apart from NAV so both surfaces can set them off. */
export const EXTERNAL = [
  { label: "GitHub", href: site.repo },
  { label: "X", href: site.x },
] as const;
