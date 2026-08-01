"""The rows the sidebar and the overview share, and their menu."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from context.system.apps import App
from context.ui.rows import AppRow, ContextRow, app_tile, context_for_app
from tests.conftest import needs_display, run_app

pytestmark = needs_display


def _row(ctx, **kwargs) -> ContextRow:
    calls = kwargs.setdefault("calls", {})
    row = ContextRow(
        ctx,
        lambda c: calls.setdefault("open", []).append(c),
        lambda c: calls.setdefault("edit", []).append(c),
        lambda c: calls.setdefault("close", []).append(c),
        is_open=kwargs.get("is_open", False),
        is_active=kwargs.get("is_active", False),
        on_forget=lambda c: calls.setdefault("forget", []).append(c),
        on_add_app=lambda c: calls.setdefault("add_app", []).append(c),
    )
    row.calls = calls
    return row


def test_an_ampersand_in_a_title_is_shown_as_one(gtk_app, isolated_store):
    """Titles were escaped whether or not markup was on.

    Only the current context's row parses markup — it is bolded — so every
    other row spelled the entities out: "Review todos &amp; notes".
    """
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create("Review todos & notes")
        seen["plain"] = _row(ctx).get_title()
        seen["active"] = _row(ctx, is_active=True).get_title()
        app.quit()

    run_app(gtk_app, body)
    assert seen["plain"] == "Review todos & notes"
    # The bolded one is markup, so its ampersand has to stay escaped or Pango
    # rejects the whole label.
    assert seen["active"] == "<b>Review todos &amp; notes</b>"


def test_the_menu_offers_what_the_row_can_do(gtk_app, isolated_store):
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create("alpha")
        window = Gtk.ApplicationWindow(application=app)
        box = Gtk.Box()
        window.set_child(box)

        open_row = _row(ctx, is_open=True)
        box.append(open_row)
        open_row.open_menu()
        seen["open_row"] = list(open_row.menu_items)

        saved_row = _row(ctx, is_open=False)
        box.append(saved_row)
        saved_row.open_menu()
        seen["saved_row"] = list(saved_row.menu_items)

        saved_row.menu_items["edit"].emit("clicked")
        seen["edited"] = saved_row.calls.get("edit") == [ctx]
        window.destroy()
        app.quit()

    run_app(gtk_app, body)
    # Closing is only offered for something that is open.
    assert "close" in seen["open_row"]
    assert "close" not in seen["saved_row"]
    assert {"open", "edit", "add-app", "forget"} <= set(seen["saved_row"])
    assert seen["edited"] is True


def test_forgetting_from_the_menu_asks_first(gtk_app, isolated_store):
    """One click either side of "Close" must not lose a context."""
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create("alpha")
        window = Gtk.ApplicationWindow(application=app)
        row = _row(ctx)
        window.set_child(row)
        row.open_menu()

        row.menu_items["forget"].emit("clicked")
        seen["asked"] = row.menu_items["confirm"].get_visible()
        seen["not_yet"] = row.calls.get("forget", [])
        row.menu_items["confirm"].emit("clicked")
        seen["forgotten"] = [c.title for c in row.calls.get("forget", [])]
        window.destroy()
        app.quit()

    run_app(gtk_app, body)
    assert seen["asked"] is True
    assert seen["not_yet"] == []
    assert seen["forgotten"] == ["alpha"]


def test_opening_an_app_here_routes_to_the_hook(gtk_app, isolated_store):
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create("alpha")
        window = Gtk.ApplicationWindow(application=app)
        row = _row(ctx)
        window.set_child(row)
        row.open_menu()
        row.menu_items["add-app"].emit("clicked")
        seen["asked"] = [c.title for c in row.calls.get("add_app", [])]
        window.destroy()
        app.quit()

    run_app(gtk_app, body)
    assert seen["asked"] == ["alpha"]


def test_an_app_row_says_what_it_would_do(gtk_app, isolated_store):
    picked = []

    def body(app):
        info = App(id="kicad.desktop", name="KiCad & friends", description="EDA", icon=None)
        row = AppRow(info, picked.append)
        picked.append(row.get_title())
        row.emit("activated")
        app.quit()

    run_app(gtk_app, body)
    assert picked[0] == "KiCad & friends"
    assert picked[1].id == "kicad.desktop"


def test_a_tile_carries_the_app_it_shows(gtk_app, isolated_store):
    """The picker activates the first tile on Enter, so it has to be readable."""
    seen = {}

    def body(app):
        info = App(id="firefox.desktop", name="Firefox", description="", icon=None)
        tile = app_tile(info, lambda _i: None)
        seen["info"] = tile.app_info
        app.quit()

    run_app(gtk_app, body)
    assert seen["info"].id == "firefox.desktop"


def test_a_context_for_an_app_is_saved_with_a_layout(gtk_app, isolated_store):
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        store = ContextStore()
        info = App(id="kicad.desktop", name="KiCad", description="", icon=None)
        ctx = context_for_app(store, info)
        seen["title"] = ctx.title
        seen["apps"] = [r.app_id for r in ctx.resources]
        seen["slots"] = len(ctx.layout.slots)
        seen["stored"] = [c.title for c in ContextStore().contexts]
        app.quit()

    run_app(gtk_app, body)
    assert seen["title"] == "KiCad"
    assert seen["apps"] == ["kicad.desktop"]
    assert seen["slots"] == 1
    assert seen["stored"] == ["KiCad"]
