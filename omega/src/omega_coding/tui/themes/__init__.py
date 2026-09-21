"""Colour, as data rather than code.

## Why JSON and not a Python dict

A theme is the one thing in omega a user should be able to change without
touching a `.py` file. Tau reached the same conclusion — "themes are data, not
code" (`research/tau/src/tau_coding/tui/themes/__init__.py:1-6`) — and ships JSON
that loads through the same parser as user themes. Copying that means a theme
someone writes is never a second-class citizen: it fails the same validation and
gets the same errors.

## Why this is a tenth of Tau's

Tau's loader is ~400 lines over **28 colour fields and 10 roles**, and validates
every value under both Rich and Textual because the same strings feed Textual CSS
variables and Rich style parsing. That is the right amount of machinery for a UI
with a sidebar, markdown tables, syntax highlighting and a completion menu.

omega has four row kinds and one pane. Nine colours and four roles cover it, and
the parser is small enough to read in one sitting. **The idea worth copying was
never the field list — it was `roles`:**

    "roles": { "user": { "border": "#6E2231", "body": "#221C1A on #F5F1E9" } }

A border colour per row kind is what draws the marker down the left of a message
and the dots beside each step. An empty border means *no rule at all*, which is
how assistant prose renders flush — Tau makes the same exception for the same
reason (`widgets.py:273`, `_BORDERLESS_TRANSCRIPT_ROLES`).

## The palette is not invented here

The three built-ins use the website's own values (`website/src/app/globals.css`),
so the terminal and the page are the same product rather than two things that
both happen to be dark red.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from importlib.resources import files
from json import JSONDecodeError, loads
from pathlib import Path
from typing import Any

from rich.color import Color, ColorParseError
from rich.errors import StyleSyntaxError
from rich.style import Style

#: The row kinds a theme must style. Mirrors `state.RowKind`; kept as a literal
#: tuple rather than imported so this module stays loadable on its own.
ROLES: tuple[str, ...] = ("user", "assistant", "tool", "notice")


class ThemeError(ValueError):
    """Raised when a theme file cannot be used."""


@dataclass(frozen=True, slots=True)
class RoleStyle:
    """How one kind of row is drawn.

    `border` is a single colour for the rule down its left edge, or `""` for no
    rule. `body` is a full Rich style string, so `"#221C1A on #F5F1E9"` sets both
    the text and the background behind it.
    """

    border: str
    body: str


@dataclass(frozen=True, slots=True)
class TuiTheme:
    """A resolved theme. Nine colours, four roles."""

    name: str
    dark: bool
    background: str
    surface: str
    foreground: str
    muted: str
    accent: str
    secondary: str
    success: str
    warning: str
    error: str
    roles: dict[str, RoleStyle]


#: Every field that names a colour — i.e. all of them except the metadata and the
#: roles. Derived from the dataclass so adding a field cannot be forgotten here.
COLOUR_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(TuiTheme) if f.name not in {"name", "dark", "roles"}
)


def _colour_problem(value: str) -> str | None:
    try:
        Color.parse(value)
    except ColorParseError:
        return f"is not a colour: {value!r}"
    return None


def _style_problem(value: str) -> str | None:
    try:
        Style.parse(value)
    except (StyleSyntaxError, ColorParseError):
        return f"is not a style: {value!r}"
    return None


def parse_theme(data: Any) -> TuiTheme:
    """Build a theme from JSON data, reporting **every** problem at once.

    Collecting problems rather than raising on the first one is deliberate: a
    hand-written theme usually has several typos, and fixing them one run at a
    time is the kind of loop that makes people give up and use the default.
    """
    if not isinstance(data, dict):
        raise ThemeError("a theme must be a JSON object")

    problems: list[str] = []

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("name must be a non-empty string")
        name = ""

    raw_colours = data.get("colors")
    colours: dict[str, str] = {}
    if not isinstance(raw_colours, dict):
        problems.append("colors must be an object")
    else:
        for missing in [f for f in COLOUR_FIELDS if f not in raw_colours]:
            problems.append(f"colors is missing {missing}")
        for unknown in sorted(set(raw_colours) - set(COLOUR_FIELDS)):
            problems.append(f"colors has no field {unknown}")
        for field_name in COLOUR_FIELDS:
            value = raw_colours.get(field_name)
            if not isinstance(value, str):
                continue
            problem = _colour_problem(value)
            if problem is not None:
                problems.append(f"colors.{field_name} {problem}")
            else:
                colours[field_name] = value

    raw_roles = data.get("roles")
    roles: dict[str, RoleStyle] = {}
    if not isinstance(raw_roles, dict):
        problems.append("roles must be an object")
    else:
        for missing_role in [r for r in ROLES if r not in raw_roles]:
            problems.append(f"roles is missing {missing_role}")
        for unknown_role in sorted(set(raw_roles) - set(ROLES)):
            problems.append(f"roles has no entry {unknown_role}")
        for role in ROLES:
            entry = raw_roles.get(role)
            if not isinstance(entry, dict) or set(entry) != {"border", "body"}:
                if role in raw_roles:
                    problems.append(f"roles.{role} needs exactly 'border' and 'body'")
                continue
            border, body = entry["border"], entry["body"]
            if not isinstance(border, str) or not isinstance(body, str):
                problems.append(f"roles.{role} border and body must be strings")
                continue
            # An empty border is the documented way to say "no rule", so it is
            # checked for emptiness before being checked for colour-ness.
            border_problem = _colour_problem(border) if border else None
            body_problem = _style_problem(body)
            if border_problem:
                problems.append(f"roles.{role}.border {border_problem}")
            if body_problem:
                problems.append(f"roles.{role}.body {body_problem}")
            if not border_problem and not body_problem:
                roles[role] = RoleStyle(border=border, body=body)

    dark = data.get("dark")
    if not isinstance(dark, bool):
        problems.append("dark must be true or false")
        dark = True

    for unknown_key in sorted(set(data) - {"name", "dark", "colors", "roles"}):
        problems.append(f"unknown field: {unknown_key}")

    if problems:
        label = f"theme {name!r}" if name else "theme"
        raise ThemeError(f"invalid {label}: " + "; ".join(problems))

    return TuiTheme(name=name, dark=dark, roles=roles, **colours)


def _builtin(filename: str) -> TuiTheme:
    return parse_theme(loads((files(__package__) / filename).read_text(encoding="utf-8")))


SLATE = _builtin("slate.json")
OXBLOOD_DARK = _builtin("oxblood-dark.json")
OXBLOOD_LIGHT = _builtin("oxblood-light.json")
HIGH_CONTRAST = _builtin("high-contrast.json")

BUILTIN: dict[str, TuiTheme] = {
    theme.name: theme for theme in (SLATE, OXBLOOD_DARK, OXBLOOD_LIGHT, HIGH_CONTRAST)
}

#: The one the app starts in.
#:
#: **Grey rather than oxblood.** The website is burgundy on bone and the first
#: themes followed it, which was the wrong inference: a page is looked at and a
#: terminal is worked in. A saturated hue on every row of a transcript competes
#: with the thing you are reading, and it leaves nothing louder for the two
#: colours that carry meaning — a failed tool, a warning — to be.
#:
#: The oxblood pair is still here and still one `/theme` away.
DEFAULT_THEME = SLATE.name

_custom: dict[str, TuiTheme] = {}


def load_custom(directory: Path) -> list[str]:
    """Load `*.json` themes from a directory. Returns what went wrong, if
    anything.

    **A broken theme must never stop omega starting.** Problems are collected and
    handed back for the caller to show as a notice; the themes that did parse are
    registered regardless. Losing your editor because one colour has a typo is a
    worse failure than a theme quietly not appearing.
    """
    problems: list[str] = []
    if not directory.is_dir():
        return problems
    for path in sorted(directory.glob("*.json")):
        try:
            theme = parse_theme(loads(path.read_text(encoding="utf-8")))
        except (OSError, JSONDecodeError, UnicodeDecodeError, ThemeError) as error:
            problems.append(f"{path.name}: {error}")
            continue
        if theme.name in BUILTIN:
            problems.append(f"{path.name}: {theme.name!r} is a built-in name and was skipped")
            continue
        _custom[theme.name] = theme
    return problems


def available() -> tuple[str, ...]:
    """Built-in names first, then custom ones, sorted."""
    return (*BUILTIN, *sorted(_custom))


def get(name: str) -> TuiTheme:
    """Look a theme up by name. Raises `KeyError` if there is no such theme."""
    if name in BUILTIN:
        return BUILTIN[name]
    return _custom[name]


def get_or_default(name: str) -> TuiTheme:
    """Look a theme up, falling back to the default rather than raising.

    Used at startup, where the name comes from a config file someone may have
    hand-edited, or from a custom theme that has since been deleted. Neither is
    a reason to refuse to start.
    """
    try:
        return get(name)
    except KeyError:
        return BUILTIN[DEFAULT_THEME]
