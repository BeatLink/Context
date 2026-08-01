"""The editor window.

Deleting is the interesting part. It happened in a dialog once, and the
dialog needed an overlay the editor did not have — the button silently did
nothing. The confirmation is inline now: the same row swaps its buttons, so
there is nothing that can fail to appear.
"""

from __future__ import annotations

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from tests.conftest import needs_display, run_app

pytestmark = needs_display


def _build(app, ctx, seen):
    from context.ui.editor_window import EditorWindow

    return EditorWindow(
        app,
        ctx,
        on_done=lambda *a: None,
        on_cancel=lambda: None,
        on_delete=lambda c: seen["deleted"].append(c),
    )


def test_delete_asks_in_the_header_and_then_deletes(gtk_app, isolated_store):
    from context.state.store import ContextStore

    store = ContextStore()
    ctx = store.create("doomed")
    seen = {"deleted": []}

    def body(app):
        window = _build(app, ctx, seen)
        page = window.page
        seen["before"] = (
            page.delete_button.get_visible(),
            page.confirm_delete.get_visible(),
        )
        page.delete_button.emit("clicked")
        seen["asking"] = (
            page.delete_button.get_visible(),
            page.keep_button.get_visible(),
            page.confirm_delete.get_visible(),
        )
        page.confirm_delete.emit("clicked")
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["before"] == (True, False)
    assert seen["asking"] == (False, True, True)
    assert seen["deleted"] == [ctx]


def test_keeping_puts_the_row_back(gtk_app, isolated_store):
    from context.state.store import ContextStore

    store = ContextStore()
    ctx = store.create("kept")
    seen = {"deleted": []}

    def body(app):
        window = _build(app, ctx, seen)
        page = window.page
        page.delete_button.emit("clicked")
        page.keep_button.emit("clicked")
        seen["after"] = (
            page.delete_button.get_visible(),
            page.keep_button.get_visible(),
            page.confirm_delete.get_visible(),
        )
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["after"] == (True, False, False)
    assert seen["deleted"] == []


def test_a_new_context_offers_no_forget(gtk_app, isolated_store):
    """Backing out of a new context deletes it anyway; a forget button there
    would be a second delete with different wording."""
    from context.ui.editor_window import EditorWindow
    from context.state.store import ContextStore

    store = ContextStore()
    ctx = store.create("fresh")
    seen = {}

    def body(app):
        window = EditorWindow(
            app,
            ctx,
            on_done=lambda *a: None,
            on_cancel=lambda: None,
            on_delete=None,
            is_new=True,
        )
        seen["has_button"] = hasattr(window.page, "delete_button")
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["has_button"] is False


def test_the_preview_edit_hotspot_opens_the_resource_page(gtk_app, isolated_store):
    """The drawn edit control on a layout slot pushes that window's settings.

    Pinned because it shipped broken twice over: the preview hands its
    callback (index, screen) and the handler took only index, so every click
    raised a TypeError that died silently in the gesture handler — the button
    looked dead. And the handler read `entries[index]` directly, where index
    is the slot's position on its screen, not overall.
    """
    from context.ui.editor_window import EditorWindow
    from context.ui.resource_page import ResourcePage
    from context.state.resources import Resource
    from context.state.store import ContextStore

    store = ContextStore()
    ctx = store.create("edit-me")
    ctx.resources = [Resource(app_id="firefox.desktop", urls=["https://x.com"])]
    store.save()
    seen = {}

    def body(app):
        window = EditorWindow(
            app, ctx, on_done=lambda *a: None, on_cancel=lambda: None
        )
        page = window.page
        # What the drawn hotspot calls on a click, screen default included.
        page.previews[0].on_edit(0)
        nav = page.get_parent()
        seen["pushed"] = isinstance(nav.get_visible_page(), ResourcePage)
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["pushed"] is True


def _preset_index(page, label: str) -> int:
    """Where an arrangement sits in what the editor is currently offering."""
    return [name for name, _slots in page.preset_options].index(label)


def test_choosing_an_arrangement_applies_it(gtk_app, isolated_store):
    """The layout choosers are buttons, not dropdowns.

    A `Gtk.DropDown` in the editor — a layer-shell overlay — opens its list but
    never keeps what is clicked in it, so both layout dropdowns appeared to do
    nothing at all.
    """
    from context.ui.editor import EditorPage
    from context.state.resources import Resource
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create(
            "probe", resources=[Resource(app_id="a"), Resource(app_id="b")]
        )
        page = EditorPage(ctx, lambda *a_: None, lambda: None)
        stacked = _preset_index(page, "Top and bottom")
        seen["starts_as"] = page.preset_chooser.get_selected()
        seen["side_by_side"] = _preset_index(page, "Side by side")

        page.preset_chooser._buttons[stacked].set_active(True)
        seen["slots"] = [
            (s.x, s.y, s.width, s.height) for s in page.previews[0].layout.slots
        ]
        seen["chosen"] = page.preset_chooser.get_selected()

        # Clicking the chosen one again must not leave nothing chosen.
        page.preset_chooser._buttons[stacked].set_active(False)
        seen["still_chosen"] = page.preset_chooser.get_selected()
        seen["stacked"] = stacked
        app.quit()

    run_app(gtk_app, body)
    # Two apps start side by side, which is the arrangement the chooser shows.
    assert seen["starts_as"] == seen["side_by_side"]
    assert seen["slots"] == [(0.0, 0.0, 1.0, 0.5), (0.0, 0.5, 1.0, 0.5)]
    assert seen["chosen"] == seen["stacked"]
    assert seen["still_chosen"] == seen["stacked"]


def test_a_hand_dragged_layout_matches_no_arrangement(gtk_app, isolated_store):
    from context.ui.editor import EditorPage
    from context.state.layout import Layout, Slot
    from context.state.resources import Resource
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create(
            "probe", resources=[Resource(app_id="a"), Resource(app_id="b")]
        )
        page = EditorPage(ctx, lambda *a_: None, lambda: None)
        page._on_layout_changed(
            Layout(slots=[Slot(0, 0, 0.3, 1.0), Slot(0.3, 0, 0.7, 1.0)]), 0
        )
        page._update_state()
        seen["chosen"] = page.preset_chooser.get_selected()
        app.quit()

    run_app(gtk_app, body)
    assert seen["chosen"] == -1


def test_only_the_arrangements_that_fit_are_offered(gtk_app, isolated_store):
    """A preset was padded or trimmed to the window count, so "Three columns"
    over two windows meant two thirds of a screen and a gap, and "Grid" meant
    the same thing as side by side."""
    from context.ui.editor import EditorPage
    from context.state.resources import Resource
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create("probe", resources=[Resource(app_id="a")])
        page = EditorPage(ctx, lambda *a_: None, lambda: None)
        seen["one"] = [label for label, _slots in page.preset_options]

        for extra in ("b", "c"):
            page.entries.append(Resource(app_id=extra))
            page.arrangement.assign(len(page.entries) - 1, 0)
        page._update_state()
        seen["three"] = [label for label, _slots in page.preset_options]
        seen["buttons"] = len(page.preset_chooser._buttons)

        for extra in ("d", "e", "f"):
            page.entries.append(Resource(app_id=extra))
            page.arrangement.assign(len(page.entries) - 1, 0)
        page._update_state()
        seen["six"] = [label for label, _slots in page.preset_options]
        seen["six_slots"] = len(page.preset_options[0][1])
        app.quit()

    run_app(gtk_app, body)
    assert seen["one"] == ["Maximised"]
    assert seen["three"] == ["Three columns", "Main and stack"]
    # The row holds exactly what is offered, and nothing that would be trimmed.
    assert seen["buttons"] == 2
    # Past what the named arrangements cover, the generated grid is the offer.
    assert seen["six"] == ["Grid of 6"]
    assert seen["six_slots"] == 6


def test_the_editor_draws_the_same_catalogue_as_the_overview(
    gtk_app, isolated_store, monkeypatch
):
    """It had a search box over a flow of tiles here and search, category and
    ordering there, so which controls you got for finding an application
    depended on which screen you were looking at."""
    from context.ui import catalogue
    from context.system.apps import App
    from context.state.store import Context
    from context.ui.editor import EditorPage
    from context.ui.rows import AddAppRow

    apps = [
        App(id="a.desktop", name="Ardour", description="", icon=None,
            categories=("AudioVideo",)),
        App(id="b.desktop", name="Builder", description="", icon=None,
            categories=("Development",)),
    ]
    monkeypatch.setattr(catalogue, "installed_apps", lambda: apps)
    seen = {}

    def body(app):
        page = EditorPage(Context(title="work"), lambda *_a: None, lambda: None)
        # The same three controls the overview has, not just a search box.
        seen["controls"] = (
            page.catalogue.entry is not None,
            page.catalogue.category_chooser is not None,
            page.catalogue.sort_chooser is not None,
        )
        seen["rows"] = [type(r).__name__ for r in page.catalogue.rows()]

        # Adding is the one answer the editor has, and the row says so.
        row = page.catalogue.row("b.desktop")
        seen["before"] = row.badge.get_label()
        row.emit("activated")
        seen["after"] = page.catalogue.row("b.desktop").badge.get_label()
        seen["entries"] = [r.app_id for r in page.entries]

        # Narrowing by kind reaches the editor's copy too.
        page.catalogue._on_category(page.catalogue.categories.index("Development"))
        seen["development"] = [r.app_info.name for r in page.catalogue.rows()]
        app.quit()

    run_app(gtk_app, body)
    assert seen["controls"] == (True, True, True)
    assert set(seen["rows"]) == {"AddAppRow"}
    assert seen["before"] == ""
    assert seen["after"] == "1 in layout"
    assert seen["entries"] == ["b.desktop"]
    assert seen["development"] == ["Builder"]


def test_adding_keeps_the_scroll_by_refreshing_one_row(
    gtk_app, isolated_store, monkeypatch
):
    """Rebuilding the whole catalogue on every add would throw the list back to
    the top, and adding to a layout is done in runs."""
    from context.ui import catalogue
    from context.system.apps import App
    from context.state.store import Context
    from context.ui.editor import EditorPage

    apps = [App(id="a.desktop", name="Ardour", description="", icon=None)]
    monkeypatch.setattr(catalogue, "installed_apps", lambda: apps)
    seen = {}

    def body(app):
        page = EditorPage(Context(title="work"), lambda *_a: None, lambda: None)
        row = page.catalogue.row("a.desktop")
        row.emit("activated")
        row.emit("activated")
        # The same widget throughout, with its count moved on.
        seen["same"] = page.catalogue.row("a.desktop") is row
        seen["badge"] = row.badge.get_label()
        app.quit()

    run_app(gtk_app, body)
    assert seen["same"] is True
    assert seen["badge"] == "2 in layout"
