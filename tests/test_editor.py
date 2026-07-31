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
