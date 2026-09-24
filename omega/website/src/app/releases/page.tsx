import type { Metadata } from "next";
import { CopyButton, Reveal } from "@/components/site/interactive";
import { ExternalArrow, GUTTER, PageHeader } from "@/components/site/primitives";
import { CommandLine, Terminal } from "@/components/site/terminal";
import { releases, site } from "@/lib/content";

export const metadata: Metadata = {
  title: "Releases — omega",
  description:
    "Every tagged point in omega's history, with the measured size of the code at each one.",
};

/**
 * A releases page for a project that ships no package.
 *
 * The honest version of this page says so at the top rather than looking like
 * every other releases page and quietly implying a download. What these tags
 * are good for is reading: each is a point where the code was coherent and a
 * tier document described it, so `git checkout tier-1` is a way to see the
 * agent before it learned each thing.
 */
export default function ReleasesPage() {
  return (
    <section className={`py-16 md:py-20 ${GUTTER}`}>
      <PageHeader
        eyebrow="releases"
        title="Every tag, and what it weighed."
        lede="Points where the code was coherent and a tier document described it. Each figure is measured from the tag itself, so a row cannot drift from what it points at."
      >
        <div className="mt-8 flex flex-wrap items-baseline gap-x-8 gap-y-3">
          <span className="flex items-baseline gap-2.5">
            <span className="tnum font-serif text-2xl">{releases.length}</span>
            <span className="label text-ink-muted">tags</span>
          </span>
          <span className="flex items-baseline gap-2.5">
            <span className="tnum font-serif text-2xl">0</span>
            <span className="label text-ink-muted">packages published</span>
          </span>
        </div>
      </PageHeader>

      {/* Said once, at the top, instead of letting the page imply otherwise. */}
      <Reveal>
        <div className="mt-14 border-t-2 border-rule-strong pt-7">
          <p className="m-0 max-w-[62ch] text-ink-muted">
            <span className="text-ink">omega is not on PyPI yet.</span> There is no{" "}
            <code className="font-mono text-sm">pip install</code> and nothing on any index. The{" "}
            <code className="font-mono text-sm">curl … | sh</code> installer on the home page installs
            straight from this repository instead, and the PyPI release workflow is written and waits
            on the name being claimed — the mechanics are in{" "}
            <a
              href={`${site.repo}/blob/main/omega/PRODUCT-BACKLOG.md`}
              className="cursor-pointer underline decoration-rule-strong underline-offset-4 transition-colors duration-200 hover:text-oxblood"
            >
              PRODUCT-BACKLOG.md
              <ExternalArrow className="ml-1" />
            </a>
            .
          </p>
          {/* How to actually use a tag: every row below has its own checkout
              line, and this is the one command they all assume. */}
          <div className="mt-6 max-w-2xl">
            <Terminal title="read the code at any tag">
              <CommandLine
                cmd={`git clone ${site.repo}.git && cd omega`}
                note="Then check out any tag below. Each row was measured from that tag, so what you see matches it."
              />
            </Terminal>
          </div>
        </div>
      </Reveal>

      <ol className="m-0 mt-14 grid list-none gap-0 p-0">
        {releases.map((release, i) => (
          <Reveal key={release.tag} delay={i * 50}>
            <li className="grid gap-x-12 gap-y-5 border-t border-rule py-9 md:grid-cols-12">
              <div className="md:col-span-3">
                <a
                  href={`${site.repo}/releases/tag/${release.tag}`}
                  className="group inline-block cursor-pointer no-underline"
                >
                  <span className="font-mono text-sm text-oxblood transition-colors duration-200 group-hover:text-ink">
                    {release.tag}
                    <ExternalArrow className="ml-1.5" />
                  </span>
                </a>
                <span className="tnum mt-1.5 block text-sm text-ink-muted">{release.date}</span>
              </div>

              <div className="md:col-span-9">
                <h2 className="m-0 text-xl leading-snug">{release.title}</h2>
                <p className="m-0 mt-2.5 max-w-[62ch] text-ink-muted">{release.body}</p>

                <div className="mt-5 flex flex-wrap items-baseline gap-x-8 gap-y-2">
                  {[
                    { value: release.lines, label: "source lines" },
                    { value: release.files, label: "files" },
                    { value: release.tests, label: "tests" },
                  ].map((stat) => (
                    <span key={stat.label} className="flex items-baseline gap-2">
                      <span className="tnum font-mono text-sm">{stat.value}</span>
                      <span className="label text-xs text-ink-muted">{stat.label}</span>
                    </span>
                  ))}
                  <span className="flex items-center gap-1.5">
                    <code className="rounded-[3px] bg-term px-1.5 py-0.5 font-mono text-[12.5px] text-ink">
                      git checkout {release.tag}
                    </code>
                    <CopyButton value={`git checkout ${release.tag}`} iconOnly className="px-1.5 py-1" />
                  </span>
                  <a
                    href={`${site.repo}/tree/${release.tag}`}
                    className="label group cursor-pointer text-xs text-ink-muted no-underline transition-colors duration-200 hover:text-oxblood"
                  >
                    browse the code
                    <ExternalArrow className="ml-1" />
                  </a>
                </div>
              </div>
            </li>
          </Reveal>
        ))}
      </ol>
    </section>
  );
}
