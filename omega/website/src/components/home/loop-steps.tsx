"use client";

import { useEffect, useRef, useState } from "react";

/**
 * The loop's four steps, stacked, lit one after another while on screen.
 *
 * The section's claim is "four steps, on repeat". A static row of four boxes
 * states the steps and loses the repeat; a highlight walking 1 → 2 → 3 → 4 → 1
 * is the repeat, drawn. It is the only thing on the home page that moves on its
 * own apart from the hero's replay, and it moves because the content does.
 *
 * The server renders all four unlit, which is also the resting state — so
 * hydration changes nothing, the cycle only runs while the band is visible,
 * and reduced motion leaves it unlit for good.
 */
export function LoopSteps({
  steps,
}: {
  steps: readonly { label: string; detail: string }[];
}) {
  const [active, setActive] = useState(-1);
  const ref = useRef<HTMLOListElement | null>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node || !("IntersectionObserver" in window)) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let timer: number | undefined;
    const observer = new IntersectionObserver(
      ([entry]) => {
        window.clearInterval(timer);
        if (!entry?.isIntersecting) return;
        timer = window.setInterval(() => setActive((i) => (i + 1) % steps.length), 1300);
      },
      { threshold: 0.4 },
    );
    observer.observe(node);
    return () => {
      observer.disconnect();
      window.clearInterval(timer);
    };
  }, [steps.length]);

  return (
    <ol ref={ref} className="m-0 list-none border-l border-rule p-0">
      {steps.map((s, i) => {
        const lit = i === active;
        return (
          <li key={s.label} className="relative py-4 pl-6 first:pt-0 last:pb-0">
            {/* The walking rule: an oxblood bar down the lit step's left edge,
                laid over the list's hairline. */}
            <span
              aria-hidden="true"
              className={`absolute -left-px top-0 bottom-0 w-0.5 origin-top bg-oxblood transition-transform duration-500 ${
                lit ? "scale-y-100" : "scale-y-0"
              }`}
            />
            <div className="flex items-baseline gap-3">
              <span className="tnum label text-oxblood">{String(i + 1).padStart(2, "0")}</span>
              <h3 className={`m-0 text-xl transition-colors duration-500 ${lit ? "text-oxblood" : ""}`}>
                {s.label}
              </h3>
            </div>
            <p className="m-0 mt-1 text-ink-muted">{s.detail}</p>
          </li>
        );
      })}
    </ol>
  );
}
