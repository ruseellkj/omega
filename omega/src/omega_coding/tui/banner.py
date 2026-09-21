"""The first screen: a wordmark, and the six facts you need before typing.

## Why a banner at all, when neither reference has one

Tau has no ASCII art anywhere in its 10,828-line `tui/` — verified, there is not
a single box-drawing character in it. Pi has a block-character logo but shows it
only during first-run setup (`first-time-setup.ts:29`), never on an ordinary
launch. So this is omega's own choice, not parity, and it is worth saying why:

**an empty box does not say what it is.** A terminal UI that opens to a blank
pane and a cursor is indistinguishable from a hung program, and the moment
before you type is the only moment you are looking for orientation.

## The six facts, and why each earns a row

The banner is decoration; the block under it is not. Each line answers a
question you would otherwise have to run a command to answer — and two of them
answer questions people do not think to ask until it is too late:

| Row | The question it answers |
|---|---|
| `model` | which model am I about to spend money on |
| `path` | **what can it reach** — the approval gate is relative to this |
| `branch` | am I about to let it edit the branch I think I am on |
| `approval` | **is every write auto-approved right now** |
| `session` | what do I pass to `--resume` |
| `version` | which omega is this, when the source tree and the install disagree |

`approval` is the one that matters most and reads as the most boring. `--yes`
means unprompted writes anywhere the path fence allows; a session started with it
looks exactly like one that was not, until something is overwritten.

## Everything here degrades rather than fails

A banner cannot be a reason omega does not start. `git` may be absent, the
directory may not be a repository, the package may not be installed under a name
`importlib.metadata` knows. Each of those returns a dash and the screen still
draws.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from omega_coding import auth
from omega_coding.version import omega_version

ENV_KNOWN = auth.ENV_VARS

#: The wordmark — the **ANSI Shadow** letterform, which is exactly what
#: `npx oh-my-logo "OMEGA" --filled` emits. Verified by running it and stripping
#: the escapes; this is that output, character for character.
#:
#: ## Why the glyphs are pasted and the colour is not
#:
#: `oh-my-logo` bakes a truecolor gradient into its output as per-character
#: escape sequences. Keeping those would have meant two problems:
#:
#: * **a Node dependency at runtime**, in a Python project, for a decorative
#:   string that never changes
#: * **a fixed palette**, which would fight `/theme` — the banner would stay blue
#:   on a grey theme and on an oxblood one
#:
#: So the letterform is stored as plain text and `gradient()` below recolours it
#: from whichever theme is live. Same look, no dependency, and it follows the
#: rest of the UI.
#:
#: ## On the horizontal breaks
#:
#: This font's segmented look comes from the glyphs themselves — `╔═╗` and `║`
#: already cut each letter into parts. Inserting gap rows on top of that was
#: tried and made the letters fall apart; drawing hairlines between them buried
#: the word. The tile letterform in the reference screenshot is a *different*
#: typeface, not this one with gaps added.
BANNER = """\
  ██████╗  ███╗   ███╗ ███████╗  ██████╗   █████╗
 ██╔═══██╗ ████╗ ████║ ██╔════╝ ██╔════╝  ██╔══██╗
 ██║   ██║ ██╔████╔██║ █████╗   ██║  ███╗ ███████║
 ██║   ██║ ██║╚██╔╝██║ ██╔══╝   ██║   ██║ ██╔══██║
 ╚██████╔╝ ██║ ╚═╝ ██║ ███████╗ ╚██████╔╝ ██║  ██║
  ╚═════╝  ╚═╝     ╚═╝ ╚══════╝  ╚═════╝  ╚═╝  ╚═╝"""


#: The mascot: a pixel omega with two eyes.
#:
#: **Nine columns, five rows, and both numbers are constraints rather than
#: taste.** It sits to the left of three lines of text in the compact header, so
#: it cannot be taller than three plus the two the name and path need; and a
#: terminal cell is roughly half as wide as it is tall, so a shape that looks
#: square on a grid of characters has to be about twice as wide as it is high.
#: Drawn at 5×5 first, it read as a blob.
#:
#: The legs splay outward because that is what distinguishes an omega from a
#: zero at this size — the counter alone does not survive five rows.
MASCOT = """\
 ▄█████▄
██ ▀ ▀ ██
██     ██
 ▀█   █▀
██▄   ▄██"""


def mascot_lines(colour: str) -> list[str]:
    """The mascot as Rich markup, one entry per row.

    Returned as a list rather than a blob because the compact header lays it
    beside text: each row has to be paired with the line that sits next to it.
    """
    return [f"[{colour}]{line}[/]" for line in MASCOT.split("\n")]


def compact_header(
    *, version: str, model: str, provider: str, path: str, colour: str, muted: str
) -> str:
    """The three-line block that replaces the splash once a conversation starts.

    **A splash earns its space exactly once.** Six facts in a bordered box are
    worth six rows before you have asked anything and worth nothing afterwards,
    when the rows belong to the answer. Claude Code and Pi both solve it the same
    way — a tall opening, then a two or three line identity block that stays.

    What survives the shrink is what you would actually want mid-session: which
    build, which model you are spending on, and where you are. The rest —
    approval mode, session id, git branch — is a startup decision you already
    made, and `ctrl+o` brings it back.
    """
    rows = mascot_lines(colour)
    # `"none"` is the status line's word for "signed in to nothing", and it reads
    # correctly in a labelled column. Beside a model name it reads as a provider
    # *called* none, which is worse than saying nothing — so the header says the
    # thing it actually means.
    signed_in = provider not in ("", "none")
    suffix = f" [{muted}]· {provider}[/]" if signed_in else f" [{muted}]· not signed in[/]"
    text = [
        f"[{colour}]omega[/] [{muted}]{version}[/]",
        f"[{muted}]{model}[/]{suffix}",
        f"[{muted}]{path}[/]",
    ]
    # The mascot is five rows and the text is three: pad the text so the pairing
    # is top-aligned rather than the mascot being cropped.
    text = ["", *text, ""]
    return "\n".join(f"{art}  {line}".rstrip() for art, line in zip(rows, text, strict=True))


def _mix(start: str, end: str, position: float) -> str:
    """One step along a two-colour ramp, as `#rrggbb`."""
    a = tuple(int(start.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    b = tuple(int(end.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    blended = (round(x + (y - x) * position) for x, y in zip(a, b, strict=True))
    return "#" + "".join(f"{value:02x}" for value in blended)


def gradient(text: str, start: str, end: str) -> str:
    """Rich markup painting `text` left-to-right along a colour ramp.

    Per **column**, not per character, so the ramp runs straight down the banner
    rather than stepping wherever a line happens to be shorter. That is the
    difference between a gradient and a stripe.

    Blank cells are emitted bare: wrapping a space in colour markup costs bytes
    and paints nothing.
    """
    lines = text.split("\n")
    width = max((len(line) for line in lines), default=1)
    painted = []
    for line in lines:
        out = ""
        for column, char in enumerate(line):
            if char == " ":
                out += " "
            else:
                out += f"[{_mix(start, end, column / max(width - 1, 1))}]{char}[/]"
        painted.append(out)
    return "\n".join(painted)


#: How long to wait for `git`. A branch name is a nicety; a UI that will not
#: paint because a network filesystem is slow is not.
GIT_TIMEOUT_SECONDS = 0.5




def git_branch(root: Path) -> str:
    """The current branch, or a dash if this is not a repository.

    Shelling out rather than parsing `.git/HEAD` by hand: detached heads,
    worktrees and submodules each have their own layout, and `git` already knows
    all of them. Every failure mode — missing binary, not a repo, slow disk —
    lands on the same dash.
    """
    try:
        finished = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "—"
    branch = finished.stdout.strip()
    return branch if finished.returncode == 0 and branch else "—"


def short_path(path: Path) -> str:
    """`~/Desktop/cli-agent` rather than the full home directory.

    Not cosmetic: the full path pushes the value off a narrow terminal, and the
    part that identifies the directory is the tail.
    """
    home = Path.home()
    try:
        relative = path.relative_to(home)
    except ValueError:
        return str(path)
    # `Path.home().relative_to(home)` is `.`, so the naive form renders the home
    # directory as `~/.` — which is what the header showed for anyone who ran
    # omega from `~`. Reported from a screenshot, not caught by a test, because
    # every test ran from a temporary directory.
    return "~" if str(relative) == "." else f"~/{relative}"


def approval_label(*, auto_approve: bool, confine: bool) -> str:
    """What the gate will do, in words, with the dangerous case stated first.

    Short enough to sit in a column. The long version used to be clipped to
    "asks before writing or ru…", which is worse than a shorter sentence that
    fits — an ellipsis in the one row about safety reads as though something is
    being hidden.
    """
    if auto_approve:
        return "auto-approved (--yes)"
    if confine:
        return "asks, confined here"
    return "asks before changes"


def startup_facts(
    *,
    model: str,
    provider: str,
    root: Path,
    session_id: str | None,
    auto_approve: bool,
    confine: bool,
) -> list[tuple[str, str]]:
    """The label/value pairs under the wordmark, in the order they are drawn.

    `signed in` earns its row for the same reason `approval` does: it answers a
    question you would otherwise only discover by asking omega something and
    being refused. `auth.source` returns where the credential came from and
    **never the credential** — that string is on screen and in screenshots.
    """
    return [
        ("model", model),
        # The provider lives on this row rather than the one above, so neither
        # is clipped: `claude-sonnet-5 via anthropic` is 29 characters against a
        # 25-wide column, and the half that survived was the half you already
        # knew from the flag you typed.
        (
            "signed in",
            f"{provider} · {auth.source(provider)}" if provider in ENV_KNOWN else provider,
        ),
        ("path", short_path(root)),
        ("branch", git_branch(root)),
        ("approval", approval_label(auto_approve=auto_approve, confine=confine)),
        ("session", session_id or "new"),
        ("version", omega_version()),
    ]
