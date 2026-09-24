"use client";

import { useId, useRef, useState } from "react";
import { CopyButton } from "@/components/site/interactive";
import { installMethods } from "@/lib/content";

type Method = (typeof installMethods)[number]["id"];

/**
 * The install command: a pill, not a window.
 *
 * Tau's hero draws the same line (research/tau/website/layouts/index.html:17):
 * the install command is a dark pill with a `$` and a copy control, and only a
 * real terminal gets the dots and a title bar. An earlier version here gave the
 * install box full window chrome, which put two identical-looking terminals in
 * the hero — one to act on, one to look at — and nothing said which was which.
 * Chrome now means "a screen you are shown"; a pill means "a line you copy".
 *
 * The route tabs sit above the pill on the paper, as quiet labels. Pi's landing
 * page offers a tab per install route; three are real here, and each was run
 * end to end before it went on the page.
 *
 * **The server renders the curl tab, complete**, so nothing moves on hydration
 * and without JS the command most people need is still there to select. Arrow
 * keys move between tabs — the WAI-ARIA tabs pattern, which is why inactive
 * tabs are `tabIndex={-1}`.
 */
export function InstallBox({
  className = "",
  centered = false,
}: {
  className?: string;
  /** Centre the tabs and the note, for a centred column. */
  centered?: boolean;
}) {
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
      <div
        role="tablist"
        aria-label="Install method"
        className={`flex items-center gap-4 ${centered ? "sm:justify-center" : ""}`}
      >
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
              className={`label -mb-px cursor-pointer border-b pb-1 transition-colors duration-200 ${
                selected
                  ? "border-ink text-ink"
                  : "border-transparent text-ink-muted hover:text-ink"
              }`}
            >
              {m.label}
            </button>
          );
        })}
      </div>

      <div
        id={`${base}-panel`}
        role="tabpanel"
        aria-labelledby={`${base}-tab-${method.id}`}
        className="mt-3 flex items-center gap-3 rounded-[10px] border border-rule-strong bg-shell py-2.5 pl-5 pr-2.5 font-mono shadow-[0_18px_40px_-26px_rgba(34,28,26,0.55)]"
      >
        {/* The command scrolls inside its own strip. A phone is narrower than
            the curl line, and the page must not scroll sideways. */}
        <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-[12.5px] text-white [scrollbar-width:thin] sm:text-[13.5px]">
          <span aria-hidden="true" className="select-none text-shell-dim">
            ${" "}
          </span>
          {method.cmd}
        </code>
        <CopyButton value={method.cmd} tone="shell" />
      </div>
      <p className={`m-0 mt-2.5 text-sm text-ink-muted ${centered ? "sm:text-center" : ""}`}>
        {method.note}
      </p>
    </div>
  );
}
