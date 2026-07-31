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

from . import settings
from .logging_setup import get_logger

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

log = get_logger("sidebar")


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
    elif sys.argv and sys.argv[0] not in ("-c", "-"):
        argv = [sys.executable, *sys.argv]
    else:
        # Imported from `python -c` or a REPL: there is nothing to re-exec, and
        # trying would restart python with no program.
        log.debug("no re-executable entry point; skipping the preload")
        return

    try:
        os.execve(sys.executable, argv, env)
    except OSError as exc:
        # Without the preload the sidebar silently becomes an ordinary window,
        # which looks like the layer-shell support having vanished.
        log.error("could not re-exec with the layer-shell preload: %s", exc)
        return

# Defaults live in `settings`; these names remain for the environment overrides.
ENV_EDGE = "CONTEXT_SIDEBAR_EDGE"
ENV_WIDTH = "CONTEXT_SIDEBAR_WIDTH"
ENV_RAIL_WIDTH = "CONTEXT_RAIL_WIDTH"


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
    raw = os.environ.get(ENV_EDGE)
    if raw:
        edge = raw.strip().casefold()
        return edge if edge in EDGES else "left"
    return settings.current().sidebar_edge


def configured_width() -> int:
    raw = os.environ.get(ENV_WIDTH)
    if raw and raw.strip().isdigit():
        return max(settings.MIN_SIDEBAR_WIDTH, int(raw.strip()))
    return settings.current().sidebar_width


# Hidden is a sliver rather than nothing. A window with no surface receives no
# pointer events, so hover-to-reveal would be impossible and the only way back
# would be a keybind — a one-way door for anyone who collapses it by accident.
HIDDEN_WIDTH = 2


def rail_width() -> int:
    raw = os.environ.get(ENV_RAIL_WIDTH)
    if raw and raw.strip().isdigit():
        return max(settings.MIN_RAIL_WIDTH, int(raw.strip()))
    return settings.current().rail_width


def resize(window, width: int, edge: str | None = None) -> None:
    """Change how much space a docked window takes.

    The exclusive zone is on `auto`, so it follows the window's size and tiled
    windows reflow to match without asking the compositor for anything.

    Both the size request and the default size have to move. The request alone
    is only a minimum, so the surface stayed at its original width and the
    collapse reserved just as much space as before.
    """
    if not available():
        return
    if (edge or configured_edge()) in ("left", "right"):
        window.set_size_request(width, -1)
        window.set_default_size(width, -1)
    else:
        window.set_size_request(-1, width)
        window.set_default_size(-1, width)


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
    # NONE, not ON_DEMAND: a docked panel that takes keyboard focus pulls it away
    # from the windows a context just opened. Focus is requested only while the
    # user is actually typing in the launcher, via `grab_keyboard`.
    LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.NONE)

    if vertical:
        window.set_default_size(width, -1)
        window.set_size_request(width, -1)
    else:
        window.set_default_size(-1, width)
        window.set_size_request(-1, width)

    return True


def apply_overlay(window) -> bool:
    """Turn `window` into a fullscreen overlay, the way rofi appears.

    Anchored to all four edges on the overlay layer, so it covers the output
    including the bars, and takes keyboard focus for as long as it is up. Unlike a
    fullscreen window it is never tiled into the workspace or placed by a layout.
    """
    if not available():
        return False

    LayerShell.init_for_window(window)
    LayerShell.set_namespace(window, "context-editor")
    LayerShell.set_layer(window, LayerShell.Layer.OVERLAY)
    for attr in EDGES.values():
        LayerShell.set_anchor(window, getattr(LayerShell.Edge, attr), True)
    # No exclusive zone: an overlay covers the bars rather than reserving space.
    LayerShell.set_exclusive_zone(window, -1)
    LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.EXCLUSIVE)
    window._context_overlay = True
    return True


def suspend_overlay(window) -> bool:
    """Get an overlay out of the way of an ordinary window.

    A layer-shell overlay is composited above every ordinary toplevel and holds
    the keyboard exclusively. Anything that opens a real window — a portal file
    chooser, say — is therefore drawn underneath it and cannot be typed into.
    Hiding the overlay for the duration is the only way the two coexist.

    Returns whether anything was suspended, so callers know to restore.
    """
    if not getattr(window, "_context_overlay", False):
        return False
    LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.NONE)
    window.set_visible(False)
    return True


def resume_overlay(window) -> bool:
    if not getattr(window, "_context_overlay", False):
        return False
    window.set_visible(True)
    LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.EXCLUSIVE)
    return True


def grab_keyboard(window, wanted: bool) -> None:
    """Take or release keyboard focus for a docked window.

    With KeyboardMode.NONE the panel never steals focus, which is what makes
    launching a context leave the new window focused. Typing in the launcher does
    need keys though, so the mode is raised for as long as that lasts.
    """
    if LayerShell is None or not available():
        return
    mode = LayerShell.KeyboardMode.ON_DEMAND if wanted else LayerShell.KeyboardMode.NONE
    LayerShell.set_keyboard_mode(window, mode)
