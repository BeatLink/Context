"""Small bits of interface state that should survive a restart.

Not configuration — the user never edits this. It records what they last did, so
a collapsed sidebar comes back collapsed instead of reclaiming the screen every
time Context restarts.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from context.system.logging_setup import get_logger

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


RECENT_APPS_KEY = "recent_apps"
RECENT_APPS_LIMIT = 200


def note_app(app_id: str, when: float | None = None) -> dict[str, float]:
    """Record when an application was launched.

    When, not merely in what order: the overview groups the grid by how long
    ago you used something — just now, an hour ago, three days ago — and an
    ordering cannot answer that.
    """
    if not app_id:
        return app_times()
    times = app_times()
    times[app_id] = time.time() if when is None else when
    # Oldest out first, so a machine that has launched everything once does not
    # grow this file without limit.
    if len(times) > RECENT_APPS_LIMIT:
        keep = sorted(times.items(), key=lambda pair: -pair[1])[:RECENT_APPS_LIMIT]
        times = dict(keep)
    save(**{RECENT_APPS_KEY: times})
    return times


def app_times() -> dict[str, float]:
    """Application id -> when it was last launched, as epoch seconds."""
    raw = get(RECENT_APPS_KEY, {})
    if isinstance(raw, list):
        # The first shape this took was an order with no times. Nothing can be
        # said about when, so it is dropped rather than guessed at.
        return {}
    if not isinstance(raw, dict):
        return {}
    times: dict[str, float] = {}
    for app_id, when in raw.items():
        try:
            times[str(app_id)] = float(when)
        except (TypeError, ValueError):
            continue
    return times


def recent_apps() -> list[str]:
    """Application ids, most recently launched first."""
    return [app_id for app_id, _ in sorted(app_times().items(), key=lambda p: -p[1])]


def previous_context(current_id: str | None) -> str | None:
    """The context to alt-tab back to: the last one that is not this one."""
    for context_id in get(RECENT_KEY, []):
        if isinstance(context_id, str) and context_id != current_id:
            return context_id
    return None
