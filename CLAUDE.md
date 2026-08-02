# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

This is a fresh `uv`-managed Python project scaffold (`cli-agent`). It currently contains only the default `uv init` stub (`main.py` with a `main()` that prints a greeting) and no dependencies, tests, or additional modules yet. There is no established architecture to preserve — treat structural decisions as open when implementing new functionality.

## Commands

- Run the app: `uv run main.py`
- Add a dependency: `uv add <package>`
- Add a dev dependency: `uv add --dev <package>`
- Sync/install the environment from the lockfile: `uv sync`

Requires Python >=3.14 (pinned via `.python-version`). No test runner, linter, or formatter is configured yet — set one up (e.g. `pytest`, `ruff`) via `uv add --dev` before assuming it exists.
