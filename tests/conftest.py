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

from context import logging_setup  # noqa: E402
from context.backends.base import Workspace  # noqa: E402
from context.layout import Slot  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Point the store at a temporary directory.

    Autouse: a test that wrote to the real store would clobber the user's
    contexts, and one that read from it would depend on what they happen to have.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
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
