"""What version of omega this is.

Its own module, and the reason is import cost. The obvious home was
`tui/banner.py`, which already showed the version in the startup facts — but
importing `omega_coding.tui.banner` runs `omega_coding/tui/__init__.py`, which
imports `app.py`, which imports Textual. That is ~160ms measured, and it would
be paid by `omega --version` and by every `-p` run in a script, for a string.

So this sits outside `tui/` and both callers import it: the banner for the facts
block, and `cli.py` for `--version`.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

#: Distribution names to try, newest first. **`omega` is taken on PyPI** (v0.4.0,
#: a games library), so the package publishes as `omega-coding` while the command
#: stays `omega` — a distribution name and a console script are different things.
#:
#: The old name is still tried because an `omega` installed before the rename is
#: a real thing sitting on someone's machine, and reporting a dash for it would
#: be a worse answer than the truth.
DISTRIBUTIONS = ("omega-coding", "omega")

#: Shown when omega is running from a source tree that was never installed. Not
#: an error: `uv run omega` is the documented development path.
UNKNOWN = "(from source)"


def omega_version() -> str:
    """The installed version, or a note that there isn't one."""
    for name in DISTRIBUTIONS:
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    return UNKNOWN
