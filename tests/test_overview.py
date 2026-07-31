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


def _tiles(flow):
    out = []
    child = flow.get_first_child()
    while child is not None:
        out.append(child)
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
        seen["apps"] = len(_tiles(window.flow))
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
        seen["apps"] = len(_tiles(window.flow))
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
