"""Switching between contexts and between windows.

The pickers are overlays over the whole screen, so they need a display.
"""

from __future__ import annotations

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk

from context.state import uistate
from context.ui import switcher
from context.system.backends.base import WindowInfo
from tests.conftest import needs_display, run_app

pytestmark = needs_display


def _rows(window):
    out = []
    row = window.listbox.get_first_child()
    while row is not None:
        if hasattr(row, "target"):
            out.append(row)
        row = row.get_next_sibling()
    return out


@pytest.fixture
def store_with_contexts(isolated_store):
    from context.state.store import ContextStore

    store = ContextStore()
    store.create("alpha")
    store.create("beta")
    return store


def test_the_context_switcher_lists_every_context(gtk_app, store_with_contexts):
    seen = {}

    def body(app):
        window = switcher.SwitcherWindow(app, store_with_contexts, switcher.CONTEXTS)
        seen["titles"] = sorted(r.get_title() for r in _rows(window))
        app.quit()

    run_app(gtk_app, body)
    assert seen["titles"] == ["alpha", "beta"]


def test_the_context_switcher_filters(gtk_app, store_with_contexts):
    seen = {}

    def body(app):
        window = switcher.SwitcherWindow(app, store_with_contexts, switcher.CONTEXTS)
        window.entry.set_text("alph")
        window.refresh()
        seen["titles"] = [r.get_title() for r in _rows(window)]
        app.quit()

    run_app(gtk_app, body)
    assert seen["titles"] == ["alpha"]


def test_open_contexts_are_listed_before_saved(gtk_app, store_with_contexts, monkeypatch):
    """Switching is mostly to something already running."""
    beta = next(c for c in store_with_contexts.contexts if c.title == "beta")
    monkeypatch.setattr(
        switcher, "open_state", lambda contexts, backend=None: ({beta.id}, None)
    )
    seen = {}

    def body(app):
        window = switcher.SwitcherWindow(app, store_with_contexts, switcher.CONTEXTS)
        seen["order"] = [r.get_title() for r in _rows(window)]
        app.quit()

    run_app(gtk_app, body)
    assert seen["order"][0] == "beta"


def test_choosing_a_context_hands_it_back(gtk_app, store_with_contexts):
    chosen = []

    def body(app):
        window = switcher.SwitcherWindow(app, store_with_contexts, switcher.CONTEXTS)
        window.on_context = chosen.append
        _rows(window)[0].emit("activated")
        app.quit()

    run_app(gtk_app, body)
    assert len(chosen) == 1


def test_the_window_switcher_lists_windows(gtk_app, store_with_contexts, backend):
    seen = {}
    backend.open_windows = [
        WindowInfo(id="0x1", title="Editor", app_id="codium", handle="ctx-alpha"),
        WindowInfo(id="0x2", title="Browser", app_id="firefox", handle="ctx-beta"),
    ]

    def body(app):
        window = switcher.SwitcherWindow(
            app, store_with_contexts, switcher.WINDOWS, scope_all=True
        )
        window.backend = backend
        window.refresh()
        seen["titles"] = [r.get_title() for r in _rows(window)]
        app.quit()

    run_app(gtk_app, body)
    assert seen["titles"] == ["Editor", "Browser"]


def test_the_window_switcher_scopes_to_the_current_context(
    gtk_app, store_with_contexts, backend
):
    """The default is the context you are in, not every window on the desktop."""
    backend.open_windows = [
        WindowInfo(id="0x1", title="Editor", app_id="codium", handle="ctx-alpha"),
        WindowInfo(id="0x2", title="Browser", app_id="firefox", handle="ctx-beta"),
    ]
    backend.current = "ctx-alpha"
    seen = {}

    def body(app):
        window = switcher.SwitcherWindow(app, store_with_contexts, switcher.WINDOWS)
        window.backend = backend
        window.refresh()
        seen["scoped"] = [r.get_title() for r in _rows(window)]
        window.toggle_scope()
        seen["all"] = [r.get_title() for r in _rows(window)]
        app.quit()

    run_app(gtk_app, body)
    assert seen["scoped"] == ["Editor"]
    assert seen["all"] == ["Editor", "Browser"]


def test_a_window_is_labelled_with_its_context(gtk_app, store_with_contexts, backend):
    alpha = next(c for c in store_with_contexts.contexts if c.title == "alpha")
    alpha.set_handle("fake", "ctx-alpha")
    backend.open_windows = [
        WindowInfo(id="0x1", title="Editor", app_id="codium", handle="ctx-alpha")
    ]
    seen = {}

    def body(app):
        window = switcher.SwitcherWindow(
            app, store_with_contexts, switcher.WINDOWS, scope_all=True
        )
        window.backend = backend
        window.refresh()
        seen["subtitle"] = _rows(window)[0].get_subtitle()
        app.quit()

    run_app(gtk_app, body)
    assert seen["subtitle"] == "alpha"


def test_choosing_a_window_focuses_it(gtk_app, store_with_contexts, backend):
    backend.open_windows = [
        WindowInfo(id="0xabc", title="Editor", app_id="codium", handle="ctx-alpha")
    ]

    def body(app):
        window = switcher.SwitcherWindow(
            app, store_with_contexts, switcher.WINDOWS, scope_all=True
        )
        window.backend = backend
        window.refresh()
        _rows(window)[0].emit("activated")
        app.quit()

    run_app(gtk_app, body)
    assert backend.focused == "0xabc"


def test_an_empty_list_says_so(gtk_app, isolated_store, backend):
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        window = switcher.SwitcherWindow(
            app, ContextStore(), switcher.WINDOWS, scope_all=True
        )
        window.backend = backend
        window.refresh()
        seen["empty_shown"] = window.empty.get_visible()
        seen["list_hidden"] = not window.listbox.get_visible()
        app.quit()

    run_app(gtk_app, body)
    assert seen["empty_shown"]
    assert seen["list_hidden"]


def test_opening_a_picker_twice_does_not_stack_overlays(gtk_app, store_with_contexts):
    """Each picker takes the keyboard exclusively, so a pile of them traps you.

    The same bind again means put it away; a different one replaces it.
    """
    from context.app import ContextApplication

    seen = {}

    def body(app):
        holder = ContextApplication.__new__(ContextApplication)
        holder.store = store_with_contexts
        holder.switcher = None
        holder.go_to_context = lambda ctx: None
        # The real windows need a registered GApplication, which a bare holder
        # is not; the guard being tested is about bookkeeping, not the widget.
        built = []

        def fake_window(_app, _store, mode, scope_all):
            window = type(
                "FakePicker",
                (),
                {
                    "mode": mode,
                    "scope_all": scope_all,
                    "on_context": None,
                    "close": lambda self: closed.append(self),
                    "connect": lambda self, *a: None,
                    "present": lambda self: None,
                },
            )()
            built.append(window)
            return window

        closed = []
        original = switcher.SwitcherWindow
        switcher.SwitcherWindow = fake_window
        try:
            ContextApplication._open_switcher(holder, switcher.CONTEXTS)
            seen["opened"] = holder.switcher is not None

            # Same picker again: closes it rather than opening a second.
            ContextApplication._open_switcher(holder, switcher.CONTEXTS)
            seen["toggled_shut"] = holder.switcher is None
            seen["closed_once"] = len(closed) == 1

            # A different picker replaces rather than stacks.
            ContextApplication._open_switcher(holder, switcher.CONTEXTS)
            second = holder.switcher
            ContextApplication._open_switcher(holder, switcher.WINDOWS)
            seen["replaced"] = (
                holder.switcher is not None and holder.switcher is not second
            )
            seen["built"] = len(built)
        finally:
            switcher.SwitcherWindow = original
        app.quit()

    run_app(gtk_app, body)
    assert seen["opened"] is True
    assert seen["toggled_shut"] is True
    assert seen["closed_once"] is True
    assert seen["replaced"] is True
    # Four calls, three windows: the second call only closed the first.
    assert seen["built"] == 3
