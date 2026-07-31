"""Window-manager backends.

`detect()` picks the backend that can drive the running session. Hyprland is the
target; anywhere else falls back to `NullBackend`, where apps launch onto the
current workspace and contexts are names without containers.
"""

from __future__ import annotations

import os

from ..logging_setup import get_logger
from .base import Backend, MonitorInfo, NullBackend, WindowInfo, Workspace
from .hyprland import HyprlandBackend

log = get_logger("backends")

BACKENDS: dict[str, type] = {
    "hyprland": HyprlandBackend,
    "none": NullBackend,
}

ENV_OVERRIDE = "CONTEXT_BACKEND"


def detect(preferred: str | None = None) -> Backend:
    name = preferred or os.environ.get(ENV_OVERRIDE)
    if not name:
        from .. import settings

        chosen = settings.current().backend
        name = None if chosen == "auto" else chosen
    if name:
        factory = BACKENDS.get(name.strip().casefold())
        if factory is not None:
            candidate = factory()
            if candidate.available():
                return candidate
            return NullBackend()
        # A name that no longer exists shouldn't pin the session to nothing.
        log.warning("unknown backend %r; detecting instead", name)

    candidate = HyprlandBackend()
    if candidate.available():
        return candidate
    return NullBackend()


__all__ = [
    "Backend",
    "BACKENDS",
    "HyprlandBackend",
    "MonitorInfo",
    "NullBackend",
    "WindowInfo",
    "Workspace",
    "detect",
]
