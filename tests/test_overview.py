"""The overview: contexts one side, apps the other, one search over both."""

from __future__ import annotations

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from context.apps import App
from tests.conftest import needs_display, run_app

pytestmark = needs_display


def _rows(listbox):
    out = []
    row = listbox.get_first_child()
    while row is not None:
        if hasattr(row, "ctx"):
            out.append(row)
        row = row.get_next_sibling()
    return out


def _tiles(window):
    """Every app tile in the grid, in the order the sections show them."""
    out = []
    for flow in window.flows:
        child = flow.get_first_child()
        while child is not None:
            out.append(child)
            child = child.get_next_sibling()
    return out


def _headings(window) -> list[str]:
    out = []
    child = window.sections.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            out.append(child.get_label())
        child = child.get_next_sibling()
    return out


@pytest.fixture
def fake_apps(monkeypatch):
    from context import overview

    apps = [
        App(id="firefox.desktop", name="Firefox", description="Browser", icon=None),
        App(id="kicad.desktop", name="KiCad", description="EDA", icon=None),
    ]
    monkeypatch.setattr(overview, "installed_apps", lambda: apps)
    return apps


def _build(app, store, backend):
    from context.overview import OverviewWindow

    window = OverviewWindow(app, store, backend=backend)
    return window


def test_contexts_split_into_open_and_saved(gtk_app, isolated_store, backend, fake_apps):
    from context.store import ContextStore

    store = ContextStore()
    running = store.create("running")
    running.set_handle("fake", "ctx-running")
    store.create("parked")
    backend.workspaces["ctx-running"] = 1
    seen = {}

    def body(app):
        window = _build(app, store, backend)
        seen["open"] = [r.ctx.title for r in _rows(window.open_list)]
        seen["saved"] = [r.ctx.title for r in _rows(window.saved_list)]
        seen["apps"] = len(_tiles(window))
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["open"] == ["running"]
    assert seen["saved"] == ["parked"]
    assert seen["apps"] == 2


def test_one_search_filters_both_sides(gtk_app, isolated_store, backend, fake_apps):
    from context.store import ContextStore

    store = ContextStore()
    store.create("surf reddit")
    store.create("pay bills")
    seen = {}

    def body(app):
        window = _build(app, store, backend)
        window.entry.set_text("fire")
        window.refresh()  # SearchEntry debounces search-changed; refresh directly
        seen["saved"] = [r.ctx.title for r in _rows(window.saved_list)]
        seen["apps"] = len(_tiles(window))
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["saved"] == []
    assert seen["apps"] == 1  # Firefox


def test_a_context_row_routes_to_the_launcher(gtk_app, isolated_store, backend, fake_apps):
    from context.store import ContextStore

    store = ContextStore()
    ctx = store.create("alpha")
    seen = {"opened": []}

    def body(app):
        window = _build(app, store, backend)
        window.on_context = lambda c: seen["opened"].append(c)
        _rows(window.saved_list)[0].emit("activated")
        app.quit()

    run_app(gtk_app, body)
    assert seen["opened"] == [ctx]


def test_an_app_becomes_a_new_context_and_opens(gtk_app, isolated_store, backend, fake_apps):
    """The point of the grid: one click from an installed app to working in it."""
    from context.store import ContextStore

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
    fresh = __import__("context.store", fromlist=["ContextStore"]).ContextStore()
    assert "KiCad" in [c.title for c in fresh.contexts]


def test_a_row_offers_the_same_handles_as_the_sidebar(
    gtk_app, isolated_store, backend, fake_apps
):
    """Editing and closing were sidebar-only, so the overview could show a
    context but not do anything with it beyond opening."""
    from context.store import ContextStore

    store = ContextStore()
    ctx = store.create("running")
    ctx.set_handle("fake", "ctx-running")
    backend.workspaces["ctx-running"] = 1
    seen = {"edited": [], "closed": []}

    def body(app):
        window = _build(app, store, backend)
        window.on_edit = lambda c, is_new=False: seen["edited"].append((c, is_new))
        window.on_close = seen["closed"].append
        dismissed = []
        window.close = lambda: dismissed.append(True)
        row = _rows(window.open_list)[0]
        row.close.emit("clicked")
        seen["still_up"] = dismissed == []
        row.edit.emit("clicked")
        seen["left_for_the_editor"] = dismissed == [True]
        app.quit()

    run_app(gtk_app, body)
    assert seen["closed"] == [ctx]
    # Closing is housekeeping — you carry on choosing, so the overview stays.
    assert seen["still_up"] is True
    assert seen["left_for_the_editor"] is True
    # Editing hands over to the launcher, which owns the editor overlay.
    assert seen["edited"] == [(ctx, False)]


def test_enter_starts_a_context_by_the_name_typed(gtk_app, isolated_store, fake_apps, backend):
    """The sidebar starts one; the overview used to do nothing at all."""
    from context.store import ContextStore

    store = ContextStore()
    seen = {"edited": []}

    def body(app):
        window = _build(app, store, backend)
        window.on_edit = lambda c, is_new=False: seen["edited"].append((c.title, is_new))
        window.entry.set_text("plan the week")
        window.refresh()
        window._activate_first()
        app.quit()

    run_app(gtk_app, body)
    assert seen["edited"] == [("plan the week", True)]


def test_the_built_in_row_says_what_it_will_do(gtk_app, isolated_store, fake_apps, backend):
    from context.store import ContextStore

    store = ContextStore()
    store.create("alpha")
    seen = {}

    def body(app):
        window = _build(app, store, backend)
        seen["blank"] = window.create_row.get_subtitle()
        window.entry.set_text("alpha")
        window.refresh()
        seen["existing"] = window.create_row.get_subtitle()
        window.entry.set_text("something new")
        window.refresh()
        seen["new"] = window.create_row.get_subtitle()
        app.quit()

    run_app(gtk_app, body)
    assert seen["blank"] == "Name it in the editor"
    assert seen["existing"] == "Open “alpha”"
    assert seen["new"] == "Start “something new”"


def test_opening_an_app_into_a_context_hands_over(
    gtk_app, isolated_store, backend, fake_apps
):
    from context.store import ContextStore

    store = ContextStore()
    ctx = store.create("alpha")
    seen = {"asked": []}

    def body(app):
        window = _build(app, store, backend)
        window.on_add_app = seen["asked"].append
        _rows(window.saved_list)[0].open_menu()
        _rows(window.saved_list)[0].menu_items["add-app"].emit("clicked")
        app.quit()

    run_app(gtk_app, body)
    assert seen["asked"] == [ctx]


def test_categories_narrow_the_grid(gtk_app, isolated_store, backend, monkeypatch):
    from context import overview
    from context.apps import App
    from context.store import ContextStore

    apps = [
        App(id="a.desktop", name="Ardour", description="", icon=None,
            categories=("AudioVideo",)),
        App(id="b.desktop", name="Builder", description="", icon=None,
            categories=("Development",)),
        App(id="c.desktop", name="Cargo", description="", icon=None,
            categories=("Development", "Utility")),
    ]
    monkeypatch.setattr(overview, "installed_apps", lambda: apps)
    seen = {}

    def body(app):
        window = _build(app, ContextStore(), backend)
        seen["offered"] = [
            b.get_label() for b in window.category_chooser._buttons
        ]
        window._on_category(window.categories.index("Development"))
        seen["development"] = [t.app_info.name for t in _tiles(window)]
        seen["heading"] = window.apps_label.get_label()
        # A search still applies inside the category.
        window.entry.set_text("car")
        window.refresh()
        seen["searched"] = [t.app_info.name for t in _tiles(window)]
        window._on_category(0)
        window.entry.set_text("")
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


def test_the_no_context_is_listed_with_the_open_ones(
    gtk_app, isolated_store, backend, fake_apps
):
    """Windows outside every context are a context until they are given one."""
    from context.launcher import NO_CONTEXT_ID
    from context.store import ContextStore

    store = ContextStore()
    backend.geometry["stray"] = [
        {"id": "0x1", "app_id": "firefox.desktop", "x": 0, "y": 0,
         "width": 1920, "height": 1080},
    ]
    seen = {"saved": [], "closed": []}

    def body(app):
        window = _build(app, store, backend)
        window.on_save = seen["saved"].append
        window.on_close = seen["closed"].append
        rows = _rows(window.open_list)
        seen["titles"] = [r.ctx.title for r in rows]
        row = rows[0]
        seen["can_edit"] = row.edit.get_visible()
        seen["offers_save"] = row.save.get_visible()
        window.close = lambda: None
        row.save.emit("clicked")
        row.close.emit("clicked")
        app.quit()

    run_app(gtk_app, body)
    assert seen["titles"] == ["No context"]
    # Nothing to edit until it has been saved as a context of its own.
    assert seen["can_edit"] is False
    assert seen["offers_save"] is True
    assert [c.id for c in seen["saved"]] == [NO_CONTEXT_ID]
    assert [c.id for c in seen["closed"]] == [NO_CONTEXT_ID]


def test_the_grid_can_be_reordered(gtk_app, isolated_store, backend, monkeypatch):
    """Three questions, three orders: the one I was just using, the one whose
    name I know, and the ones I actually work in."""
    from context import overview
    from context.apps import App
    from context.resources import Resource
    from context.store import ContextStore

    from context import uistate

    apps = [
        App(id="a.desktop", name="Ardour", description="", icon=None,
            categories=("AudioVideo",)),
        App(id="z.desktop", name="Zed", description="", icon=None,
            categories=("Development",)),
        App(id="m.desktop", name="Meld", description="", icon=None,
            categories=("Development",)),
        App(id="7.desktop", name="7-Zip", description="", icon=None),
    ]
    monkeypatch.setattr(overview, "installed_apps", lambda: apps)
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

        window._on_sort(window.sort_keys.index("name"))
        seen["by_name"] = [t.app_info.name for t in _tiles(window)]
        seen["letters"] = _headings(window)

        window._on_sort(window.sort_keys.index("contexts"))
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
    """The grid answers two questions, and which one is a toggle rather than a
    different way in."""
    from context.store import ContextStore

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
        seen["offers"] = window.target_chooser._buttons[1].get_label()

        window._on_target(1)
        window._open_app(fake_apps[0])
        # And back: a new context is still one click away.
        window._on_target(0)
        window._open_app(fake_apps[1])
        app.quit()

    run_app(gtk_app, body)
    assert seen["offers"] == "Current context"
    assert seen["into"] == [("work", "firefox.desktop")]
    assert [c.title for c in seen["opened"]] == ["KiCad"]


def test_there_is_nothing_to_add_to_without_a_context(
    gtk_app, isolated_store, backend, fake_apps
):
    from context.store import ContextStore

    seen = {}

    def body(app):
        window = _build(app, ContextStore(), backend)
        seen["offered"] = window.target_chooser._buttons[1].get_sensitive()
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["offered"] is False


def test_by_kind_groups_under_the_categories(gtk_app, isolated_store, backend, monkeypatch):
    from context import overview
    from context.apps import App
    from context.store import ContextStore

    apps = [
        App(id="a.desktop", name="Ardour", description="", icon=None,
            categories=("AudioVideo",)),
        App(id="b.desktop", name="Builder", description="", icon=None,
            categories=("Development", "Utility")),
        App(id="c.desktop", name="Chores", description="", icon=None),
    ]
    monkeypatch.setattr(overview, "installed_apps", lambda: apps)
    seen = {}

    def body(app):
        window = _build(app, ContextStore(), backend)
        window._on_sort(window.sort_keys.index("kind"))
        seen["headings"] = _headings(window)
        seen["order"] = [t.app_info.name for t in _tiles(window)]
        window.close()
        app.quit()

    run_app(gtk_app, body)
    # Filed under the first category it claims, never under both.
    assert seen["headings"] == ["Media", "Development", "Everything else"]
    assert seen["order"] == ["Ardour", "Builder", "Chores"]
