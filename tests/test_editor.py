"""The editor window.

The forget confirmation is the interesting part: it draws into an overlay
inside the editor window, and when no overlay exists it silently answered
itself — the button appeared to do nothing.
"""

from __future__ import annotations

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from context import widgets

from tests.conftest import needs_display, run_app

pytestmark = needs_display


def _find_dialog(widget):
    queue = [widget]
    while queue:
        node = queue.pop(0)
        if isinstance(node, widgets.AlertDialog):
            return node
        child = node.get_first_child()
        while child is not None:
            queue.append(child)
            child = child.get_next_sibling()
    return None


def test_forget_asks_and_then_forgets(gtk_app, isolated_store):
    """Clicking Forget shows the confirmation, and confirming deletes.

    Pinned because it shipped broken: the editor window had no overlay for the
    dialog to draw into, so `present` fell back to the default response —
    cancel — and the button did nothing, visibly or otherwise.
    """
    from context.editor_window import EditorWindow
    from context.store import ContextStore

    store = ContextStore()
    ctx = store.create("doomed")
    seen = {"deleted": [], "cancelled": []}

    def body(app):
        window = EditorWindow(
            app,
            ctx,
            on_done=lambda *a: None,
            on_cancel=lambda: seen["cancelled"].append(True),
            on_delete=lambda c: seen["deleted"].append(c),
        )
        window.page._confirm_delete()
        dialog = _find_dialog(window)
        seen["appeared"] = dialog is not None
        if dialog is not None:
            dialog._respond("delete")
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["appeared"] is True
    assert seen["deleted"] == [ctx]


def test_dismissing_the_confirmation_keeps_the_context(gtk_app, isolated_store):
    from context.editor_window import EditorWindow
    from context.store import ContextStore

    store = ContextStore()
    ctx = store.create("kept")
    seen = {"deleted": []}

    def body(app):
        window = EditorWindow(
            app,
            ctx,
            on_done=lambda *a: None,
            on_cancel=lambda: None,
            on_delete=lambda c: seen["deleted"].append(c),
        )
        window.page._confirm_delete()
        dialog = _find_dialog(window)
        assert dialog is not None
        dialog._respond("cancel")
        seen["gone"] = _find_dialog(window) is None
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["deleted"] == []
    assert seen["gone"] is True
