"""Finding your API key, from whatever directory omega was started in.

Tier 2 loaded exactly one `.env` — the one sitting next to the source tree. That
worked precisely as long as omega was only ever run from its own repository. The
moment it is installed as a real command and run somewhere else, that file is
nowhere near you, and the agent claims your key is missing while the key sits
happily in a file it refuses to look at.

So the search walks **outward from where you are**, nearest first:

    ./.env                      the project you are working in
    ../.env                     …and its parents, up to your home directory
    ~/.env
    ~/.config/omega/.env        set it once, works everywhere

Two rules make the result predictable:

**A variable you exported yourself always wins.** Everything is loaded with
`override=False`, so `ANTHROPIC_API_KEY=… omega` beats every file on disk. That
is the Unix expectation — explicit beats implicit — and it is what makes a
one-off override possible at all.

**Nearest file wins, further files fill gaps.** All of them are loaded, in order,
and the first to define a variable keeps it. So a project `.env` can override
your global key while still inheriting everything else from `~/.config/omega/.env`.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

#: Where to put a key once and have every project see it.
USER_CONFIG = Path(".config") / "omega" / ".env"


def _home() -> Path:
    try:
        return Path.home().resolve()
    except RuntimeError:
        # No resolvable home (a bare container, a stripped environment). The
        # walk upward still works; only the user-level fallback is lost.
        return Path("/")


def find_env_files(start: Path, home: Path | None = None) -> list[Path]:
    """Every `.env` worth checking, nearest first.

    Returns paths whether or not they exist — the caller filters. Keeping it
    pure like this is what makes the search order testable without a filesystem.
    """
    resolved_home = (home or _home()).resolve()
    candidates: list[Path] = []

    current = start.resolve()
    while True:
        candidates.append(current / ".env")
        # Stop at home when we are inside it; otherwise walk to the filesystem
        # root, since a project can perfectly well live outside your home.
        if current == resolved_home or current.parent == current:
            break
        current = current.parent

    user_level = resolved_home / USER_CONFIG
    if user_level not in candidates:
        candidates.append(user_level)
    return candidates


def load_environment(start: Path | None = None, home: Path | None = None) -> list[Path]:
    """Load every `.env` that exists, nearest first. Returns the ones it used.

    The return value is for telling the user which files were read — a key that
    silently came from three directories up is a key you will waste an afternoon
    on one day.
    """
    resolved_home = (home or _home()).resolve()
    loaded: list[Path] = []

    for candidate in find_env_files(start or Path.cwd(), resolved_home):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            loaded.append(candidate)

    return loaded
