"""The layer boundaries, enforced instead of described.

Until Tier 2's restructure, "nothing points upward" was a sentence in
`dev-notes/03-architecture/04-boundaries-and-layout.md`. One flat package cannot
check it — every module can see every other one, and the rule survives on
discipline alone. Three packages make it mechanically checkable, so it should be
checked, and it should fail loudly the first time somebody takes a shortcut.

    omega_ai      knows one vendor's wire format, and nothing else
    omega_agent   knows messages, events, tools, turns. Not files. Not vendors.
    omega_coding  knows files, shells, policy, the screen

Imports are read with `ast`, not `grep`, and that distinction is load-bearing:
every one of these packages *mentions* the others in its module docstring while
importing none of them. A textual search reports those as violations, which is
how an architecture test becomes something people switch off.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

AGENT = SRC / "omega_agent"
AI = SRC / "omega_ai"
CODING = SRC / "omega_coding"

#: The concrete adapters. Naming one of these is a decision about *which* vendor,
#: which only a composition root is allowed to make.
CONCRETE_ADAPTERS = {
    "omega_ai.anthropic",
    "omega_ai.openai",
    "omega_ai.openai_codex",
    "omega_ai.fake",
}

#: Files that are allowed to make that decision, because building a runnable
#: agent out of parts is precisely their job.
COMPOSITION_ROOTS = {"cli.py", "evals.py"}


def _python_files(package: Path) -> list[Path]:
    return sorted(p for p in package.rglob("*.py") if "__pycache__" not in str(p))


def _imports(path: Path) -> set[str]:
    """Every module this file imports. Docstrings and comments do not count."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _offenders(package: Path, forbidden: tuple[str, ...]) -> list[str]:
    found = []
    for path in _python_files(package):
        for module in sorted(_imports(path)):
            if module.split(".")[0] in forbidden:
                found.append(f"{path.relative_to(SRC)} imports {module}")
    return found


# ------------------------------------------------------------ nothing upward


def test_the_core_imports_nothing_above_it() -> None:
    """The rule the whole design rests on.

    `omega_agent` holds the loop, the harness and the contract. If it imported
    an adapter, swapping providers would mean editing the loop — beginner
    failure #7, recreated by a directory layout. If it imported the coding app,
    the loop would know what a file is, and could no longer be tested without one.
    """
    assert _offenders(AGENT, ("omega_ai", "omega_coding")) == []


def test_the_provider_layer_does_not_know_the_app() -> None:
    """An adapter translates a wire format. It has no business knowing that
    files, approval gates or terminals exist."""
    assert _offenders(AI, ("omega_coding",)) == []


# ------------------------------------------------------- the inversion itself


def test_the_contract_lives_in_the_core_and_omega_ai_re_exports_it() -> None:
    """The consumer owns the interface.

    `omega_ai/provider.py` is five lines that import from `omega_agent`. The
    direction is the point: it goes *down*, and nothing comes back up.
    """
    assert "omega_agent.provider" in _imports(AI / "provider.py")
    assert "omega_ai" not in _imports(AGENT / "provider.py")


# ------------------------------------------------- who may choose a vendor


def test_only_composition_roots_name_a_concrete_adapter() -> None:
    """Everything else is written against the interface and cannot tell which
    provider it got.

    There are two roots, not one: `cli.py` builds an agent for a person and
    `evals.py` builds one for a measurement. Both have to pick something
    concrete; nothing else does.
    """
    naming = {
        path.name
        for package in (AGENT, AI, CODING)
        for path in _python_files(package)
        if _imports(path) & CONCRETE_ADAPTERS
    }
    assert naming == COMPOSITION_ROOTS


def test_exactly_two_files_import_a_vendor_sdk() -> None:
    """This was a `grep` in the README. A test fails; a README is remembered."""
    importers = {
        str(path.relative_to(SRC))
        for package in (AGENT, AI, CODING)
        for path in _python_files(package)
        if {"anthropic", "openai"} & {m.split(".")[0] for m in _imports(path)}
    }
    assert importers == {"omega_ai/anthropic.py", "omega_ai/openai.py"}


# ------------------------------------------------------------------ hygiene


def test_all_three_packages_exist_and_are_importable() -> None:
    for package in (AGENT, AI, CODING):
        assert package.is_dir(), package
        assert (package / "__init__.py").is_file(), package


def test_the_old_flat_package_is_gone() -> None:
    """A leftover `src/omega/` would shadow the split and still import cleanly,
    which is the kind of thing that survives for months."""
    assert not (SRC / "omega").exists()
