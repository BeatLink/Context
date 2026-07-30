"""Window-manager backends.

`detect()` picks the backend that can drive the running session. Hyprland is
preferred when present; Cinnamon is the fallback for development on X11.
"""

from __future__ import annotations

import os

from .base import Backend, NullBackend, Workspace
from .cinnamon import CinnamonBackend
from .hyprland import HyprlandBackend

BACKENDS: dict[str, type] = {
    "hyprland": HyprlandBackend,
    "cinnamon": CinnamonBackend,
    "none": NullBackend,
}

ENV_OVERRIDE = "CONTEXT_BACKEND"


def detect(preferred: str | None = None) -> Backend:
    name = preferred or os.environ.get(ENV_OVERRIDE)
    if name:
        factory = BACKENDS.get(name.strip().casefold())
        if factory is not None:
            candidate = factory()
            if candidate.available():
                return candidate
            return NullBackend()

    for factory in (HyprlandBackend, CinnamonBackend):
        candidate = factory()
        if candidate.available():
            return candidate
    return NullBackend()


__all__ = [
    "Backend",
    "BACKENDS",
    "CinnamonBackend",
    "HyprlandBackend",
    "NullBackend",
    "Workspace",
    "detect",
]
