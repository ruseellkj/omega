import type { NextConfig } from "next";

/**
 * Static export. Nothing on this site is dynamic — no route handlers, no server
 * actions, no `cookies()` or `headers()`, no ISR — so the App Router was already
 * prerendering every page at build time.
 *
 * What `output: "export"` changes is the *artifact*, not the rendering strategy:
 * the build emits plain HTML, CSS and JS into `out/` with no Node server behind
 * it. That is what makes it hostable on any static host and removes cold starts;
 * it is not a change in how fast a page renders once delivered.
 *
 * `trailingSlash` emits `out/docs/index.html` rather than `out/docs.html`, which
 * is what GitHub Pages and S3-style hosts need to serve `/docs` without a
 * rewrite rule.
 */
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
};

export default nextConfig;
