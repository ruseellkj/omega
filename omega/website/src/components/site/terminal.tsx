import { CopyButton } from "@/components/site/interactive";

/**
 * The one terminal frame on the site. Every box that shows a command or a
 * session uses it, so there is one terminal style rather than four.
 *
 * The shape is Tau's `.terminal` (research/tau/website/assets/css/main.css:417):
 * a dark body, a title bar with a faint rule under it, three dots of which only
 * the first has colour, a monospace title at low contrast. Static — it shows
 * text, it does not perform it.
 *
 * `rounded-[10px]` is spelled out because this theme maps `rounded-md` and
 * `rounded-lg` to 2px (globals.css), which is what made the first boxes look
 * square.
 */
export function Terminal({
  title = "omega",
  bar,
  children,
  className = "",
  bodyClassName = "px-5 py-4",
}: {
  title?: string;
  /** Replaces the title — the install box puts its tabs here. */
  bar?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <div
      className={`min-w-0 overflow-hidden rounded-[10px] border border-rule-strong bg-shell font-mono text-[13px] leading-[1.8] text-shell-ink shadow-[0_24px_50px_-30px_rgba(34,28,26,0.55)] ${className}`}
    >
      <div className="flex items-center gap-2 border-b border-white/[0.07] px-4 py-2.5">
        <span aria-hidden="true" className="size-[11px] shrink-0 rounded-full bg-shell-red/80" />
        <span aria-hidden="true" className="size-[11px] shrink-0 rounded-full bg-white/[0.18]" />
        <span aria-hidden="true" className="size-[11px] shrink-0 rounded-full bg-white/[0.18]" />
        {bar ?? <span className="ml-2 truncate text-[12px] text-white/40">{title}</span>}
      </div>
      <div className={bodyClassName}>{children}</div>
    </div>
  );
}

/**
 * One `$ command` with a copy button, and whatever it printed beneath.
 *
 * The command scrolls inside its own row and the button never scrolls with
 * it — a copy control that slides off with a long line is one nobody finds.
 * The `$` is `select-none` and absent from the copied value, so neither a
 * drag-select nor the button picks up a prompt the shell would reject.
 */
export function CommandLine({
  cmd,
  output,
  note,
}: {
  cmd: string;
  output?: readonly string[];
  note?: string;
}) {
  return (
    <div className="pb-3 last:pb-0">
      <div className="flex items-center gap-3">
        <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap py-0.5 text-white [scrollbar-width:thin]">
          <span aria-hidden="true" className="select-none text-shell-dim">
            ${" "}
          </span>
          {cmd}
        </code>
        <CopyButton value={cmd} tone="shell" iconOnly className="px-2 py-1.5" />
      </div>
      {output && output.length > 0 && (
        <pre className="m-0 mt-0.5 whitespace-pre-wrap break-words font-mono text-[12.5px] text-shell-dim">
          {output.join("\n")}
        </pre>
      )}
      {note && (
        <p className="m-0 mt-1 font-sans text-[12.5px] leading-snug text-shell-dim">{note}</p>
      )}
    </div>
  );
}

/**
 * A small terminal for one step: a number and a title in the bar, the command
 * below.
 *
 * The home page's first-steps grid is six of these rather than one long
 * terminal, because each step is a different moment — before install, inside
 * omega, in a clone — and one window would imply they run in sequence in the
 * same shell. They do not.
 */
export function ShellCard({
  step,
  title,
  cmd,
  output,
  note,
}: {
  step: number;
  title: string;
  cmd: string;
  output?: readonly string[];
  note?: string;
}) {
  return (
    <Terminal
      className="h-full"
      bar={
        <span className="ml-2 truncate text-[12px] text-white/40">
          <span className="tnum text-white/60">{String(step).padStart(2, "0")}</span> · {title}
        </span>
      }
    >
      <CommandLine cmd={cmd} output={output} note={note} />
    </Terminal>
  );
}
