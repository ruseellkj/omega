"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";

/**
 * Small client islands shared across pages: the scroll reveal and the copy
 * button. Everything else renders on the server.
 */

/**
 * Fade-and-lift a section as it enters the viewport.
 *
 * Progressive enhancement, deliberately: the server renders the content
 * *visible*, and the hidden state is only applied once JS confirms it can
 * observe and un-hide again. Without JS, without IntersectionObserver, or with
 * reduced motion requested, the content is simply there — an element must never
 * be left invisible, which is the usual failure mode of scroll reveals.
 *
 * The class toggle goes through the DOM node rather than React state because
 * this is presentation, not application state — nothing else needs to know.
 */
const HIDDEN = ["opacity-0", "translate-y-3"] as const;

export function Reveal({
  children,
  delay = 0,
  className = "",
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || !("IntersectionObserver" in window)) return;

    node.classList.add(...HIDDEN);

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            node.classList.remove(...HIDDEN);
            observer.disconnect();
          }
        }
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.05 },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      style={{ transitionDelay: delay ? `${delay}ms` : undefined }}
      className={`transition-[opacity,transform] duration-700 ease-out motion-reduce:transition-none ${className}`}
    >
      {children}
    </div>
  );
}

/**
 * Put text on the clipboard, or say it could not.
 *
 * `navigator.clipboard` exists only in a secure context, so a page opened over
 * plain http on a LAN address has none. The hidden-textarea fallback is the
 * pre-Clipboard-API route and still works there. Deprecated is not the same as
 * gone, and a copy button that silently does nothing is the worst outcome.
 */
async function writeClipboard(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // fall through to the textarea route
  }
  try {
    const area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

/*
 * Lucide's `copy` and `check` (lucide-react@1.34.0, ISC), inlined for the same
 * reason as ExternalArrow in primitives.tsx: a few paths do not need the
 * package in the bundle, and a path cannot fall back to the wrong font.
 */
function CopyGlyph() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="size-3.5 shrink-0">
      <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
    </svg>
  );
}

function CheckGlyph() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" className="size-3.5 shrink-0">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

/**
 * Transparent in both places, with a hairline and nothing else. The first
 * version filled the install box's button with the accent colour, and a filled
 * button beside every command competed with the command itself.
 *
 * `hover:bg-transparent` is not redundant: the shadcn `ghost` variant brings
 * its own hover fill, which here would be the forest accent.
 */
const COPY_TONES = {
  /** On paper. */
  paper:
    "border border-rule bg-transparent text-ink-muted hover:border-rule-strong hover:bg-transparent hover:text-ink",
  /** Inside a terminal. */
  shell:
    "border border-white/15 bg-transparent text-shell-dim hover:border-white/35 hover:bg-transparent hover:text-white",
} as const;

/**
 * Copy a shell command. The label states what happened rather than apologising
 * for what did not, and it reverts, so the control is never left lying.
 *
 * `value` is the command alone. The `$ ` a terminal box draws is decoration,
 * and a copy that included it would paste a command the shell rejects.
 *
 * `iconOnly` is for the per-line buttons inside a terminal, where a word on
 * every row would drown the commands. It keeps an accessible name; only the
 * visible label goes.
 */
export function CopyButton({
  value,
  tone = "paper",
  iconOnly = false,
  label = "Copy",
  className = "",
}: {
  value: string;
  tone?: keyof typeof COPY_TONES;
  iconOnly?: boolean;
  label?: string;
  className?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  useEffect(() => {
    if (state === "idle") return;
    const timer = window.setTimeout(() => setState("idle"), 1800);
    return () => window.clearTimeout(timer);
  }, [state]);

  async function copy() {
    setState((await writeClipboard(value)) ? "copied" : "failed");
  }

  const word = state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : label;

  return (
    <Button
      type="button"
      onClick={copy}
      variant="ghost"
      aria-label={iconOnly ? `${label}: ${value}` : undefined}
      title={iconOnly ? `${label} command` : undefined}
      className={`label h-auto shrink-0 cursor-pointer gap-1.5 rounded-[6px] px-2.5 py-1.5 transition-colors duration-200 ${COPY_TONES[tone]} ${className}`}
    >
      {state === "copied" ? <CheckGlyph /> : <CopyGlyph />}
      <span aria-live="polite" className={iconOnly ? "sr-only" : ""}>
        {word}
      </span>
    </Button>
  );
}
