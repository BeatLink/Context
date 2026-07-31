"""Shared fixtures.

Two things make Context awkward to test, and both are handled here:

* **It talks to a compositor.** `FakeBackend` implements the same interface with
  no window manager behind it, so the launch and close logic can be exercised
  without a session.
* **It is a GTK application.** `gtk_app` runs one against whatever display is
  available, and the tests that need one are skipped when there is none rather
  than failing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context import logging_setup, settings, theme  # noqa: E402
from dataclasses import replace  # noqa: E402

from context.backends.base import MonitorInfo, WindowInfo, Workspace  # noqa: E402
from context.layout import Slot  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Point the store at a temporary directory.

    Autouse: a test that wrote to the real store would clobber the user's
    contexts, and one that read from it would depend on what they happen to have.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    # Config too, or a test reads whatever settings the user happens to have —
    # and `settings` caches, so one test's change leaks into every later one.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "_current", None)
    monkeypatch.setattr(theme, "_current", None)
    # Modules bind their logger at import, which happens during collection —
    # before this fixture runs. Re-configuring moves the file handler onto the
    # temporary path; without it the suite writes its fixture names into the
    # real log, which is misleading when reading it to debug a live session.
    logging_setup.configure()
    # Never touch a real window manager from a test.
    monkeypatch.setenv("CONTEXT_BACKEND", "none")
    yield tmp_path


class FakeBackend:
    """A window manager that only exists in memory.

    Records what it was asked to do so tests can assert on the sequence, which
    is what actually matters for tiling: preselect, launch, preselect, launch.
    """

    name = "fake"

    def __init__(self, existing: dict[str, int] | None = None) -> None:
        # workspace handle -> window count
        self.workspaces: dict[str, int] = dict(existing or {})
        self.calls: list[tuple] = []
        self.current: str | None = None
        self.fail_switch = False
        # Most recently focused first, the order a switcher lists them in.
        self.open_windows: list[WindowInfo] = []
        self.focused: str | None = None
        # One 16:9 output unless a test says otherwise.
        self.outputs: list[MonitorInfo] = [
            MonitorInfo(name="FAKE-1", width=1920, height=1080, focused=True)
        ]
        # handle -> monitor it was bound to.
        self.placements: dict[str, str] = {}
        self.states: dict[str, str] = {}
        self.grouped: set[str] = set()

    # -- interface ----------------------------------------------------------

    def available(self) -> bool:
        return True

    def ensure_workspace(self, title: str, handle: str | None) -> Workspace:
        name = handle or f"ctx-{title.strip().casefold().replace(' ', '-')}"
        created = name not in self.workspaces
        self.calls.append(("ensure", name, created))
        return Workspace(handle=name, label=title, created=created)

    def switch_to(self, workspace: Workspace) -> bool:
        self.calls.append(("switch", workspace.handle))
        if self.fail_switch:
            return False
        self.current = workspace.handle
        return True

    def current_handle(self) -> str | None:
        return self.current

    def prepare_launch(self, workspace: Workspace) -> None:
        self.calls.append(("prepare", workspace.handle))

    def workspace_exists(self, handle: str) -> bool:
        return handle in self.workspaces

    def window_count(self, handle: str) -> int:
        return self.workspaces.get(handle, 0)

    def live_handles(self) -> set[str]:
        self.calls.append(("live",))
        return {h for h, count in self.workspaces.items() if count > 0}

    def windows(self, handle: str | None = None) -> list[WindowInfo]:
        self.calls.append(("windows", handle))
        return [w for w in self.open_windows if handle is None or w.handle == handle]

    def place_windows(self, handle: str, *app_ids: str) -> None:
        """Put windows on a workspace, as if apps had already been launched.

        Keeps `windows()` and `window_count()` telling the same story — the
        launcher reads both, and a fake where they disagree tests nothing.
        """
        for app_id in app_ids:
            self.open_windows.append(
                WindowInfo(
                    id=f"0x{len(self.open_windows):x}",
                    title=app_id,
                    app_id=app_id,
                    handle=handle,
                )
            )
        self.workspaces[handle] = self.workspaces.get(handle, 0) + len(app_ids)

    def focus_window(self, window_id: str) -> bool:
        self.calls.append(("focus", window_id))
        self.focused = window_id
        return True

    def monitors(self) -> list[MonitorInfo]:
        self.calls.append(("monitors",))
        return list(self.outputs)

    def place_workspace(self, handle: str, monitor: str) -> bool:
        self.calls.append(("place", handle, monitor))
        self.placements[handle] = monitor
        return True

    def move_window(self, window_id: str, handle: str) -> bool:
        self.calls.append(("move_window", window_id, handle))
        moved = []
        for window in self.open_windows:
            if window.id == window_id:
                if window.handle:
                    self.workspaces[window.handle] = max(
                        0, self.workspaces.get(window.handle, 1) - 1
                    )
                self.workspaces[handle] = self.workspaces.get(handle, 0) + 1
                moved.append(replace(window, handle=handle))
            else:
                moved.append(window)
        self.open_windows = moved
        return True

    def set_window_state(self, window_id: str, state: str) -> bool:
        if state not in (
            "fullscreen", "maximise", "restore", "float", "tile", "pin", "center"
        ):
            return False
        self.calls.append(("state", window_id, state))
        self.states[window_id] = state
        return True

    def swap_windows(self, window_id: str, direction: str) -> bool:
        if direction not in ("l", "r", "u", "d"):
            return False
        self.calls.append(("swap", window_id, direction))
        return True

    def group_windows(self, window_id: str, direction: str = "r") -> bool:
        if direction not in ("l", "r", "u", "d"):
            return False
        self.calls.append(("group", window_id, direction))
        self.grouped.add(window_id)
        return True

    def ungroup_window(self, window_id: str) -> bool:
        self.calls.append(("ungroup", window_id))
        self.grouped.discard(window_id)
        return True

    def close_workspace(self, handle: str) -> int:
        count = self.workspaces.get(handle, 0)
        self.calls.append(("close", handle, count))
        self.workspaces[handle] = 0
        return count

    def remove_workspace(self, handle: str) -> bool:
        if self.workspaces.get(handle, 0) == 0:
            self.workspaces.pop(handle, None)
            self.calls.append(("remove", handle))
            return True
        return False

    def preselect(self, direction: str) -> bool:
        self.calls.append(("preselect", direction))
        return True

    def apply_ratios(self, handle: str, slots) -> int:
        self.calls.append(("ratios", handle, len(slots)))
        return max(0, len(slots) - 1)

    # -- helpers for tests --------------------------------------------------

    def add_window(self, handle: str, count: int = 1) -> None:
        self.workspaces[handle] = self.workspaces.get(handle, 0) + count
        for index in range(count):
            self.open_windows.append(
                WindowInfo(
                    id=f"{handle}-{len(self.open_windows)}",
                    title=f"window {index} on {handle}",
                    app_id="fake.desktop",
                    handle=handle,
                )
            )

    def sequence(self, *kinds: str) -> list[tuple]:
        """Only the calls of the given kinds, in order."""
        return [c for c in self.calls if c[0] in kinds]


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def slots():
    """A two-slot side-by-side layout."""
    return [Slot(0.0, 0.0, 0.5, 1.0), Slot(0.5, 0.0, 0.5, 1.0)]


def _has_display() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


needs_display = pytest.mark.skipif(
    not _has_display(), reason="no display; run under xvfb-run"
)


@pytest.fixture
def gtk_app():
    """An Adw.Application with a unique id, so instances never collide.

    GApplication is single-instance per id: two tests sharing one would hand off
    to each other over D-Bus and silently do nothing.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    import uuid

    app = Adw.Application(application_id=f"io.beatlink.CtxTest{uuid.uuid4().hex[:8]}")
    yield app


def run_app(app, body, timeout_ms: int = 4000):
    """Run `body(app)` inside the GTK main loop and return once it is done.

    `body` is called after activation, and should call `app.quit()` when
    finished. A timeout quits anyway, so a hung test fails rather than blocks.
    """
    from gi.repository import GLib

    def on_activate(a):
        GLib.idle_add(lambda: (body(a), False)[1])

    app.connect("activate", on_activate)
    GLib.timeout_add(timeout_ms, lambda: (app.quit(), False)[1])
    return app.run([])
