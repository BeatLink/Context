"""Small bits of interface state that should survive a restart.

Not configuration — the user never edits this. It records what they last did, so
a collapsed sidebar comes back collapsed instead of reclaiming the screen every
time Context restarts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .logging_setup import get_logger

log = get_logger("uistate")

ENV_PATH = "CONTEXT_UI_STATE"


def state_path() -> Path:
    override = os.environ.get(ENV_PATH)
    if override:
        return Path(override)
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "context" / "ui.json"


def load() -> dict:
    path = state_path()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        # Never let unreadable state stop the launcher from starting.
        log.warning("ignoring %s: %s", path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def save(**values) -> None:
    path = state_path()
    merged = load() | values
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(merged, indent=2))
        temporary.replace(path)
    except OSError as exc:
        log.warning("could not write %s: %s", path, exc)


def get(key: str, default=None):
    return load().get(key, default)


RECENT_KEY = "recent_contexts"
RECENT_LIMIT = 16


def note_visit(context_id: str) -> list[str]:
    """Record that a context was switched to, most recent first.

    Kept here rather than on the contexts themselves: it is the order the user
    moved through them, not a property of any one of them, and it should not
    rewrite `contexts.json` on every switch.
    """
    recent = [i for i in get(RECENT_KEY, []) if isinstance(i, str) and i != context_id]
    recent.insert(0, context_id)
    del recent[RECENT_LIMIT:]
    save(**{RECENT_KEY: recent})
    return recent


def previous_context(current_id: str | None) -> str | None:
    """The context to alt-tab back to: the last one that is not this one."""
    for context_id in get(RECENT_KEY, []):
        if isinstance(context_id, str) and context_id != current_id:
            return context_id
    return None
