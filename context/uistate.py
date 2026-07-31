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
