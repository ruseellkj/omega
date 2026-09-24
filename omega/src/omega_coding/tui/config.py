"""What the UI remembers between runs.

Two settings: the theme, and whether selecting text copies it. They live in
`~/.omega/tui.json` rather than in the session file because they are about
*you*, not about a conversation: switching theme and then resuming a session
should not switch it back.

**Deliberately its own file and not part of a general settings system.** omega
has no config file (`PRODUCT-BACKLOG.md` records that as future work), and two
keys in one JSON object do not yet justify one. Both go through `_read` and
`_write`, so a third is one pair of functions, not a new mechanism.

Every failure here is non-fatal. A corrupt file, an unreadable directory, a
read-only home — none of them are reasons to refuse to start an editor, so each
falls back to the default and says nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Whether selecting text copies it, when nothing has been saved.
#:
#: **On, and Tau's is off** (`tau_coding/tui/config.py:92`). Tau treats auto-copy
#: as an opt-in. omega turns it on because it was asked for as the default, and
#: because in a Textual app the terminal's own selection is gone: mouse
#: reporting sends the drag to the app, so the terminal never gets it. With the
#: setting off, the drag still highlights and ctrl+c copies it.
AUTO_COPY_DEFAULT = True


def config_path() -> Path:
    """Where the file lives. `~/.omega/` is already where logs go."""
    return Path.home() / ".omega" / "tui.json"


def _read() -> dict[str, Any]:
    """The whole file as a dict, or `{}` for anything unusable."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(key: str, value: Any) -> None:
    """Set one key. Silent on failure, by design — see the module docstring."""
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Read-modify-write rather than overwrite, so that a future setting added
        # by a newer omega is not deleted by an older one.
        existing = _read()
        existing[key] = value
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return


def load_theme_name(default: str) -> str:
    """The remembered theme, or `default` if there is not a usable one."""
    name = _read().get("theme")
    return name if isinstance(name, str) and name else default


def save_theme_name(name: str) -> None:
    """Remember a theme."""
    _write("theme", name)


def load_auto_copy() -> bool:
    """Whether selecting text copies it. Anything but a real bool is the default."""
    value = _read().get("auto_copy")
    return value if isinstance(value, bool) else AUTO_COPY_DEFAULT


def save_auto_copy(enabled: bool) -> None:
    """Remember the auto-copy setting."""
    _write("auto_copy", enabled)
