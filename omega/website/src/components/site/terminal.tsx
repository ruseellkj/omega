import { CopyButton } from "@/components/site/interactive";

/**
 * A terminal window. Chrome only — the caller supplies the lines.
 *
 * Extracted because the same frame is wanted anywhere a command is shown, and
 * because the window dressing was drowning the hero's markup.
 */
export function Terminal({
  title = "omega",
  children,
  className = "",
}: {
  title?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-[5px] border border-rule bg-term font-mono text-[13px] leading-relaxed ${className}`}>
      <div className="flex items-center gap-1.5 border-b border-rule px-4 py-2.5">
        <span className="h-2 w-2 rounded-full bg-oxblood/40" />
        <span className="h-2 w-2 rounded-full bg-forest/40" />
        <span className="h-2 w-2 rounded-full bg-ink-muted/30" />
        <span className="label ml-2 truncate text-ink-muted">{title}</span>
      </div>
      <div className="px-4 py-3.5">{children}</div>
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
        <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap py-0.5 [scrollbar-width:thin]">
          <span aria-hidden="true" className="select-none text-oxblood">
            ${" "}
          </span>
          {cmd}
        </code>
        <CopyButton value={cmd} iconOnly className="px-2 py-1.5" />
      </div>
      {output && output.length > 0 && (
        <pre className="m-0 mt-1 whitespace-pre-wrap break-words font-mono text-[12.5px] text-forest">
          {output.join("\n")}
        </pre>
      )}
      {note && <p className="m-0 mt-1 font-sans text-[12.5px] leading-snug text-ink-muted">{note}</p>}
    </div>
  );
}

/**
 * A small terminal for one step: a number, what the step is, the command.
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
    <div className="group flex h-full min-w-0 flex-col rounded-[5px] border border-rule bg-term font-mono text-[13px] leading-relaxed transition-[border-color,box-shadow] duration-300 hover:border-rule-strong hover:shadow-[0_14px_30px_-22px_rgba(34,28,26,0.45)]">
      <div className="flex items-center gap-2.5 border-b border-rule px-4 py-2.5">
        <span className="tnum label text-oxblood">{String(step).padStart(2, "0")}</span>
        <span className="label truncate text-ink-muted transition-colors duration-200 group-hover:text-ink">
          {title}
        </span>
      </div>
      <div className="flex-1 px-4 py-3.5">
        <CommandLine cmd={cmd} output={output} note={note} />
      </div>
    </div>
  );
}
