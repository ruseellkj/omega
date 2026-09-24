"use client";

import { useId, useRef, useState } from "react";
import { CopyButton } from "@/components/site/interactive";
import { Terminal } from "@/components/site/terminal";
import { installMethods } from "@/lib/content";

type Method = (typeof installMethods)[number]["id"];

/**
 * The install command, where a first-time visitor is already looking.
 *
 * Both references put this in the hero rather than on a docs page. Tau's is a
 * dark pill with a `$` and a copy control (research/tau/website/layouts/index.html:17),
 * and Pi's adds tabs, one per install route, with a primary Copy button beside
 * the command. This is Pi's tabs inside the site's one terminal frame, so the
 * install command looks like every other command on the page.
 *
 * **The server renders the curl tab, complete.** Tabs only matter once there is
 * JS to switch them, so nothing moves or flashes when the page hydrates, and
 * without JS the one command most people need is still there to select.
 *
 * Arrow keys move between tabs. That is the WAI-ARIA tabs pattern, and the
 * reason the inactive tabs are `tabIndex={-1}`: Tab should leave the tab list,
 * not walk through it.
 */
export function InstallBox({ className = "" }: { className?: string }) {
  const [active, setActive] = useState<Method>("curl");
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const base = useId();
  const method = installMethods.find((m) => m.id === active) ?? installMethods[0];

  function onKey(e: React.KeyboardEvent, index: number) {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = (index + step + installMethods.length) % installMethods.length;
    setActive(installMethods[next].id);
    tabs.current[next]?.focus();
  }

  return (
    <div className={`min-w-0 max-w-full ${className}`}>
      <Terminal
        bodyClassName=""
        bar={
          <div role="tablist" aria-label="Install method" className="ml-2 flex items-center gap-1">
            {installMethods.map((m, i) => {
              const selected = m.id === active;
              return (
                <button
                  key={m.id}
                  ref={(node) => {
                    tabs.current[i] = node;
                  }}
                  id={`${base}-tab-${m.id}`}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  aria-controls={`${base}-panel`}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => setActive(m.id)}
                  onKeyDown={(e) => onKey(e, i)}
                  className={`cursor-pointer rounded-[6px] px-2 py-0.5 text-[12px] transition-colors duration-200 ${
                    selected ? "bg-white/[0.08] text-white" : "text-white/40 hover:text-white/75"
                  }`}
                >
                  {m.label}
                </button>
              );
            })}
          </div>
        }
      >
        <div
          id={`${base}-panel`}
          role="tabpanel"
          aria-labelledby={`${base}-tab-${method.id}`}
          className="flex items-center gap-3 py-3 pl-5 pr-3"
        >
          {/* The command scrolls inside its own strip. A phone is narrower
              than the curl line, and the page must not scroll sideways. */}
          <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-[12.5px] text-white [scrollbar-width:thin] sm:text-[13.5px]">
            <span aria-hidden="true" className="select-none text-shell-dim">
              ${" "}
            </span>
            {method.cmd}
          </code>
          <CopyButton value={method.cmd} tone="shell" />
        </div>
      </Terminal>
      <p className="m-0 mt-2.5 text-sm text-ink-muted">{method.note}</p>
    </div>
  );
}
