import { Terminal } from "@/components/site/terminal";

/**
 * The hero's terminal: one turn of omega's terminal UI.
 *
 * The words are from a real capture — `OmegaApp` driven through Textual's pilot
 * in an empty `git init`ed folder, with the scripted `FakeProvider` answering.
 * The header is `tui/banner.py`'s compact header, the tool rows are
 * `status._VERBS` in the past tense. The layout is simplified — the pixel
 * mascot and the `▶` row markers are left out. The reply is the one authored line,
 * because a scripted provider's reply is authored by definition, and the
 * caption says so.
 *
 * Static on purpose. An earlier version replayed the turn — typing, spinner,
 * streamed reply — in the TUI's own oxblood theme. It read as decoration rather
 * than as a terminal, and a reader can take in a finished turn at a glance,
 * which is the whole job of a hero figure. This is Tau's session terminal
 * (research/tau/website/layouts/index.html, the origin section) in shape: a
 * prompt, the steps, what came back.
 */
const TOOLS = [
  { verb: "listed", target: "." },
  { verb: "read", target: "README.md" },
] as const;

export function SessionSnapshot() {
  return (
    <figure className="m-0 min-w-0">
      <Terminal title="omega — ~/my-project" bodyClassName="px-5 py-5 text-[13px] sm:px-6 sm:text-[13.5px]">
        <div className="text-shell-dim">
          <div>
            <span className="text-white">omega</span> v0.1.0
          </div>
          <div>claude-sonnet-5 · anthropic</div>
          <div>~/my-project</div>
        </div>

        <div className="mt-5">
          <span className="text-shell-key">❯</span>{" "}
          <span className="text-white">what does this project do?</span>
        </div>

        <ul className="m-0 mt-3 list-none p-0">
          {TOOLS.map((t) => (
            <li key={t.target} className="whitespace-pre">
              <span className="text-shell-dim">{`  ✓ ${t.verb.padEnd(7)}`}</span>
              <span className="text-shell-key">{t.target}</span>
            </li>
          ))}
        </ul>

        <p className="m-0 mt-3">
          It converts a CSV file to JSON. main.py is the whole program, and tests/ holds one test.
        </p>

        <div className="mt-5 border-t border-white/[0.07] pt-3">
          <span className="text-shell-key">❯</span>{" "}
          <span className="text-shell-dim">Ask anything, or / for commands</span>
        </div>
      </Terminal>
      <figcaption className="mt-3 text-sm text-ink-muted">
        One turn of the terminal UI, from a real capture. The scripted{" "}
        <code className="whitespace-nowrap font-mono text-[0.85em] text-ink">--fake</code> provider wrote the reply;
        the rest is what omega printed.
      </figcaption>
    </figure>
  );
}
