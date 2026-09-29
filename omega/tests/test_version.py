"""Reporting a version, and the two places it must not cost anything.

`--version` is the one flag an installer calls to prove the thing it just put
on disk actually runs (`install.sh` does exactly that). So it has to work
before credentials, before a provider, and before Textual — which is most of
what these tests check.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import omega_agent
from omega_coding import version as version_module


def test_the_core_package_states_the_version_in_pyproject() -> None:
    """**It said 0.2.0 while 0.1.0 was on PyPI.** `omega_agent.__version__` was
    set when omega split into three packages and never touched again, so the
    one version a library user reads was wrong in the release itself.

    It stays a constant rather than reading package metadata: `omega_agent`
    must not know which distribution carries it (`omega_coding.version` tries
    three names), and metadata is only current after a reinstall. So it is
    pinned here to `pyproject.toml`, and a version bump that misses it fails.
    """
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())

    assert omega_agent.__version__ == pyproject["project"]["version"]


def test_the_distribution_is_the_new_name_and_both_old_ones_still_resolve() -> None:
    """**`omega` is taken on PyPI** (v0.4.0, an unrelated games library), so the
    package publishes as `omega-coding-agent` while the command stays `omega`.

    Both older names are still tried, because each is installed somewhere. The
    distribution was `omega-coding` until 2026-09-25 and `install.sh` installed
    it straight from git, so anyone who ran the one-liner before then has that
    name on disk; `omega` is older still. Reporting "(from source)" for either
    would be a worse answer than the truth.

    Newest first, so a machine carrying two of them answers with the one
    installed most recently.
    """
    assert version_module.DISTRIBUTIONS == ("omega-coding-agent", "omega-coding", "omega")
    assert version_module.omega_version() != version_module.UNKNOWN


def test_a_source_checkout_says_so_rather_than_failing() -> None:
    """`uv run omega` from a clone is the documented development path, so no
    installed distribution is an ordinary state, not an error."""
    assert version_module.UNKNOWN == "(from source)"

    original = version_module.DISTRIBUTIONS
    try:
        version_module.DISTRIBUTIONS = ("definitely-not-installed-anywhere",)
        assert version_module.omega_version() == "(from source)"
    finally:
        version_module.DISTRIBUTIONS = original


def test_version_needs_no_credentials_and_no_provider() -> None:
    """`install.sh` runs this as its verification step, on a machine that has
    never been logged in. If it needed a key the installer would fail on a fresh
    install — which is the only kind an installer sees.
    """
    finished = subprocess.run(
        [sys.executable, "-m", "omega_coding.cli", "--version"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent-home-for-this-test"},
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.startswith("omega "), finished.stdout


def test_version_does_not_import_textual() -> None:
    """**The reason `version.py` is not inside `tui/`.**

    Importing `omega_coding.tui.banner` runs `tui/__init__.py`, which imports
    `app.py`, which imports Textual — ~160ms measured. `omega --version` and
    every `-p` run in a script would have paid it for a string.
    """
    finished = subprocess.run(
        [
            sys.executable,
            "-c",
            "import omega_coding.version, sys; "
            "print(sum(1 for m in sys.modules if m.startswith('textual')))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == "0", "importing the version pulled in Textual"
