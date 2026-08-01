"""The editor window.

Forgetting is the interesting part. It happened in a dialog once, and the
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
    from context.editor_window import EditorWindow

    return EditorWindow(
        app,
        ctx,
        on_done=lambda *a: None,
        on_cancel=lambda: None,
        on_delete=lambda c: seen["deleted"].append(c),
    )


def test_forget_asks_in_the_row_and_then_forgets(gtk_app, isolated_store):
    from context.store import ContextStore

    store = ContextStore()
    ctx = store.create("doomed")
    seen = {"deleted": []}

    def body(app):
        window = _build(app, ctx, seen)
        page = window.page
        seen["before"] = (
            page.delete_button.get_visible(),
            page.forget_button.get_visible(),
        )
        page.delete_button.emit("clicked")
        seen["asking"] = (
            page.delete_button.get_visible(),
            page.keep_button.get_visible(),
            page.forget_button.get_visible(),
        )
        page.forget_button.emit("clicked")
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["before"] == (True, False)
    assert seen["asking"] == (False, True, True)
    assert seen["deleted"] == [ctx]


def test_keeping_puts_the_row_back(gtk_app, isolated_store):
    from context.store import ContextStore

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
            page.forget_button.get_visible(),
        )
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["after"] == (True, False, False)
    assert seen["deleted"] == []


def test_a_new_context_offers_no_forget(gtk_app, isolated_store):
    """Backing out of a new context deletes it anyway; a forget button there
    would be a second delete with different wording."""
    from context.editor_window import EditorWindow
    from context.store import ContextStore

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
    from context.editor_window import EditorWindow
    from context.resource_page import ResourcePage
    from context.resources import Resource
    from context.store import ContextStore

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


def _preset_index(name: str) -> int:
    from context.layout import PRESETS

    return list(PRESETS).index(name)


def test_choosing_an_arrangement_applies_it(gtk_app, isolated_store):
    """The layout choosers are buttons, not dropdowns.

    A `Gtk.DropDown` in the editor — a layer-shell overlay — opens its list but
    never keeps what is clicked in it, so both layout dropdowns appeared to do
    nothing at all.
    """
    from context.editor import EditorPage
    from context.resources import Resource
    from context.store import ContextStore

    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create(
            "probe", resources=[Resource(app_id="a"), Resource(app_id="b")]
        )
        page = EditorPage(ctx, lambda *a_: None, lambda: None)
        seen["starts_as"] = page.preset_chooser.get_selected()

        page.preset_chooser._buttons[_preset_index("stacked")].set_active(True)
        seen["slots"] = [
            (s.x, s.y, s.width, s.height) for s in page.previews[0].layout.slots
        ]
        seen["chosen"] = page.preset_chooser.get_selected()

        # Clicking the chosen one again must not leave nothing chosen.
        page.preset_chooser._buttons[_preset_index("stacked")].set_active(False)
        seen["still_chosen"] = page.preset_chooser.get_selected()
        app.quit()

    run_app(gtk_app, body)
    # Two apps start side by side, which is the preset the chooser shows.
    assert seen["starts_as"] == _preset_index("side-by-side")
    assert seen["slots"] == [(0.0, 0.0, 1.0, 0.5), (0.0, 0.5, 1.0, 0.5)]
    assert seen["chosen"] == _preset_index("stacked")
    assert seen["still_chosen"] == _preset_index("stacked")


def test_a_hand_dragged_layout_matches_no_arrangement(gtk_app, isolated_store):
    from context.editor import EditorPage
    from context.layout import Layout, Slot
    from context.resources import Resource
    from context.store import ContextStore

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
