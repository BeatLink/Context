"""The launcher window.

These need a display. Run the suite under `xvfb-run` to include them; without
one they are skipped rather than failing.
"""

from __future__ import annotations

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from tests.conftest import needs_display, run_app

pytestmark = needs_display


def rows(listbox):
    out = []
    child = listbox.get_first_child()
    while child is not None:
        if hasattr(child, "ctx"):
            out.append(child)
        child = child.get_next_sibling()
    return out


@pytest.fixture
def window_factory(gtk_app, isolated_store):
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()

    def build(app, open_titles=(), active_title=None):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window._is_open = lambda ctx: ctx.title in open_titles
        window._active_context = lambda: next(
            (c for c in store.contexts if c.title == active_title), None
        )
        window.refresh()
        return window

    return store, build


def test_saved_expanded_when_nothing_is_open(gtk_app, window_factory):
    store, build = window_factory
    store.create("alpha")
    store.create("beta")
    seen = {}

    def body(app):
        window = build(app)
        seen["expanded"] = window.saved_expander.get_expanded()
        seen["saved"] = len(rows(window.listbox))
        app.quit()

    run_app(gtk_app, body)
    assert seen["expanded"] is True
    assert seen["saved"] == 2


def test_saved_collapses_once_something_is_open(gtk_app, window_factory):
    store, build = window_factory
    store.create("alpha")
    store.create("beta")
    seen = {}

    def body(app):
        window = build(app, open_titles=("alpha",))
        seen["expanded"] = window.saved_expander.get_expanded()
        seen["still_visible"] = window.saved_expander.get_visible()
        seen["open"] = [r.ctx.title for r in rows(window.open_listbox)]
        seen["saved"] = [r.ctx.title for r in rows(window.listbox)]
        app.quit()

    run_app(gtk_app, body)
    assert seen["expanded"] is False
    # Collapsed, but reachable — not hidden behind a search.
    assert seen["still_visible"] is True
    assert seen["open"] == ["alpha"]
    assert seen["saved"] == ["beta"]


def test_expanding_by_hand_survives_a_refresh(gtk_app, window_factory):
    store, build = window_factory
    store.create("alpha")
    store.create("beta")
    seen = {}

    def body(app):
        window = build(app, open_titles=("alpha",))
        window.saved_expander.set_expanded(True)
        window.refresh()
        seen["still_expanded"] = window.saved_expander.get_expanded()
        app.quit()

    run_app(gtk_app, body)
    assert seen["still_expanded"] is True


def test_open_contexts_carry_a_close_button(gtk_app, window_factory):
    """Closing applies to something running; forgetting happens in the editor."""
    store, build = window_factory
    store.create("alpha")
    store.create("beta")
    seen = {}

    def body(app):
        window = build(app, open_titles=("alpha",))
        seen["open_can_close"] = all(
            r.close.get_visible() for r in rows(window.open_listbox)
        )
        seen["saved_cannot"] = all(
            not r.close.get_visible() for r in rows(window.listbox)
        )
        # Gtk.Widget has its own remove(), so check for the button we would
        # have added rather than the attribute name.
        seen["no_delete"] = not any(
            isinstance(getattr(r, "remove", None), Gtk.Button)
            for r in rows(window.open_listbox) + rows(window.listbox)
        )
        app.quit()

    run_app(gtk_app, body)
    assert seen["open_can_close"]
    assert seen["saved_cannot"]
    assert seen["no_delete"]


def test_the_active_context_is_marked(gtk_app, window_factory):
    store, build = window_factory
    store.create("alpha")
    seen = {}

    def body(app):
        window = build(app, open_titles=("alpha",), active_title="alpha")
        seen["active"] = [r.ctx.title for r in rows(window.open_listbox) if r.is_active]
        app.quit()

    run_app(gtk_app, body)
    assert seen["active"] == ["alpha"]


def test_exact_title_opens_rather_than_duplicating(gtk_app, window_factory):
    store, build = window_factory
    store.create("alpha")
    opened = []

    def body(app):
        from context.window import LauncherWindow

        window = LauncherWindow(app, store, lambda c: opened.append(c.title), None)
        window.entry.set_text("ALPHA")
        window._on_entry_activate(window.entry)
        app.quit()

    run_app(gtk_app, body)
    assert opened == ["alpha"]
    assert len(store.contexts) == 1
