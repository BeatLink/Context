"""The app grid, asked which application to open in an existing context."""

from __future__ import annotations

import pytest

from context.system.apps import App
from tests.conftest import needs_display, run_app

pytestmark = needs_display


@pytest.fixture
def fake_apps(monkeypatch):
    # The catalogue reads them: the picker draws the same one the overview and
    # the editor do.
    from context.ui import catalogue

    apps = [
        App(id="firefox.desktop", name="Firefox", description="Browser", icon=None),
        App(id="kicad.desktop", name="KiCad", description="EDA", icon=None),
    ]
    monkeypatch.setattr(catalogue, "installed_apps", lambda: apps)
    return apps


def _rows(window) -> list:
    return window.catalogue.rows()


def test_the_picker_draws_the_shared_catalogue(gtk_app, isolated_store, fake_apps):
    """It was a flow of tiles with a search box and nothing else — no filter by
    kind, no ordering — so a half-remembered name meant scrolling."""
    from context.ui.app_picker import AppGridWindow

    seen = {}

    def body(app):
        window = AppGridWindow(app, "Open in “alpha”", lambda _i: None)
        seen["all"] = len(_rows(window))
        seen["controls"] = (
            window.catalogue.category_chooser is not None,
            window.catalogue.sort_chooser is not None,
        )
        # One answer, since the context is already chosen.
        seen["kind"] = {type(r).__name__ for r in _rows(window)}
        window.catalogue.entry.set_text("kic")
        window.refresh()
        seen["filtered"] = [r.app_info.name for r in _rows(window)]
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["all"] == 2
    assert seen["controls"] == (True, True)
    assert seen["kind"] == {"AddAppRow"}
    assert seen["filtered"] == ["KiCad"]


def test_enter_picks_the_first_match_and_leaves(gtk_app, isolated_store, fake_apps):
    from context.ui.app_picker import AppGridWindow

    picked = []

    def body(app):
        window = AppGridWindow(app, "Open in “alpha”", picked.append)
        closed = []
        window.close = lambda: closed.append(True)
        window.catalogue.entry.set_text("fire")
        window.refresh()
        window._pick_first()
        picked.append(closed)
        app.quit()

    run_app(gtk_app, body)
    assert picked[0].id == "firefox.desktop"
    # The picker is an overlay holding the keyboard; it has to go before what
    # it picked is launched.
    assert picked[1] == [True]


def test_an_app_joins_the_context_and_launches_it(gtk_app, isolated_store, backend):
    """"Open here" means the app belongs here, not just this once."""
    import logging

    from context.app import ContextApplication
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        holder = ContextApplication.__new__(ContextApplication)
        holder.log = logging.getLogger("test.add-app")
        holder.backend = backend
        holder.store = ContextStore()
        ctx = holder.store.create("alpha")
        launched = []
        holder.launch_context = launched.append
        holder.add_app_to_context(
            ctx, App(id="kicad.desktop", name="KiCad", description="", icon=None)
        )
        seen["resources"] = [r.app_id for r in ctx.resources]
        seen["launched"] = [c.title for c in launched]
        seen["stored"] = [
            [r.app_id for r in c.resources]
            for c in ContextStore().contexts
            if c.title == "alpha"
        ]
        app.quit()

    run_app(gtk_app, body)
    assert seen["resources"] == ["kicad.desktop"]
    assert seen["launched"] == ["alpha"]
    assert seen["stored"] == [["kicad.desktop"]]
