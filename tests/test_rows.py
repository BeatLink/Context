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


@needs_display
def test_a_row_only_carries_the_buttons_it_will_show(gtk_app):
    """`.linked` rounds by position among the box's children and `:first-child`
    is structural, not visual — a button that is present but hidden would leave
    its neighbour square on one side. So only the ones that show are added."""
    from context.state.store import ContextStore
    from context.ui.rows import ContextRow

    seen = {}

    def suffixes(row):
        found, child = [], row._suffixes.get_first_child()
        while child is not None:
            found.append(child)
            child = child.get_next_sibling()
        return found

    def body(app):
        store = ContextStore()
        ctx = store.create("work")

        busy = ContextRow(
            ctx,
            lambda c: None,
            lambda c: None,
            lambda c: None,
            is_open=True,
            is_drifted=True,
            on_save=lambda c: None,
        )
        seen["open"] = len(suffixes(busy))
        seen["linked"] = busy._suffixes.has_css_class("linked")
        seen["spacing"] = busy._suffixes.get_spacing()
        # The frame has to come back or there is no join to see.
        seen["flat"] = [b.has_css_class("flat") for b in suffixes(busy)]

        # Saved and not drifted: only the pencil, so it is first and last at
        # once and keeps a rounded corner on both sides.
        seen["saved"] = len(suffixes(ContextRow(ctx, lambda c: None, lambda c: None, None)))
        app.quit()

    run_app(gtk_app, body)
    assert seen["open"] == 3
    assert seen["saved"] == 1
    assert seen["linked"] is True
    assert seen["spacing"] == 0
    assert seen["flat"] == [False, False, False]


@needs_display
def test_an_app_card_has_the_same_menu_the_context_card_does(gtk_app):
    """Reaching for a right-click on an application and getting nothing reads
    as the row being inert, when it has the same two answers as its buttons."""
    from context.system.apps import App
    from context.ui.rows import AppRow

    seen = {"picked": []}

    def body(app):
        info = App(id="firefox.desktop", name="Firefox", description="", icon=None)
        # In a window first: a popover parented to a row with no root crashes
        # GTK outright rather than failing the assertion.
        window = Gtk.ApplicationWindow(application=app)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        window.set_child(box)

        both = AppRow(
            info,
            lambda i: seen["picked"].append("new"),
            lambda i: seen["picked"].append("here"),
        )
        box.append(both)
        both.open_menu()
        seen["with_context"] = sorted(both.menu_items)
        both.menu_items["here"].emit("clicked")

        alone = AppRow(info, lambda i: seen["picked"].append("new"))
        box.append(alone)
        alone.open_menu()
        seen["without_context"] = sorted(alone.menu_items)
        alone.menu_items["new"].emit("clicked")

        window.destroy()
        app.quit()

    run_app(gtk_app, body)
    assert seen["with_context"] == ["here", "new"]
    assert seen["without_context"] == ["new"]
    assert seen["picked"] == ["here", "new"]


@needs_display
def test_a_drifted_context_offers_the_way_back_as_well_as_the_way_on(gtk_app, isolated_store):
    """Only offering "keep this" made drifting a one-way door: the way back was
    to close the context and reopen it."""
    from context.state.store import ContextStore
    from context.ui.rows import ContextRow

    seen = {"restored": []}

    def body(app):
        store = ContextStore()
        ctx = store.create("work")
        window = Gtk.ApplicationWindow(application=app)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        window.set_child(box)

        drifted = ContextRow(
            ctx, lambda c: None, lambda c: None, lambda c: None,
            is_open=True, is_drifted=True,
            on_save=lambda c: None, on_restore=seen["restored"].append,
        )
        box.append(drifted)
        seen["offered"] = drifted.restore.get_visible()
        drifted.restore.emit("clicked")
        drifted.open_menu()
        seen["in_menu"] = "restore" in drifted.menu_items

        # Nothing has changed, so there is nothing to put back.
        settled = ContextRow(
            ctx, lambda c: None, lambda c: None, lambda c: None,
            is_open=True, is_drifted=False,
            on_save=lambda c: None, on_restore=seen["restored"].append,
        )
        box.append(settled)
        seen["settled"] = settled.restore.get_visible()
        window.destroy()
        app.quit()

    run_app(gtk_app, body)
    assert seen["offered"] is True
    assert seen["settled"] is False
    assert seen["in_menu"] is True
    assert [c.title for c in seen["restored"]] == ["work"]
