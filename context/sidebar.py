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

# How far a docked or overlaid surface floats from the screen edges, matching
# the compositor's gaps so Context reads as a window among windows.
GAP = 8

OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}


def _set_margins(window, gap: int, edge: str | None = None) -> None:
    """Float the surface, except on the side the windows adjoin.

    Tiled windows bring their own gap to the boundary of the reserved space,
    so a margin there doubled it: 16px between sidebar and windows against
    8px everywhere else.
    """
    open_side = OPPOSITE.get(edge) if edge else None
    for name, attr in EDGES.items():
        amount = 0 if name == open_side else gap
        LayerShell.set_margin(window, getattr(LayerShell.Edge, attr), amount)


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
    # The hidden sliver hugs the edge with no gap: a 2px hover target floated
    # 8px into the screen cannot be found by sliding the pointer to the edge.
    _set_margins(
        window, 0 if width == HIDDEN_WIDTH else GAP, edge or configured_edge()
    )


def gdk_monitor(name: str | None):
    """The `Gdk.Monitor` the compositor calls `name`, if it is connected.

    Layer-shell takes a GDK monitor, while everything else in Context names an
    output the way the compositor does, so the two have to be matched up. GDK
    exposes the connector name, which is the same string Hyprland reports.
    """
    if not name:
        return None
    from gi.repository import Gdk

    display = Gdk.Display.get_default()
    if display is None:
        return None
    found = display.get_monitors()
    for index in range(found.get_n_items()):
        monitor = found.get_item(index)
        if monitor.get_connector() == name:
            return monitor
    log.info("monitor %s is not connected; leaving the placement to the compositor", name)
    return None


def place(window, name: str | None = None) -> str | None:
    """Pin a layer-shell surface to one output.

    Without this the compositor chooses, which on a multi-monitor session means
    the launcher appears whereever focus happened to be — different on each
    start. Must run before the window is realized.

    Returns the monitor actually used, or None when the choice was left to the
    compositor.
    """
    if LayerShell is None or not available():
        return None
    wanted = name if name is not None else settings.current().monitor
    if wanted == settings.ALL_MONITORS:
        # "Every screen" is a set of windows, each named individually; a window
        # that was not told which is left to the compositor.
        return None
    monitor = gdk_monitor(wanted)
    if monitor is None:
        return None
    LayerShell.set_monitor(window, monitor)
    return wanted


def apply(
    window,
    edge: str | None = None,
    width: int | None = None,
    monitor: str | None = None,
) -> bool:
    """Turn `window` into an anchored sidebar. Must run before it is realized."""
    if not available():
        return False

    edge = edge or configured_edge()
    width = width or configured_width()
    vertical = edge in ("left", "right")

    LayerShell.init_for_window(window)
    place(window, monitor)
    LayerShell.set_namespace(window, "context-sidebar")
    LayerShell.set_layer(window, LayerShell.Layer.TOP)

    for name, attr in EDGES.items():
        anchored = name == edge or (
            # Span the screen along the edge it is docked to.
            (vertical and name in ("top", "bottom"))
            or (not vertical and name in ("left", "right"))
        )
        LayerShell.set_anchor(window, getattr(LayerShell.Edge, attr), anchored)

    # Reserve the space so tiled windows do not sit underneath. The margins
    # are inside the reservation — auto exclusive zones account for them.
    _set_margins(window, GAP, edge)
    LayerShell.auto_exclusive_zone_enable(window)
    # ON_DEMAND: the compositor focuses and unfocuses this the way it does an
    # ordinary window — clicking in gives it the keyboard, clicking away takes
    # it back. It does not take focus on map, so a context's windows keep it.
    #
    # NONE was used here instead, with the keyboard raised while the pointer
    # was inside. That made anything with a popover unusable: opening one sends
    # the sidebar a pointer-leave, the keyboard was dropped, and the popover
    # dismissed itself a frame later.
    LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.ON_DEMAND)

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
    # The same output the launcher is on: an editor that opens on the other
    # screen from the sidebar that summoned it is disorienting.
    place(window)
    LayerShell.set_namespace(window, "context-editor")
    LayerShell.set_layer(window, LayerShell.Layer.OVERLAY)
    for attr in EDGES.values():
        LayerShell.set_anchor(window, getattr(LayerShell.Edge, attr), True)
    _set_margins(window, GAP)
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


def release_focus(window) -> None:
    """Give the keyboard back to whatever the user turns to next.

    Dropping to NONE and back to ON_DEMAND, because there is no "unfocus me"
    request in the protocol: the mode *is* the request, so the only way to say
    "not now" is to stop being focusable for a moment. Staying on NONE would
    leave the sidebar unclickable for the rest of the session.

    Keyboard interactivity is double-buffered, so the restore has to wait for
    the NONE to be committed: both modes set inside one commit collapse to no
    change at all, and the compositor never sees a release. The restore rides
    the frame that carries the NONE out; a window with no frames coming gets
    it back immediately, which for an unmapped surface changes nothing.
    """
    if LayerShell is None or not available():
        return
    LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.NONE)
    clock = window.get_frame_clock()
    if clock is None or not window.get_mapped():
        LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.ON_DEMAND)
        return
    if getattr(window, "_keyboard_restore", None) is not None:
        window.queue_draw()
        return

    def restore(painted) -> None:
        handler = getattr(window, "_keyboard_restore", None)
        if handler is None:
            return
        window._keyboard_restore = None
        painted.disconnect(handler)
        LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.ON_DEMAND)
        # The mode change needs a commit of its own to take effect.
        window.queue_draw()

    window._keyboard_restore = clock.connect("after-paint", restore)
    window.queue_draw()
