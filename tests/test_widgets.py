"""The shared widgets, where behaviour is theirs rather than a view's."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk

from context.ui import widgets
from tests.conftest import needs_display, run_app

pytestmark = needs_display


def _clear_icon(bar) -> str | None:
    name = bar.get_icon_name(Gtk.EntryIconPosition.SECONDARY)
    return name


@needs_display
def test_the_clear_button_appears_only_with_something_to_clear(gtk_app):
    """A button that does nothing is worse than an absent one, and it sits in
    the text's way."""
    seen = {}

    def body(app):
        bar = widgets.SearchBar("Search")
        seen["empty"] = _clear_icon(bar)
        bar.set_text("firefox")
        seen["typed"] = _clear_icon(bar)
        seen["sensitive"] = bar.get_icon_sensitive(Gtk.EntryIconPosition.SECONDARY)
        app.quit()

    run_app(gtk_app, body)
    assert seen["empty"] is None
    assert seen["typed"] == "edit-clear-symbolic"
    assert seen["sensitive"] is True


@needs_display
def test_clearing_empties_the_box(gtk_app):
    seen = {}

    def body(app):
        bar = widgets.SearchBar("Search")
        bar.set_text("firefox")
        bar.clear()
        seen["text"] = bar.get_text()
        seen["icon"] = _clear_icon(bar)
        app.quit()

    run_app(gtk_app, body)
    assert seen["text"] == ""
    assert seen["icon"] is None


@needs_display
def test_a_search_bar_carries_the_search_icon(gtk_app):
    seen = {}

    def body(app):
        bar = widgets.SearchBar("Search")
        seen["icon"] = bar.get_icon_name(Gtk.EntryIconPosition.PRIMARY)
        app.quit()

    run_app(gtk_app, body)
    assert seen["icon"] == "system-search-symbolic"


@needs_display
def test_it_re_emits_what_a_search_entry_would(gtk_app):
    """Every call site was written against `Gtk.SearchEntry`, so both of its
    signals have to arrive or adopting this would have silently broken them."""
    seen = {"changed": 0, "stopped": 0}

    def body(app):
        bar = widgets.SearchBar("Search")
        bar.connect("search-changed", lambda _b: seen.__setitem__("changed", seen["changed"] + 1))
        bar.connect("stop-search", lambda _b: seen.__setitem__("stopped", seen["stopped"] + 1))
        bar.set_text("a")
        bar.emit("stop-search")
        seen["text_after_change"] = bar.get_text()
        app.quit()

    run_app(gtk_app, body)
    assert seen["changed"] >= 1
    assert seen["stopped"] == 1


@needs_display
def test_escape_clears_before_it_closes(gtk_app):
    """The key does the smaller thing first; the view still gets something to
    close on once there is nothing left to clear."""
    seen = {"stopped": 0}

    def body(app):
        bar = widgets.SearchBar("Search")
        bar.connect("stop-search", lambda _b: seen.__setitem__("stopped", seen["stopped"] + 1))

        bar.set_text("firefox")
        bar._on_key(None, Gdk.KEY_Escape, 0, 0)
        seen["after_first"] = (bar.get_text(), seen["stopped"])

        bar._on_key(None, Gdk.KEY_Escape, 0, 0)
        seen["after_second"] = (bar.get_text(), seen["stopped"])
        app.quit()

    run_app(gtk_app, body)
    assert seen["after_first"] == ("", 0)
    assert seen["after_second"] == ("", 1)
