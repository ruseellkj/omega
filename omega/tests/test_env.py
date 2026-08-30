"""Finding the API key from any directory.

The bug this covers only appears once omega is installed as a real command:
Tier 2 read one `.env`, the one beside its own source, so running the agent
anywhere else reported a missing key while the key sat in a file it would not
look at.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from omega_coding.env import find_env_files, load_environment


@pytest.fixture
def clean_env() -> Iterator[None]:
    """Restore os.environ afterwards.

    load_dotenv mutates the real environment, and monkeypatch cannot undo a
    variable it never saw being set.
    """
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


# ------------------------------------------------------------- search order


def test_it_looks_in_the_current_directory_first(tmp_path: Path) -> None:
    found = find_env_files(tmp_path / "a" / "b", home=tmp_path)

    assert found[0] == tmp_path / "a" / "b" / ".env"


def test_it_walks_up_to_your_home_directory(tmp_path: Path) -> None:
    found = find_env_files(tmp_path / "work" / "api", home=tmp_path)

    assert found[:3] == [
        tmp_path / "work" / "api" / ".env",
        tmp_path / "work" / ".env",
        tmp_path / ".env",
    ]


def test_it_stops_at_home_and_does_not_climb_past_it(tmp_path: Path) -> None:
    """Reading a stray .env from somewhere above your home is nobody's intent."""
    home = tmp_path / "home" / "someone"
    home.mkdir(parents=True)

    found = find_env_files(home / "project", home=home)

    assert all(tmp_path in path.parents or path == tmp_path / ".env" for path in found)
    assert (tmp_path / "home" / ".env") not in found


def test_the_user_level_config_is_the_last_resort(tmp_path: Path) -> None:
    found = find_env_files(tmp_path / "project", home=tmp_path)

    assert found[-1] == tmp_path / ".config" / "omega" / ".env"


def test_a_project_outside_home_still_walks_upward(tmp_path: Path) -> None:
    """Not every project lives under your home directory."""
    elsewhere = tmp_path / "srv" / "code"
    home = tmp_path / "home"

    found = find_env_files(elsewhere, home=home)

    assert found[0] == elsewhere / ".env"
    assert (tmp_path / "srv" / ".env") in found


# ---------------------------------------------------------------- loading


def test_it_loads_a_key_from_the_current_directory(tmp_path: Path, clean_env: None) -> None:
    os.environ.pop("OMEGA_TEST_KEY", None)
    (tmp_path / ".env").write_text("OMEGA_TEST_KEY=from-cwd\n")

    loaded = load_environment(tmp_path, home=tmp_path)

    assert os.environ["OMEGA_TEST_KEY"] == "from-cwd"
    assert loaded == [tmp_path / ".env"]


def test_it_finds_a_key_several_directories_up(tmp_path: Path, clean_env: None) -> None:
    """The case that made this necessary: run omega deep inside a project."""
    os.environ.pop("OMEGA_TEST_KEY", None)
    (tmp_path / ".env").write_text("OMEGA_TEST_KEY=from-the-root\n")
    deep = tmp_path / "src" / "pkg" / "sub"
    deep.mkdir(parents=True)

    load_environment(deep, home=tmp_path)

    assert os.environ["OMEGA_TEST_KEY"] == "from-the-root"


def test_the_nearest_file_wins(tmp_path: Path, clean_env: None) -> None:
    os.environ.pop("OMEGA_TEST_KEY", None)
    (tmp_path / ".env").write_text("OMEGA_TEST_KEY=outer\n")
    inner = tmp_path / "project"
    inner.mkdir()
    (inner / ".env").write_text("OMEGA_TEST_KEY=inner\n")

    load_environment(inner, home=tmp_path)

    assert os.environ["OMEGA_TEST_KEY"] == "inner"


def test_further_files_still_fill_the_gaps(tmp_path: Path, clean_env: None) -> None:
    """A project .env overrides your key without discarding everything else."""
    os.environ.pop("OMEGA_TEST_KEY", None)
    os.environ.pop("OMEGA_TEST_OTHER", None)
    (tmp_path / ".env").write_text("OMEGA_TEST_KEY=outer\nOMEGA_TEST_OTHER=inherited\n")
    inner = tmp_path / "project"
    inner.mkdir()
    (inner / ".env").write_text("OMEGA_TEST_KEY=inner\n")

    load_environment(inner, home=tmp_path)

    assert os.environ["OMEGA_TEST_KEY"] == "inner"
    assert os.environ["OMEGA_TEST_OTHER"] == "inherited"


def test_an_exported_variable_always_wins(tmp_path: Path, clean_env: None) -> None:
    """Explicit beats implicit. `KEY=x omega` must override every file on disk."""
    os.environ["OMEGA_TEST_KEY"] = "exported"
    (tmp_path / ".env").write_text("OMEGA_TEST_KEY=from-file\n")

    load_environment(tmp_path, home=tmp_path)

    assert os.environ["OMEGA_TEST_KEY"] == "exported"


def test_the_user_level_config_works_from_anywhere(tmp_path: Path, clean_env: None) -> None:
    """Set the key once; every project sees it. The point of the whole change."""
    os.environ.pop("OMEGA_TEST_KEY", None)
    config = tmp_path / ".config" / "omega"
    config.mkdir(parents=True)
    (config / ".env").write_text("OMEGA_TEST_KEY=global\n")
    project = tmp_path / "unrelated"
    project.mkdir()

    load_environment(project, home=tmp_path)

    assert os.environ["OMEGA_TEST_KEY"] == "global"


def test_no_env_file_anywhere_is_not_an_error(tmp_path: Path, clean_env: None) -> None:
    assert load_environment(tmp_path, home=tmp_path) == []
