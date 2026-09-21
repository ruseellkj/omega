"""Shared fixtures.

## Why this file exists at all

A test wrote to the real `~/.omega/tui.json`. It passed, and then made the *next*
run fail: `/theme oxblood-light` is not a change if oxblood-light is already what
loaded from disk. So the failure appeared one run later than the cause, in a test
that had not been touched — the worst shape a test bug can have.

The rule this encodes: **a test may not write anywhere a user keeps state.** The
suite is meant to be runnable on a laptop without leaving a trace, and every
other path in it is already a `tmp_path`. The remembered-theme file was the one
that reached for `Path.home()` on its own.

`~/.omega/models.json` then became the second, and it is worse: the theme file
only had to be *written* to cause trouble, whereas the model overlay causes it
by merely **existing**. A developer who had added one model to their own omega
would watch `test_the_longest_prefix_wins_for_windows` fail on a clean
checkout — measured, not supposed:

    $ echo '{"anthropic": [{"name": "claude-sonnet-5", "window": 12345}]}' \
        > ~/.omega/models.json
    $ uv run pytest -q tests/test_models.py
    E       assert 12345 == 200000
    2 failed, 12 passed

So every path that reads `Path.home()` gets a fixture here, and the reason is
the same each time: the suite must describe omega, not the laptop it runs on.

**It happened a third time**, which is why there is now a test instead of a
promise. Adding `models.cache_path()` for `/model refresh` created a second
home-reaching function and the fixture only covered the first, so the refresh
tests wrote a fixture-derived catalog into the developer's real
`~/.omega/models-cache.json`. Nothing failed — the pollution was invisible until
one assertion happened to check the file was absent. `ISOLATED_MODEL_PATHS`
below is checked against the module's source, so the next function like this
cannot be forgotten: the suite names it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omega_coding import models
from omega_coding.tui import config


@pytest.fixture(autouse=True)
def _isolate_tui_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the remembered-theme file at a temporary directory.

    Autouse rather than opt-in: the write happens inside `_switch_theme`, two
    calls below anything a test names, so a test author has no reason to suspect
    they need it.
    """
    monkeypatch.setattr(config, "config_path", lambda: tmp_path / "tui.json")


@pytest.fixture(autouse=True)
def _isolate_model_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the model overlay at a path that does not exist.

    Autouse for the same reason as the theme file, and one more: a test does not
    have to *touch* the overlay to be affected by it. `models.window_for` reads
    it on every call, so a developer's own extra model silently redefines what
    the built-in catalog says — which is the assertion half these tests make.

    The file deliberately is not created. Absent is the state the built-ins are
    specified against, and a test that wants an overlay writes one itself.
    """
    monkeypatch.setattr(models, "overlay_path", lambda: tmp_path / "models.json")
    monkeypatch.setattr(models, "cache_path", lambda: tmp_path / "models-cache.json")


#: Every `models` function that reaches `Path.home()`, and the fixture above has
#: to redirect all of them. `tests/test_models.py` fails if this list and the
#: module's actual `Path.home()` users diverge — see
#: `test_every_home_path_in_models_is_isolated`.
ISOLATED_MODEL_PATHS = ("overlay_path", "cache_path")
