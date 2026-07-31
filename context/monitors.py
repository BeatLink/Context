"""Which output things happen on.

Context is a single-monitor design in one specific way: a context's workspace
lives on whichever output the compositor puts it on, and the launcher docks to
one of them. Neither is chosen here — the compositor decides — but both need to
be *askable*, because a layout drawn at the wrong aspect ratio lies about where
windows land, and a launcher that appears on the wrong screen is worse than one
that appears on a fixed one.

The setting names a monitor; nothing means "wherever the compositor would put
it", which is the right answer for a single-monitor session and for anyone who
has not thought about it.
"""

from __future__ import annotations

from .backends import MonitorInfo
from .logging_setup import get_logger

log = get_logger("monitors")

# Used when no monitor can be identified at all — a null backend, a compositor
# that will not say, or a headless test. Matches the most common panel rather
# than pretending to know.
FALLBACK_ASPECT = 16 / 9


def all_monitors(backend=None) -> list[MonitorInfo]:
    """Connected outputs, left to right.

    The order is the screen numbering everything else uses, so it has to be
    stable and it has to be positional. Sorting by focus would make "screen 1"
    mean a different monitor depending on where the pointer was, and an app the
    user placed on their right-hand display would open on the left.
    """
    from . import backends

    wm = backend or backends.detect()
    try:
        found = list(wm.monitors())
    except OSError as exc:
        log.warning("could not read monitors: %s", exc)
        return []
    return sorted(found, key=lambda m: (m.x, m.y, m.name))


def ordered(backend=None) -> list[MonitorInfo]:
    """Connected outputs as screen 1, screen 2, and so on.

    This is the only place a screen number becomes a physical monitor.
    Contexts never name a monitor — they say "screen 2" — so moving a cable or
    rearranging the desk is one change here rather than an edit to every
    context.

    The configured order comes first, then anything connected it does not
    mention, left to right. A configured monitor that is not plugged in is
    skipped rather than leaving a gap, so unplugging the middle screen of three
    makes the third become screen 2 rather than the layout losing a screen.
    """
    from . import settings

    found = all_monitors(backend)
    by_name = {m.name: m for m in found}

    wanted = []
    for name in settings.current().screen_order:
        monitor = by_name.pop(name, None)
        if monitor is not None:
            wanted.append(monitor)
    # Whatever the order did not mention keeps its positional place.
    return wanted + [m for m in found if m.name in by_name]


def focused(backend=None) -> MonitorInfo | None:
    """The output with the pointer or keyboard on it, if the backend says."""
    found = all_monitors(backend)
    for monitor in found:
        if monitor.focused:
            return monitor
    return found[0] if found else None


def by_name(name: str | None, backend=None) -> MonitorInfo | None:
    """The named output, or None if it is not connected.

    Not connected is the ordinary case, not an error: a laptop configured for
    its docking station spends most of its time away from it.
    """
    if not name:
        return None
    for monitor in all_monitors(backend):
        if monitor.name == name:
            return monitor
    log.info("monitor %s is not connected", name)
    return None


def preferred(backend=None) -> MonitorInfo | None:
    """The output the launcher should dock to.

    The configured one when it is connected, otherwise the focused one — so
    unplugging the monitor a setting names moves the launcher back to the
    laptop rather than leaving it nowhere.
    """
    from . import settings

    wanted = settings.current().monitor
    return by_name(wanted, backend) or focused(backend)


def preview_aspect(backend=None) -> float:
    """Width over height for the layout preview.

    A layout is fractions of the output it opens on, so the preview has to be
    that output's shape to mean anything.
    """
    monitor = preferred(backend)
    if monitor is None or not monitor.height:
        return FALLBACK_ASPECT
    return monitor.aspect


def names(backend=None) -> list[str]:
    return [m.name for m in all_monitors(backend)]
