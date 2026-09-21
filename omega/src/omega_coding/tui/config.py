"""What the UI remembers between runs.

One setting so far — the theme. It lives in `~/.omega/tui.json` rather than in
the session file because it is about *you*, not about a conversation: switching
theme and then resuming a session should not switch it back.

**Deliberately its own file and not part of a general settings system.** omega
has no config file (`TIER-3-PLUS.md` records that as future work), and inventing
one to hold a single string would be building the general case before there is a
second instance of it. When settings arrive, this is one key to move.

Every failure here is non-fatal. A corrupt file, an unreadable directory, a
read-only home — none of them are reasons to refuse to start an editor, so each
falls back to the default and says nothing.
"""

from __future__ import annotations

import json
from pathlib import Path


def config_path() -> Path:
    """Where the file lives. `~/.omega/` is already where logs go."""
    return Path.home() / ".omega" / "tui.json"


def load_theme_name(default: str) -> str:
    """The remembered theme, or `default` if there is not a usable one."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default
    name = data.get("theme") if isinstance(data, dict) else None
    return name if isinstance(name, str) and name else default


def save_theme_name(name: str) -> None:
    """Remember a theme. Silent on failure, by design — see the module docstring."""
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Read-modify-write rather than overwrite, so that a future setting added
        # by a newer omega is not deleted by an older one.
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        existing["theme"] = name
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return
