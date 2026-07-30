"""Presenting the launcher as a persistent sidebar.

Layer-shell anchors the window to a screen edge and reserves space for it, so it
is not a window the compositor tiles — it sits alongside everything else and stays
put across workspace switches.

Only available on Wayland compositors implementing zwlr-layer-shell (Hyprland
does). Elsewhere `available()` is False and the launcher stays a normal window.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell
except (ImportError, ValueError):  # pragma: no cover - depends on the session
    LayerShell = None

EDGES = {
    "left": "LEFT",
    "right": "RIGHT",
    "top": "TOP",
    "bottom": "BOTTOM",
}

ENV_LIB = "CONTEXT_LAYER_SHELL_LIB"
_REEXEC_FLAG = "CONTEXT_LAYER_SHELL_PRELOADED"


def ensure_preloaded() -> None:
    """Re-exec with gtk4-layer-shell preloaded, if it isn't already.

    The library installs hooks into GDK and only works when it is loaded before
    libwayland-client; without that `is_supported()` is False and anchoring
    silently does nothing. Doing this in-process is not possible, so the whole
    program is restarted once with LD_PRELOAD set.
    """
    import sys

    if os.environ.get(_REEXEC_FLAG):
        return
    if not os.environ.get("WAYLAND_DISPLAY"):
        return

    library = os.environ.get(ENV_LIB)
    if not library or not os.path.exists(library):
        return

    preload = os.environ.get("LD_PRELOAD", "")
    if library in preload.split(":"):
        return

    env = dict(os.environ)
    env["LD_PRELOAD"] = f"{library}:{preload}" if preload else library
    env[_REEXEC_FLAG] = "1"

    # Under `python -m context`, argv[0] is __main__.py's path. Re-running that
    # as a plain script drops the package, breaking its relative imports, so the
    # -m form has to be reconstructed.
    main = sys.modules.get("__main__")
    package = getattr(main, "__package__", None)
    if package:
        argv = [sys.executable, "-m", package, *sys.argv[1:]]
    else:
        argv = [sys.executable, *sys.argv]

    try:
        os.execve(sys.executable, argv, env)
    except OSError:
        return

DEFAULT_WIDTH = 380
ENV_EDGE = "CONTEXT_SIDEBAR_EDGE"
ENV_WIDTH = "CONTEXT_SIDEBAR_WIDTH"


def available() -> bool:
    """Whether this session can host a docked sidebar.

    `is_supported()` inspects the open GDK display, so it only answers correctly
    once one exists — before that it warns and returns False regardless. Callers
    run this from window construction, by which point GTK has a display.
    """
    if LayerShell is None:
        return False
    if not os.environ.get("WAYLAND_DISPLAY"):
        return False

    from gi.repository import Gdk

    display = Gdk.Display.get_default()
    if display is None or "Wayland" not in type(display).__name__:
        return False
    return bool(LayerShell.is_supported())


def configured_edge() -> str:
    edge = (os.environ.get(ENV_EDGE) or "left").strip().casefold()
    return edge if edge in EDGES else "left"


def configured_width() -> int:
    raw = os.environ.get(ENV_WIDTH)
    if raw and raw.strip().isdigit():
        return max(200, int(raw.strip()))
    return DEFAULT_WIDTH


def apply(window, edge: str | None = None, width: int | None = None) -> bool:
    """Turn `window` into an anchored sidebar. Must run before it is realized."""
    if not available():
        return False

    edge = edge or configured_edge()
    width = width or configured_width()
    vertical = edge in ("left", "right")

    LayerShell.init_for_window(window)
    LayerShell.set_namespace(window, "context-sidebar")
    LayerShell.set_layer(window, LayerShell.Layer.TOP)

    for name, attr in EDGES.items():
        anchored = name == edge or (
            # Span the screen along the edge it is docked to.
            (vertical and name in ("top", "bottom"))
            or (not vertical and name in ("left", "right"))
        )
        LayerShell.set_anchor(window, getattr(LayerShell.Edge, attr), anchored)

    # Reserve the space so tiled windows do not sit underneath.
    LayerShell.auto_exclusive_zone_enable(window)
    # Let the entry take input without stealing focus from everything else.
    LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.ON_DEMAND)

    if vertical:
        window.set_default_size(width, -1)
        window.set_size_request(width, -1)
    else:
        window.set_default_size(-1, width)
        window.set_size_request(-1, width)

    return True
