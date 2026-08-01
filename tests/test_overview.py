"""The overview: every installed application, searchable.

Contexts moved to the sidebar, which stands open beside home — the two listed
the same rows on the same screen. Their tests went with them, to
`test_window.py`; what is left here is the grid.
"""

from __future__ import annotations

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from context.system.apps import App
from tests.conftest import needs_display, run_app

pytestmark = needs_display


def _tiles(window):
    """Every app row, in the order the sections show them."""
    return window.catalogue.rows()


def _headings(window) -> list[str]:
    """The group names, without their counts — these tests are about which
    groups appear and in what order. `test_headings_count...` covers the rest.
    """
    out = []
    child = window.catalogue.sections.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            out.append(child.get_label().rsplit(" · ", 1)[0])
        child = child.get_next_sibling()
    return out


@pytest.fixture
def fake_apps(monkeypatch):
    # The catalogue reads them, not the overview: both views draw the same one.
    from context.ui import catalogue

    apps = [
        App(id="firefox.desktop", name="Firefox", description="Browser", icon=None),
        App(id="kicad.desktop", name="KiCad", description="EDA", icon=None),
    ]
    monkeypatch.setattr(catalogue, "installed_apps", lambda: apps)
    return apps


def _build(app, store, backend):
    from context.ui.overview import OverviewWindow

    window = OverviewWindow(app, store, backend=backend)
    return window


def test_an_app_becomes_a_new_context_and_opens(gtk_app, isolated_store, backend, fake_apps):
    """The point of the grid: one click from an installed app to working in it."""
    from context.state.store import ContextStore

    store = ContextStore()
    seen = {"opened": []}

    def body(app):
        window = _build(app, store, backend)
        window.on_context = lambda c: seen["opened"].append(c)
        window._open_app(fake_apps[1])  # KiCad
        app.quit()

    run_app(gtk_app, body)
    assert len(seen["opened"]) == 1
    created = seen["opened"][0]
    assert created.title == "KiCad"
    assert [r.app_id for r in created.resources] == ["kicad.desktop"]
    # Persisted, so it is a real context and not a one-off launch.
    fresh = __import__("context.state.store", fromlist=["ContextStore"]).ContextStore()
    assert "KiCad" in [c.title for c in fresh.contexts]


def test_categories_narrow_the_grid(gtk_app, isolated_store, backend, monkeypatch):
    from context.ui import catalogue
    from context.system.apps import App
    from context.state.store import ContextStore

    apps = [
        App(id="a.desktop", name="Ardour", description="", icon=None,
            categories=("AudioVideo",)),
        App(id="b.desktop", name="Builder", description="", icon=None,
            categories=("Development",)),
        App(id="c.desktop", name="Cargo", description="", icon=None,
            categories=("Development", "Utility")),
    ]
    monkeypatch.setattr(catalogue, "installed_apps", lambda: apps)
    seen = {}

    def body(app):
        window = _build(app, ContextStore(), backend)
        seen["offered"] = [
            b.get_label() for b in window.catalogue.category_chooser._buttons
        ]
        window.catalogue._on_category(window.catalogue.categories.index("Development"))
        seen["development"] = [t.app_info.name for t in _tiles(window)]
        seen["heading"] = window.catalogue.label.get_label()
        # A search still applies inside the category.
        window.catalogue.entry.set_text("car")
        window.refresh()
        seen["searched"] = [t.app_info.name for t in _tiles(window)]
        window.catalogue._on_category(0)
        window.catalogue.entry.set_text("")
        window.refresh()
        seen["all"] = len(_tiles(window))
        window.close()
        app.quit()

    run_app(gtk_app, body)
    # Only the categories something is actually filed under, "All" first.
    assert seen["offered"] == ["All", "Media", "Development", "Utilities"]
    assert seen["development"] == ["Builder", "Cargo"]
    assert seen["heading"].startswith("Development · 2")
    assert seen["searched"] == ["Cargo"]
    assert seen["all"] == 3


def test_the_grid_can_be_reordered(gtk_app, isolated_store, backend, monkeypatch):
    """Three questions, three orders: the one I was just using, the one whose
    name I know, and the ones I actually work in."""
    from context.ui import catalogue
    from context.system.apps import App
    from context.state.resources import Resource
    from context.state.store import ContextStore

    from context.state import uistate

    apps = [
        App(id="a.desktop", name="Ardour", description="", icon=None,
            categories=("AudioVideo",)),
        App(id="z.desktop", name="Zed", description="", icon=None,
            categories=("Development",)),
        App(id="m.desktop", name="Meld", description="", icon=None,
            categories=("Development",)),
        App(id="7.desktop", name="7-Zip", description="", icon=None),
    ]
    monkeypatch.setattr(catalogue, "installed_apps", lambda: apps)
    store = ContextStore()
    store.create("one", resources=[Resource(app_id="z.desktop")])
    store.create("two", resources=[Resource(app_id="z.desktop")])
    store.create("three", resources=[Resource(app_id="m.desktop")])
    import time

    now = time.time()
    uistate.note_app("m.desktop", when=now - 3 * 86400)  # three days ago
    uistate.note_app("a.desktop", when=now - 90 * 60)  # an hour and a half
    seen = {}

    def body(app):
        window = _build(app, store, backend)
        seen["default"] = [t.app_info.name for t in _tiles(window)]
        seen["default_headings"] = _headings(window)

        window.catalogue._on_sort(window.catalogue.sort_keys.index("name"))
        seen["by_name"] = [t.app_info.name for t in _tiles(window)]
        seen["letters"] = _headings(window)

        window.catalogue._on_sort(window.catalogue.sort_keys.index("contexts"))
        seen["by_use"] = [t.app_info.name for t in _tiles(window)]
        seen["split"] = _headings(window)
        window.close()
        app.quit()

    run_app(gtk_app, body)
    # Recent is the default: last launched first, then everything never opened.
    assert seen["default"] == ["Ardour", "Meld", "7-Zip", "Zed"]
    # Grouped by how long ago, in the words a person would use.
    assert seen["default_headings"] == ["1 hour ago", "3 days ago", "Not opened yet"]
    # A-Z is lettered, with anything not starting with a letter last.
    assert seen["by_name"] == ["Ardour", "Meld", "Zed", "7-Zip"]
    assert seen["letters"] == ["A", "M", "Z", "#"]
    # In contexts: those you work in, most contexts first, then the rest.
    assert seen["by_use"] == ["Zed", "Meld", "7-Zip", "Ardour"]
    assert seen["split"] == ["In contexts", "Not in a context"]


def test_an_app_can_go_into_the_context_you_are_in(
    gtk_app, isolated_store, backend, fake_apps
):
    """The overview lists the sidebar's app card, so an application offers the
    same two answers in both views rather than a mode above a grid in one."""
    from context.state.store import ContextStore

    store = ContextStore()
    ctx = store.create("work")
    ctx.set_handle("fake", "ctx-work")
    backend.workspaces["ctx-work"] = 1
    backend.current = "ctx-work"
    seen = {"into": [], "opened": []}

    def body(app):
        window = _build(app, store, backend)
        window.on_app_into = lambda c, i: seen["into"].append((c.title, i.id))
        window.on_context = seen["opened"].append
        window.close = lambda: None

        row = window._app_row(fake_apps[0])
        seen["offers_both"] = row.here.get_visible()
        row.here.emit("clicked")

        # And a new context is still one click away, on the other button.
        window._app_row(fake_apps[1]).fresh.emit("clicked")
        app.quit()

    run_app(gtk_app, body)
    assert seen["offers_both"] is True
    assert seen["into"] == [("work", "firefox.desktop")]
    assert [c.title for c in seen["opened"]] == ["KiCad"]


def test_there_is_nothing_to_add_to_without_a_context(
    gtk_app, isolated_store, backend, fake_apps
):
    """With nothing open there is nothing to add to, so that button is not
    offered rather than meaning what its neighbour means."""
    from context.state.store import ContextStore

    seen = {"opened": []}

    def body(app):
        window = _build(app, ContextStore(), backend)
        window.on_context = seen["opened"].append
        window.close = lambda: None

        row = window._app_row(fake_apps[0])
        seen["offers_here"] = row.here.get_visible()
        # Activating it still works; it just has one meaning.
        row.emit("activated")
        app.quit()

    run_app(gtk_app, body)
    assert seen["offers_here"] is False
    assert [c.title for c in seen["opened"]] == ["Firefox"]


def test_by_kind_groups_under_the_categories(gtk_app, isolated_store, backend, monkeypatch):
    from context.ui import catalogue
    from context.system.apps import App
    from context.state.store import ContextStore

    apps = [
        App(id="a.desktop", name="Ardour", description="", icon=None,
            categories=("AudioVideo",)),
        App(id="b.desktop", name="Builder", description="", icon=None,
            categories=("Development", "Utility")),
        App(id="c.desktop", name="Chores", description="", icon=None),
    ]
    monkeypatch.setattr(catalogue, "installed_apps", lambda: apps)
    seen = {}

    def body(app):
        window = _build(app, ContextStore(), backend)
        window.catalogue._on_sort(window.catalogue.sort_keys.index("kind"))
        seen["headings"] = _headings(window)
        seen["order"] = [t.app_info.name for t in _tiles(window)]
        window.close()
        app.quit()

    run_app(gtk_app, body)
    # Filed under the first category it claims, never under both.
    assert seen["headings"] == ["Media", "Development", "Everything else"]
    assert seen["order"] == ["Ardour", "Builder", "Chores"]


@needs_display
def test_the_overview_opens_with_the_keyboard_in_the_search_box(gtk_app, isolated_store):
    """Typing is what it is for, so it should not need a click first."""
    from context.state.store import ContextStore
    from context.ui.overview import OverviewWindow

    seen = {}

    def body(app):
        from gi.repository import GLib

        store = ContextStore()
        store.create("Work on Context")
        window = OverviewWindow(app, store)
        window.present()

        # Focus lands when the window maps, which is not synchronous with
        # present() — reading it straight back tests nothing.
        context = GLib.MainContext.default()
        deadline = GLib.get_monotonic_time() + 2_000_000
        while not window.get_mapped() and GLib.get_monotonic_time() < deadline:
            context.iteration(False)
        while (
            window.get_focus() is None and GLib.get_monotonic_time() < deadline
        ):
            context.iteration(False)

        focus = window.get_focus()
        # A GtkSearchEntry is composite: grab_focus() lands on the GtkText
        # inside it, so `entry.has_focus()` is False even when it has it.
        # Asserting on the entry itself passes only when nothing works.
        seen["in_entry"] = focus is not None and (
            focus is window.catalogue.entry or focus.is_ancestor(window.catalogue.entry)
        )
        seen["focus_type"] = type(focus).__name__ if focus is not None else None
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["in_entry"] is True, f"focus was on {seen['focus_type']}"


@needs_display
def test_the_overview_refuses_to_be_closed(gtk_app, isolated_store, backend, fake_apps):
    """Home is the one screen always there to come back to, so the
    compositor's own close cannot take it away. `restart` is the exception —
    an execv leaves the surface behind otherwise."""
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        window = _build(app, ContextStore(), backend)
        window.present()
        window.close()
        seen["survived"] = window.get_visible()

        window.permanent = False
        window.close()
        seen["released"] = window.get_visible()
        app.quit()

    run_app(gtk_app, body)
    assert seen["survived"] is True
    assert seen["released"] is False


@needs_display
def test_escape_clears_the_search_before_leaving(
    gtk_app, isolated_store, backend, fake_apps
):
    """Closing is no longer a thing that can happen, so Escape does the two
    things left that mean "not this": undo the filtering, then go back."""
    from context.state.store import ContextStore

    seen = {"left": 0}

    def body(app):
        window = _build(app, ContextStore(), backend)
        window.on_leave = lambda: seen.update(left=seen["left"] + 1)
        window.catalogue.entry.set_text("something")
        window._escape()
        seen["text"] = window.catalogue.entry.get_text()
        seen["left_while_typing"] = seen["left"]
        window._escape()
        seen["left_when_empty"] = seen["left"]
        app.quit()

    run_app(gtk_app, body)
    assert seen["text"] == ""
    assert seen["left_while_typing"] == 0
    assert seen["left_when_empty"] == 1


@needs_display
def test_an_app_names_the_context_it_came_from(
    gtk_app, isolated_store, backend, fake_apps
):
    """Standing on home nothing is active, which is exactly when "open this
    app here" is asked — so it means the context you came from, by name."""
    from context.state import uistate
    from context.state.store import ContextStore

    store = ContextStore()
    ctx = store.create("Work on Context")
    ctx.set_handle("fake", "ctx-work")
    backend.place_windows("ctx-work", "firefox.desktop")
    # Standing on home, having come from that context.
    backend.current = backend.home_handle()
    uistate.note_visit(ctx.id)
    seen = {"into": []}

    def body(app):
        window = _build(app, store, backend)
        window.on_app_into = lambda c, info: seen["into"].append((c.title, info.id))
        window.refresh()
        row = _tiles(window)[0]
        seen["offered"] = row.here.get_visible()
        seen["named"] = row.here.get_tooltip_text()
        row.here.emit("clicked")
        app.quit()

    run_app(gtk_app, body)
    assert seen["offered"] is True
    assert "Work on Context" in seen["named"]
    assert seen["into"] and seen["into"][0][0] == "Work on Context"


@needs_display
def test_the_card_paints_the_padding_it_holds(gtk_app, isolated_store, backend, fake_apps):
    """`.ctx-surface` is what paints — the window itself is transparent — and a
    margin falls outside the box it is set on. Carrying the class and the inset
    on one box left an unpainted band inside the window's own rounded edge.

    So: the window's child is the card, it has no margins of its own, and the
    inset lives on a box inside it.
    """
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        window = _build(app, ContextStore(), backend)
        card = window.get_child()
        seen["paints"] = card.has_css_class("ctx-surface")
        # Opaque, whatever the surface alpha says: a haze over a whole output
        # is not the same thing as a translucent strip at the edge.
        seen["solid"] = card.has_css_class("ctx-solid")
        seen["card_margins"] = [
            card.get_margin_top(), card.get_margin_bottom(),
            card.get_margin_start(), card.get_margin_end(),
        ]
        inner = card.get_first_child()
        seen["inner_margins"] = [
            inner.get_margin_top(), inner.get_margin_bottom(),
            inner.get_margin_start(), inner.get_margin_end(),
        ]
        app.quit()

    run_app(gtk_app, body)
    assert seen["paints"] is True
    assert seen["solid"] is True
    # The card reaches the window's edge; the padding is inside what it paints.
    assert seen["card_margins"] == [0, 0, 0, 0]
    assert seen["inner_margins"] == [18, 18, 18, 18]


@needs_display
def test_the_overview_carries_no_titlebar(gtk_app, isolated_store, backend, fake_apps):
    """Home is a fixture: nothing to close, nowhere else for it to go. The
    compositor's half is a window rule; this is the toolkit's."""
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        window = _build(app, ContextStore(), backend)
        seen["decorated"] = window.get_decorated()
        seen["titlebar"] = window.get_titlebar()
        app.quit()

    run_app(gtk_app, body)
    assert seen["decorated"] is False
    assert seen["titlebar"] is None


@needs_display
def test_the_search_filters_the_grid_alone(gtk_app, isolated_store, backend, fake_apps):
    """One search over contexts and apps was the point when both were here.
    Contexts are the sidebar's now, which has a search of its own."""
    from context.state.store import ContextStore

    store = ContextStore()
    store.create("Firefox stuff")
    seen = {}

    def body(app):
        window = _build(app, store, backend)
        seen["all"] = len(_tiles(window))
        window.catalogue.entry.set_text("fire")
        window.refresh()  # SearchEntry debounces search-changed
        seen["matched"] = [r.app_info.name for r in _tiles(window)]
        app.quit()

    run_app(gtk_app, body)
    assert seen["all"] == 2
    # The context named "Firefox stuff" is not the grid's business.
    assert seen["matched"] == ["Firefox"]


@needs_display
def test_enter_opens_the_first_application_matched(
    gtk_app, isolated_store, backend, fake_apps
):
    """The answer that always works, the same one clicking a row takes."""
    from context.state.store import ContextStore

    store = ContextStore()
    seen = {"opened": []}

    def body(app):
        window = _build(app, store, backend)
        window.on_context = lambda c: seen["opened"].append(c.title)
        window.catalogue.entry.set_text("kic")
        window.refresh()
        window.catalogue.entry.emit("activate")
        app.quit()

    run_app(gtk_app, body)
    assert seen["opened"] == ["KiCad"]


@needs_display
def test_the_overview_lists_no_contexts(gtk_app, isolated_store, backend, fake_apps):
    """They were here and in the sidebar at once — the same contexts, the same
    handles, twice on one screen. The sidebar is the one that is there from
    every workspace rather than only from this one."""
    from context.state.store import ContextStore

    store = ContextStore()
    store.create("alpha")
    seen = {}

    def body(app):
        window = _build(app, store, backend)
        seen["has_lists"] = any(
            hasattr(window, name)
            for name in ("open_list", "saved_list", "create_row")
        )
        # Every row on the screen is an application.
        seen["rows"] = [type(r).__name__ for r in _tiles(window)]
        app.quit()

    run_app(gtk_app, body)
    assert seen["has_lists"] is False
    assert set(seen["rows"]) == {"AppRow"}


@needs_display
def test_an_app_row_carries_no_buttons(gtk_app, isolated_store, backend, fake_apps):
    """A full screen of rows with two small icons each is a lot of furniture
    for a question most rows are never asked. Clicking opens a context of its
    own — the answer that always works — and the menu says both in words."""
    from context.state import uistate
    from context.state.store import ContextStore

    store = ContextStore()
    ctx = store.create("Work on Context")
    ctx.set_handle("fake", "ctx-work")
    backend.place_windows("ctx-work", "firefox.desktop")
    backend.current = backend.home_handle()
    uistate.note_visit(ctx.id)
    seen = {"into": [], "new": []}

    def body(app):
        window = _build(app, store, backend)
        window.on_app_into = lambda c, i: seen["into"].append(c.title)
        window.on_context = lambda c: seen["new"].append(c.title)
        row = _tiles(window)[0]
        # The pair is built either way, so asking whether it shows gets the
        # truth — an unparented widget reports itself visible.
        seen["parented"] = row.here.get_parent() is not None

        # Both answers survive, in the menu.
        row.open_menu()
        seen["menu"] = list(row.menu_items)
        row.menu_items["here"].emit("clicked")
        app.quit()

    run_app(gtk_app, body)
    assert seen["parented"] is False
    assert seen["menu"] == ["new", "here"]
    assert seen["into"] == ["Work on Context"]


@needs_display
def test_enter_reaches_the_search_box(gtk_app, isolated_store, backend, fake_apps):
    """The catalogue owns the entry now, and what Enter means in it belongs to
    the screen around it — which is a connection that can simply be missing."""
    from context.state.store import ContextStore

    store = ContextStore()
    seen = {"opened": []}

    def body(app):
        window = _build(app, store, backend)
        window.on_context = lambda c: seen["opened"].append(c.title)
        window.catalogue.entry.set_text("kic")
        window.refresh()
        window.catalogue.entry.emit("activate")
        app.quit()

    run_app(gtk_app, body)
    assert seen["opened"] == ["KiCad"]


@needs_display
def test_headings_count_what_is_under_them(gtk_app, isolated_store, backend, monkeypatch):
    """Grouped by kind or by letter, how many are in each group is what the
    grouping is for — the total at the top cannot say it."""
    from context.ui import catalogue
    from context.system.apps import App
    from context.state.store import ContextStore

    apps = [
        App(id="a.desktop", name="Ardour", description="", icon=None,
            categories=("AudioVideo",)),
        App(id="b.desktop", name="Bard", description="", icon=None,
            categories=("AudioVideo",)),
        App(id="c.desktop", name="Cargo", description="", icon=None,
            categories=("Development",)),
    ]
    monkeypatch.setattr(catalogue, "installed_apps", lambda: apps)
    seen = {}

    def body(app):
        window = _build(app, ContextStore(), backend)
        window.catalogue._on_sort(window.catalogue.sort_keys.index("kind"))
        seen["raw"] = [
            child.get_label()
            for child in _labels(window.catalogue.sections)
        ]
        app.quit()

    run_app(gtk_app, body)
    assert "Media · 2" in seen["raw"]
    assert "Development · 1" in seen["raw"]


def _labels(box):
    out = []
    child = box.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            out.append(child)
        child = child.get_next_sibling()
    return out
