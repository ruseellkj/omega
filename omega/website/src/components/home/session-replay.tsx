"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The hero's terminal: one real turn of omega's terminal UI, replayed.
 *
 * ## Captured, not drawn
 *
 * Every string below came off the real screen. `OmegaApp` was driven through
 * Textual's pilot at 96×30 in an empty `git init`ed folder, with the scripted
 * `FakeProvider` answering, and the screen was exported after each step. The
 * wordmark is `tui/banner.py`'s `BANNER`, the mascot is its `MASCOT`, the
 * spinner is `status.FRAMES`, the verbs ("listing", "listed") are
 * `status._VERBS`. The one authored line is the reply, because a scripted
 * provider's reply is authored by definition, and the caption says so.
 *
 * ## Why a replay rather than a screenshot
 *
 * A screenshot shows the splash or the answer, never the part that makes a
 * coding agent legible: the tool rows opening, spinning and closing while the
 * status line says which file. That sequence is the product, so it moves.
 *
 * ## The first frame is the server's frame
 *
 * The server renders the splash with an empty prompt, which is exactly what
 * omega shows before you type. Playback starts from there when the terminal
 * scrolls into view, so hydration changes nothing and there is no flash of a
 * finished turn being wiped and retyped. With reduced motion requested it
 * skips straight to the finished turn and never animates.
 */

const WORDMARK = [
  "  ██████╗  ███╗   ███╗ ███████╗  ██████╗   █████╗",
  " ██╔═══██╗ ████╗ ████║ ██╔════╝ ██╔════╝  ██╔══██╗",
  " ██║   ██║ ██╔████╔██║ █████╗   ██║  ███╗ ███████║",
  " ██║   ██║ ██║╚██╔╝██║ ██╔══╝   ██║   ██║ ██╔══██║",
  " ╚██████╔╝ ██║ ╚═╝ ██║ ███████╗ ╚██████╔╝ ██║  ██║",
  "  ╚═════╝  ╚═╝     ╚═╝ ╚══════╝  ╚═════╝  ╚═╝  ╚═╝",
].join("\n");

const MASCOT = [" ▄█████▄", "██ ▀ ▀ ██", "██     ██", " ▀█   █▀", "██▄   ▄██"];

/** The splash's facts box, in the order the real one lays them out. */
const FACTS = [
  ["model", "claude-sonnet-5"],
  ["approval", "asks before changes"],
  ["signed in", "anthropic · environment"],
  ["session", "new"],
  ["path", "~/my-project"],
  ["version", "0.1.0"],
  ["branch", "main"],
] as const;

const FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

const PROMPT = "what does this project do?";
const PLACEHOLDER = "Ask anything, or / for commands";
const REPLY =
  "It converts a CSV file to JSON. main.py is the whole program, and tests/ holds one test.";
const REPLY_WORDS = REPLY.split(" ");

type Tool = { running: string; done: string };
const TOOLS: Tool[] = [
  { running: "listing .", done: "listed ." },
  { running: "reading README.md", done: "read README.md" },
];

type Frame = {
  phase: "splash" | "turn";
  typed: number;
  tools: number; // tools opened so far
  closed: number; // tools finished so far
  words: number; // reply words shown
  status: string | null;
};

const START: Frame = { phase: "splash", typed: 0, tools: 0, closed: 0, words: 0, status: null };
const END: Frame = {
  phase: "turn",
  typed: PROMPT.length,
  tools: TOOLS.length,
  closed: TOOLS.length,
  words: REPLY_WORDS.length,
  status: null,
};

/** The whole turn as (delay before, next frame) steps. Timings are for reading, not realism. */
function script(): [number, Partial<Frame>][] {
  const steps: [number, Partial<Frame>][] = [];
  for (let i = 1; i <= PROMPT.length; i++) steps.push([i === 1 ? 700 : 48, { typed: i }]);
  steps.push([420, { phase: "turn", status: "working" }]);
  steps.push([650, { tools: 1, status: TOOLS[0].running }]);
  steps.push([850, { closed: 1, tools: 2, status: TOOLS[1].running }]);
  steps.push([850, { closed: 2, status: "working" }]);
  for (let w = 1; w <= REPLY_WORDS.length; w++) steps.push([w === 1 ? 600 : 55, { words: w }]);
  steps.push([200, { status: null }]);
  return steps;
}

export function SessionReplay() {
  const [frame, setFrame] = useState<Frame>(START);
  const [spin, setSpin] = useState(0);
  const [done, setDone] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  const timers = useRef<number[]>([]);

  const stop = () => {
    for (const t of timers.current) window.clearTimeout(t);
    timers.current = [];
  };

  const play = useCallback(() => {
    stop();
    setDone(false);
    setFrame(START);
    let at = 0;
    for (const [delay, patch] of script()) {
      at += delay;
      timers.current.push(window.setTimeout(() => setFrame((f) => ({ ...f, ...patch })), at));
    }
    timers.current.push(window.setTimeout(() => setDone(true), at + 50));
  }, []);

  // Start once, when the terminal is actually on screen.
  useEffect(() => {
    const node = box.current;
    if (!node) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      // One swap to the finished turn; nothing ever animates.
      timers.current.push(
        window.setTimeout(() => {
          setFrame(END);
          setDone(true);
        }, 0),
      );
      return stop;
    }
    if (typeof IntersectionObserver === "undefined") {
      // Deferred, like the branch above: state set from a callback, not from
      // the effect body, so the first render commits before playback starts.
      timers.current.push(window.setTimeout(play, 0));
      return stop;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          observer.disconnect();
          play();
        }
      },
      { threshold: 0.35 },
    );
    observer.observe(node);
    return () => {
      observer.disconnect();
      stop();
    };
  }, [play]);

  // The spinner turns only while something is running — as in the TUI.
  useEffect(() => {
    if (!frame.status) return;
    const id = window.setInterval(() => setSpin((s) => (s + 1) % FRAMES.length), 100);
    return () => window.clearInterval(id);
  }, [frame.status]);

  const typing = frame.phase === "splash";
  const promptText = typing ? PROMPT.slice(0, frame.typed) : "";

  return (
    <figure className="m-0 min-w-0">
      <div
        ref={box}
        className="overflow-hidden rounded-md border border-night-rule bg-night font-mono text-[12px] leading-[1.55] text-night-ink shadow-[0_28px_60px_-30px_rgba(34,28,26,0.7)] sm:text-[12.5px]"
      >
        {/* window chrome */}
        <div className="flex items-center gap-1.5 border-b border-night-rule bg-night-raised px-4 py-2.5">
          <span className="h-2.5 w-2.5 rounded-full bg-night-accent/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-night-muted/50" />
          <span className="h-2.5 w-2.5 rounded-full bg-night-green/70" />
          <span className="label ml-2 truncate text-night-muted">omega — ~/my-project</span>
          <button
            type="button"
            onClick={play}
            aria-label="Replay the session"
            tabIndex={done ? 0 : -1}
            className={`label ml-auto cursor-pointer rounded-sm border border-night-rule px-2 py-0.5 text-night-muted transition-[opacity,color,border-color] duration-300 hover:border-night-accent hover:text-night-ink ${
              done ? "opacity-100" : "pointer-events-none opacity-0"
            }`}
          >
            ↻ replay
          </button>
        </div>

        {/* The screen. Fixed height so the splash and the turn occupy the same
            box and nothing below the hero moves when one replaces the other. */}
        <div className="flex h-[25.5rem] flex-col sm:h-[27rem]">
          <div className="min-h-0 flex-1 overflow-hidden px-4 pt-4">
            {typing ? (
              <div>
                <pre className="m-0 w-fit max-w-full overflow-hidden bg-linear-to-r from-night-accent to-night-green bg-clip-text font-glyph text-[8.5px] leading-none text-transparent min-[420px]:text-[10px] sm:text-[12px] lg:text-[11.5px] xl:text-[12.5px]">
                  {WORDMARK}
                </pre>
                <dl className="m-0 mt-3 grid grid-cols-1 gap-x-5 gap-y-0.5 rounded-md border border-night-accent/40 px-3.5 py-2.5 text-[11.5px] sm:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
                  {FACTS.map(([k, v]) => (
                    <div key={k} className="flex min-w-0 gap-2.5">
                      <dt className="w-[4.7rem] shrink-0 text-night-accent/65">{k}</dt>
                      <dd className="m-0 truncate font-semibold text-night-accent">{v}</dd>
                    </div>
                  ))}
                </dl>
                <p className="m-0 mt-2 truncate whitespace-pre text-night-accent/65">
                  {" /  commands    ↑  history    ctrl+o  expand    ctrl+c/ctrl+d  exit"}
                </p>
              </div>
            ) : (
              <div>
                {/* the compact header that replaces the splash */}
                {/* A terminal cell is taller than the block glyph drawn in it, so
                    the mascot is set solid (leading-none) and stretched to the
                    text's row height rather than spaced out to it — spacing
                    leaves a gap between every row of blocks. The wrapper holds
                    the stretched height, since a transform takes no layout space. */}
                <div className="flex gap-3">
                  <div className="h-[6.25em] shrink-0">
                    <pre className="m-0 origin-top scale-y-125 font-glyph leading-none text-night-accent">
                      {MASCOT.join("\n")}
                    </pre>
                  </div>
                  <div className="pt-[1.25em] leading-[1.25]">
                    <div>
                      <span className="text-night-accent">omega</span>{" "}
                      <span className="text-night-muted">v0.1.0</span>
                    </div>
                    <div className="text-night-muted">claude-sonnet-5 · anthropic</div>
                    <div className="text-night-muted">~/my-project</div>
                  </div>
                </div>

                <div className="mt-3 rounded-sm bg-night-band px-2 py-1">
                  ❯ {PROMPT}
                </div>

                <ul className="m-0 mt-2 list-none p-0">
                  {TOOLS.slice(0, frame.tools).map((t, i) => {
                    const closed = i < frame.closed;
                    return (
                      <li key={t.done} className="truncate">
                        ▶ {closed ? "✓" : "…"} {closed ? t.done : t.running}
                      </li>
                    );
                  })}
                </ul>

                {frame.words > 0 && (
                  <p className="m-0 mt-2 text-night-ink">
                    {REPLY_WORDS.slice(0, frame.words).join(" ")}
                  </p>
                )}
              </div>
            )}
          </div>

          {/* status line, prompt box and key bar — pinned to the bottom, as in the TUI */}
          <div className="shrink-0">
            <div className="h-[1.55em] px-4 text-night-muted">
              {frame.status && (
                <span>
                  <span className="text-night-accent">{FRAMES[spin]}</span> {frame.status}
                </span>
              )}
            </div>
            <div className="border-y border-night-rule px-4 py-1.5">
              <span className="font-semibold text-night-accent">❯</span>{" "}
              {promptText ? (
                <span>{promptText}</span>
              ) : (
                <span className="text-night-muted/70">{PLACEHOLDER}</span>
              )}
              {typing && (
                <span
                  aria-hidden="true"
                  className="cursor-blink ml-px inline-block h-[1.1em] w-[0.55em] translate-y-[0.15em] bg-night-ink/80"
                />
              )}
            </div>
            <div className="flex justify-between gap-4 whitespace-nowrap px-4 py-1.5 text-[11px] text-night-ink">
              <span className="truncate">
                <b className="font-semibold text-night-accent">^d</b> Exit{"  "}
                <b className="font-semibold text-night-accent">^c</b> Stop / exit{"  "}
                <b className="font-semibold text-night-accent">^o</b> Expand tools
              </span>
              <span className="hidden sm:inline">
                <b className="font-semibold text-night-accent">^p</b> palette
              </span>
            </div>
          </div>
        </div>
      </div>
      <figcaption className="mt-3 text-sm text-ink-muted">
        One turn of the terminal UI, replayed from a real capture. The scripted{" "}
        <code className="font-mono text-[0.85em] text-ink">--fake</code> provider wrote the reply;
        omega drew everything else. Theme:{" "}
        <code className="font-mono text-[0.85em] text-ink">oxblood-dark</code>.
      </figcaption>
    </figure>
  );
}
