"""Reacting to a monitor being plugged in or unplugged.

The launchers were built once at startup and never revisited, so unplugging a
screen left a launcher docked to an output that no longer existed — it stacked
onto the remaining monitor as a second bar — and plugging one in gave the new
screen nothing.

`ContextApplication.rebuild_launchers` is exercised here through a stand-in
rather than a real `Adw.Application`, because building one needs a display and
these are the parts with no GTK in them: which screens are wanted, whether that
differs from what is up, and what gets torn down.
"""

from __future__ import annotations

import pytest

from context.state import settings
from context.system import monitors
from context.app import ContextApplication
from context.system.backends.base import MonitorInfo


class FakeWindow:
    def __init__(self, monitor: str | None) -> None:
        self.monitor = monitor
        self.destroyed = False
        self.presented = 0

    def destroy(self) -> None:
        self.destroyed = True

    def present(self) -> None:
        self.presented += 1


class FakeApp:
    """Enough of the application for the rebuild to run without a display."""

    rebuild_launchers = ContextApplication.rebuild_launchers
    launchers = ContextApplication.launchers

    def __init__(self, backend) -> None:
        self.backend = backend
        self.window: FakeWindow | None = None
        self.extra_windows: list[FakeWindow] = []
        self.built = 0

        class Log:
            def info(self, *a): ...
            def warning(self, *a): ...

        self.log = Log()

    def _build_launchers(self) -> None:
        self.built += 1
        docks = monitors.docks_on(self.backend)
        for index, monitor in enumerate(docks):
            window = FakeWindow(getattr(monitor, "name", None))
            if index == 0:
                self.window = window
            else:
                self.extra_windows.append(window)


@pytest.fixture
def app(backend):
    return FakeApp(backend)


def two_screens(backend):
    backend.outputs = [
        MonitorInfo(name="eDP-1", width=1920, height=1200, focused=True),
        MonitorInfo(name="HDMI-A-1", width=3440, height=1440, x=1920),
    ]


def test_unplugging_removes_the_launcher_that_had_no_screen(app, backend):
    """The reported bug: two bars on one monitor after a screen went away."""
    settings.update(monitor=settings.ALL_MONITORS)
    two_screens(backend)
    app.rebuild_launchers()
    assert [w.monitor for w in app.launchers] == ["eDP-1", "HDMI-A-1"]

    gone = list(app.launchers)
    backend.outputs = [backend.outputs[0]]
    app.rebuild_launchers()

    assert [w.monitor for w in app.launchers] == ["eDP-1"]
    # The old windows are actually taken down, not just dropped from the list —
    # a surface left mapped is exactly what the second bar was.
    assert all(w.destroyed for w in gone)


def test_plugging_in_gives_the_new_screen_a_launcher(app, backend):
    settings.update(monitor=settings.ALL_MONITORS)
    backend.outputs = [MonitorInfo(name="eDP-1", width=1920, height=1200, focused=True)]
    app.rebuild_launchers()
    assert [w.monitor for w in app.launchers] == ["eDP-1"]

    two_screens(backend)
    app.rebuild_launchers()
    assert [w.monitor for w in app.launchers] == ["eDP-1", "HDMI-A-1"]


def test_a_change_that_moves_nothing_rebuilds_nothing(app, backend):
    """Hot-plug arrives as a burst, and unrelated changes (resolution, scale)
    come through the same signal. Tearing the launchers down for those would
    make the bar flicker for no reason."""
    settings.update(monitor=settings.ALL_MONITORS)
    two_screens(backend)
    app.rebuild_launchers()
    before = app.built
    existing = list(app.launchers)

    app.rebuild_launchers()

    assert app.built == before
    assert not any(w.destroyed for w in existing)


def test_docking_to_one_screen_ignores_the_others(app, backend):
    """A launcher pinned to a named monitor stays one launcher."""
    two_screens(backend)
    settings.update(monitor="HDMI-A-1")
    app.rebuild_launchers()
    assert [w.monitor for w in app.launchers] == ["HDMI-A-1"]


def test_unplugging_the_named_monitor_falls_back(app, backend):
    """Losing the monitor a setting names moves the launcher to what is left,
    rather than leaving it on an output that is gone."""
    two_screens(backend)
    settings.update(monitor="HDMI-A-1")
    app.rebuild_launchers()

    backend.outputs = [MonitorInfo(name="eDP-1", width=1920, height=1200, focused=True)]
    app.rebuild_launchers()
    assert [w.monitor for w in app.launchers] == ["eDP-1"]
