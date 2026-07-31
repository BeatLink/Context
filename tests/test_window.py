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


def test_urls_are_editable_rows_not_a_text_box(gtk_app, isolated_store):
    """Each URL is separately removable, rather than lines in one box."""
    from context.apps import App
    from context.resource_page import ResourcePage
    from context.resources import Resource

    resource = Resource(
        app_id="firefox.desktop", urls=["https://a.com", "https://b.com"]
    )
    app_info = App(id="firefox.desktop", name="Firefox", description="", icon=None)
    seen = {}

    def body(app):
        page = ResourcePage(app_info, resource, lambda r: None)
        seen["rows"] = len(page.url_rows())
        seen["urls"] = page.current_urls()

        page._add_url("c.com")
        seen["after_add"] = page.current_urls()

        page._remove_url(page.url_rows()[0])
        seen["after_remove"] = page.current_urls()
        app.quit()

    run_app(gtk_app, body)
    assert seen["rows"] == 2
    assert seen["urls"] == ["https://a.com", "https://b.com"]
    # A bare host is normalised on the way out.
    assert seen["after_add"][-1] == "https://c.com"
    assert seen["after_remove"] == ["https://b.com", "https://c.com"]


def test_path_apps_get_a_picker_not_a_url_list(gtk_app, isolated_store):
    from context.apps import App
    from context.resource_page import ResourcePage
    from context.resources import Resource

    resource = Resource(app_id="codium.desktop")
    app_info = App(id="codium.desktop", name="VSCodium", description="", icon=None)
    seen = {}

    def body(app):
        page = ResourcePage(app_info, resource, lambda r: None)
        seen["has_path_row"] = page.path_row is not None
        seen["urls_hidden"] = not page.url_section.get_visible()
        page._set_path("/tmp/project")
        seen["subtitle"] = page.path_row.get_subtitle()
        page._set_path(None)
        seen["cleared"] = page.path_row.get_subtitle()
        app.quit()

    run_app(gtk_app, body)
    assert seen["has_path_row"]
    assert seen["urls_hidden"]
    assert seen["subtitle"] == "/tmp/project"
    assert seen["cleared"] == "nothing chosen"


def test_a_launched_context_moves_from_saved_to_open(gtk_app, isolated_store):
    """The open list is what tells you a launch worked.

    A launch that blocked the main loop meant the poll never fired and the
    context stayed under Saved even though its windows were up.
    """
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    ctx = store.create("work")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.refresh()
        seen["before_open"] = len(rows(window.open_listbox))
        seen["before_saved"] = len(rows(window.listbox))

        window._open_ids = {ctx.id}
        window.refresh()
        seen["after_open"] = len(rows(window.open_listbox))
        seen["after_saved"] = len(rows(window.listbox))
        app.quit()

    run_app(gtk_app, body)
    assert (seen["before_open"], seen["before_saved"]) == (0, 1)
    assert (seen["after_open"], seen["after_saved"]) == (1, 0)


def test_refresh_open_state_rereads_the_backend(gtk_app, isolated_store, monkeypatch):
    """A finished launch refreshes the list instead of waiting for the poll."""
    from context.store import ContextStore
    from context import window as window_module

    store = ContextStore()
    ctx = store.create("work")
    seen = {}

    def body(app):
        win = window_module.LauncherWindow(app, store, lambda c: None, lambda c: None)
        monkeypatch.setattr(
            window_module, "open_state", lambda contexts: ({ctx.id}, ctx.id)
        )
        win.refresh_open_state()
        seen["open"] = len(rows(win.open_listbox))
        seen["active"] = win._active_context().title
        app.quit()

    run_app(gtk_app, body)
    assert seen["open"] == 1
    assert seen["active"] == "work"
