"""The launcher window.

These need a display. Run the suite under `xvfb-run` to include them; without
one they are skipped rather than failing.
"""

from __future__ import annotations

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk

from tests.conftest import needs_display, run_app

pytestmark = needs_display


def rows(listbox):
    out = []
    child = listbox.get_first_child()
    while child is not None:
        if hasattr(child, "ctx"):
            out.append(child)
        child = child.get_next_sibling()
    return out


@pytest.fixture
def window_factory(gtk_app, isolated_store):
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()

    def build(app, open_titles=(), active_title=None):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window._is_open = lambda ctx: ctx.title in open_titles
        window._active_context = lambda: next(
            (c for c in store.contexts if c.title == active_title), None
        )
        window.refresh()
        return window

    return store, build


def test_saved_expanded_when_nothing_is_open(gtk_app, window_factory):
    store, build = window_factory
    store.create("alpha")
    store.create("beta")
    seen = {}

    def body(app):
        window = build(app)
        seen["expanded"] = window.saved_expander.get_expanded()
        seen["saved"] = len(rows(window.listbox))
        app.quit()

    run_app(gtk_app, body)
    assert seen["expanded"] is True
    assert seen["saved"] == 2


def test_saved_collapses_once_something_is_open(gtk_app, window_factory):
    store, build = window_factory
    store.create("alpha")
    store.create("beta")
    seen = {}

    def body(app):
        window = build(app, open_titles=("alpha",))
        seen["expanded"] = window.saved_expander.get_expanded()
        seen["still_visible"] = window.saved_expander.get_visible()
        seen["open"] = [r.ctx.title for r in rows(window.open_listbox)]
        seen["saved"] = [r.ctx.title for r in rows(window.listbox)]
        app.quit()

    run_app(gtk_app, body)
    assert seen["expanded"] is False
    # Collapsed, but reachable — not hidden behind a search.
    assert seen["still_visible"] is True
    assert seen["open"] == ["alpha"]
    assert seen["saved"] == ["beta"]


def test_expanding_by_hand_survives_a_refresh(gtk_app, window_factory):
    store, build = window_factory
    store.create("alpha")
    store.create("beta")
    seen = {}

    def body(app):
        window = build(app, open_titles=("alpha",))
        window.saved_expander.set_expanded(True)
        window.refresh()
        seen["still_expanded"] = window.saved_expander.get_expanded()
        app.quit()

    run_app(gtk_app, body)
    assert seen["still_expanded"] is True


def test_open_contexts_carry_a_close_button(gtk_app, window_factory):
    """Closing applies to something running; forgetting happens in the editor."""
    store, build = window_factory
    store.create("alpha")
    store.create("beta")
    seen = {}

    def body(app):
        window = build(app, open_titles=("alpha",))
        seen["open_can_close"] = all(
            r.close.get_visible() for r in rows(window.open_listbox)
        )
        seen["saved_cannot"] = all(
            not r.close.get_visible() for r in rows(window.listbox)
        )
        # Gtk.Widget has its own remove(), so check for the button we would
        # have added rather than the attribute name.
        seen["no_delete"] = not any(
            isinstance(getattr(r, "remove", None), Gtk.Button)
            for r in rows(window.open_listbox) + rows(window.listbox)
        )
        app.quit()

    run_app(gtk_app, body)
    assert seen["open_can_close"]
    assert seen["saved_cannot"]
    assert seen["no_delete"]


def test_the_active_context_is_marked(gtk_app, window_factory):
    store, build = window_factory
    store.create("alpha")
    seen = {}

    def body(app):
        window = build(app, open_titles=("alpha",), active_title="alpha")
        seen["active"] = [r.ctx.title for r in rows(window.open_listbox) if r.is_active]
        app.quit()

    run_app(gtk_app, body)
    assert seen["active"] == ["alpha"]


def test_exact_title_opens_rather_than_duplicating(gtk_app, window_factory):
    store, build = window_factory
    store.create("alpha")
    opened = []

    def body(app):
        from context.window import LauncherWindow

        window = LauncherWindow(app, store, lambda c: opened.append(c.title), None)
        window.entry.set_text("ALPHA")
        window._on_entry_activate(window.entry)
        app.quit()

    run_app(gtk_app, body)
    assert opened == ["alpha"]
    assert len(store.contexts) == 1


def test_urls_are_editable_rows_not_a_text_box(gtk_app, isolated_store):
    """Each URL is separately removable, rather than lines in one box."""
    from context.apps import App
    from context.resource_page import ResourcePage
    from context.resources import Resource

    resource = Resource(
        app_id="firefox.desktop", urls=["https://a.com", "https://b.com"]
    )
    app_info = App(id="firefox.desktop", name="Firefox", description="", icon=None)
    seen = {}

    def body(app):
        page = ResourcePage(app_info, resource, lambda r: None)
        seen["rows"] = len(page.url_rows())
        seen["urls"] = page.current_urls()

        page._add_url("c.com")
        seen["after_add"] = page.current_urls()

        page._remove_url(page.url_rows()[0])
        seen["after_remove"] = page.current_urls()
        app.quit()

    run_app(gtk_app, body)
    assert seen["rows"] == 2
    assert seen["urls"] == ["https://a.com", "https://b.com"]
    # A bare host is normalised on the way out.
    assert seen["after_add"][-1] == "https://c.com"
    assert seen["after_remove"] == ["https://b.com", "https://c.com"]


def test_path_apps_get_a_picker_not_a_url_list(gtk_app, isolated_store):
    from context.apps import App
    from context.resource_page import ResourcePage
    from context.resources import Resource

    resource = Resource(app_id="codium.desktop")
    app_info = App(id="codium.desktop", name="VSCodium", description="", icon=None)
    seen = {}

    def body(app):
        page = ResourcePage(app_info, resource, lambda r: None)
        seen["has_path_row"] = page.path_row is not None
        seen["urls_hidden"] = not page.url_section.get_visible()
        page._set_path("/tmp/project")
        seen["subtitle"] = page.path_row.get_subtitle()
        page._set_path(None)
        seen["cleared"] = page.path_row.get_subtitle()
        app.quit()

    run_app(gtk_app, body)
    assert seen["has_path_row"]
    assert seen["urls_hidden"]
    assert seen["subtitle"] == "/tmp/project"
    assert seen["cleared"] == "nothing chosen"


def test_a_launched_context_moves_from_saved_to_open(gtk_app, isolated_store):
    """The open list is what tells you a launch worked.

    A launch that blocked the main loop meant the poll never fired and the
    context stayed under Saved even though its windows were up.
    """
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    ctx = store.create("work")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.refresh()
        seen["before_open"] = len(rows(window.open_listbox))
        seen["before_saved"] = len(rows(window.listbox))

        window._open_ids = {ctx.id}
        window.refresh()
        seen["after_open"] = len(rows(window.open_listbox))
        seen["after_saved"] = len(rows(window.listbox))
        app.quit()

    run_app(gtk_app, body)
    assert (seen["before_open"], seen["before_saved"]) == (0, 1)
    assert (seen["after_open"], seen["after_saved"]) == (1, 0)


def test_refresh_open_state_rereads_the_backend(gtk_app, isolated_store, monkeypatch):
    """A finished launch refreshes the list instead of waiting for the poll."""
    from context.store import ContextStore
    from context import window as window_module

    store = ContextStore()
    ctx = store.create("work")
    seen = {}

    def body(app):
        win = window_module.LauncherWindow(app, store, lambda c: None, lambda c: None)
        monkeypatch.setattr(
            window_module, "open_state", lambda contexts: ({ctx.id}, ctx.id)
        )
        win.refresh_open_state()
        seen["open"] = len(rows(win.open_listbox))
        seen["active"] = win._active_context().title
        app.quit()

    run_app(gtk_app, body)
    assert seen["open"] == 1
    assert seen["active"] == "work"


def test_the_sidebar_starts_expanded(gtk_app, isolated_store):
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    store.create("alpha")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        seen["collapsed"] = window.collapsed
        seen["child"] = window.mode_stack.get_visible_child_name()
        app.quit()

    run_app(gtk_app, body)
    assert seen["collapsed"] is False
    assert seen["child"] == "full"


def test_collapsing_swaps_to_the_rail(gtk_app, isolated_store):
    """The rail replaces the launcher rather than squeezing it.

    Search and titles have nowhere to go at rail width, so the content is
    swapped out and the header — which carries both — is hidden.
    """
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    store.create("alpha")
    store.create("beta")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.toggle_collapsed()
        seen["child"] = window.mode_stack.get_visible_child_name()
        seen["header"] = window.header.get_visible()
        seen["buttons"] = _rail_buttons(window)
        window.toggle_collapsed()
        seen["back"] = window.mode_stack.get_visible_child_name()
        seen["header_back"] = window.header.get_visible()
        app.quit()

    run_app(gtk_app, body)
    assert seen["child"] == "rail"
    assert seen["header"] is False
    assert seen["buttons"] == 2
    assert seen["back"] == "full"
    assert seen["header_back"] is True


def _rail_buttons(window) -> int:
    count = 0
    child = window.rail.get_first_child()
    while child is not None:
        count += 1
        child = child.get_next_sibling()
    return count


def test_the_rail_ignores_the_search_box(gtk_app, isolated_store):
    """There is no search bar at rail width to explain a filtered list."""
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    store.create("alpha")
    store.create("beta")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.entry.set_text("alpha")
        window.toggle_collapsed()
        seen["buttons"] = _rail_buttons(window)
        app.quit()

    run_app(gtk_app, body)
    assert seen["buttons"] == 2


def test_a_rail_button_opens_its_context(gtk_app, isolated_store):
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    store.create("alpha")
    opened = []

    def body(app):
        window = LauncherWindow(app, store, opened.append, lambda c: None)
        window.toggle_collapsed()
        window.rail.get_first_child().emit("clicked")
        app.quit()

    run_app(gtk_app, body)
    assert [c.title for c in opened] == ["alpha"]


def test_the_collapsed_state_survives_a_restart(gtk_app, isolated_store):
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    store.create("alpha")
    seen = {}

    def body(app):
        first = LauncherWindow(app, store, lambda c: None, lambda c: None)
        first.is_sidebar = True  # Collapsing is only offered when docked.
        first.toggle_collapsed()

        second = LauncherWindow(app, store, lambda c: None, lambda c: None)
        second.is_sidebar = True
        second.collapsed = bool(__import__(
            "context.uistate", fromlist=["get"]
        ).get("collapsed", False))
        seen["remembered"] = second.collapsed
        app.quit()

    run_app(gtk_app, body)
    assert seen["remembered"] is True


def _rail_children(window):
    out = []
    child = window.rail.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def _classes(widget) -> set:
    return set(widget.get_css_classes())


def test_the_rail_keeps_the_open_and_saved_grouping(gtk_app, isolated_store):
    """The same two groups the expanded list shows, split by a rule.

    There is no room for headings at rail width, so the divider is what says
    these are two groups rather than one ordered list.
    """
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    running = store.create("running")
    store.create("idle")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window._open_ids = {running.id}
        window.toggle_collapsed()
        # Unfold, so both groups are on the rail at once.
        _rail_toggle(window).emit("clicked")
        children = _rail_children(window)
        seen["kinds"] = [type(c).__name__ for c in children]
        seen["classes"] = [_classes(c) for c in children if isinstance(c, Gtk.Button)]
        app.quit()

    run_app(gtk_app, body)
    # Open, a rule, the fold control, then saved.
    assert seen["kinds"] == ["Button", "Separator", "Button", "Button"]
    assert "ctx-open" in seen["classes"][0]
    assert "ctx-rail-toggle" in seen["classes"][1]
    assert "ctx-saved" in seen["classes"][2]


def test_the_rail_has_no_divider_without_both_groups(gtk_app, isolated_store):
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    store.create("idle")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.toggle_collapsed()
        seen["kinds"] = [type(c).__name__ for c in _rail_children(window)]
        app.quit()

    run_app(gtk_app, body)
    assert seen["kinds"] == ["Button"]


def test_the_active_context_outranks_merely_being_open(gtk_app, isolated_store):
    """Three states, three appearances — the one you are in is not just 'open'."""
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    here = store.create("here")
    elsewhere = store.create("elsewhere")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window._open_ids = {here.id, elsewhere.id}
        window._active_id = here.id
        window.toggle_collapsed()
        seen["classes"] = {
            b.get_tooltip_text().split(" · ")[0]: _classes(b)
            for b in _rail_children(window)
            if isinstance(b, Gtk.Button)
        }
        app.quit()

    run_app(gtk_app, body)
    assert "ctx-active" in seen["classes"]["here"]
    assert "ctx-open" not in seen["classes"]["here"]
    assert "ctx-open" in seen["classes"]["elsewhere"]
    assert "ctx-active" not in seen["classes"]["elsewhere"]


def test_the_settings_page_opens_and_is_not_duplicated(gtk_app, isolated_store):
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.open_settings()
        seen["first"] = window.nav.get_visible_page().get_tag()
        window.open_settings()
        seen["second"] = window.nav.get_visible_page().get_tag()
        app.quit()

    run_app(gtk_app, body)
    assert seen["first"] == "settings"
    assert seen["second"] == "settings"


def test_changing_a_width_resizes_without_a_restart(gtk_app, isolated_store, monkeypatch):
    """The widths are the settings most worth seeing applied immediately."""
    from context import settings, sidebar
    from context.store import ContextStore
    from context.window import LauncherWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    monkeypatch.delenv("CONTEXT_SIDEBAR_WIDTH", raising=False)
    widths = []

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        monkeypatch.setattr(sidebar, "available", lambda: True)
        monkeypatch.setattr(
            sidebar, "resize", lambda w, width, edge=None: widths.append(width)
        )
        settings.update(sidebar_width=500)
        window.settings_changed(changed={"sidebar_width": 500})
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert widths[-1] == 500


def test_hover_expansion_does_not_change_the_saved_state(gtk_app, isolated_store):
    """Peeking is not a decision — the rail is still what it goes back to."""
    from context import uistate
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    store.create("alpha")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window.toggle_collapsed()
        window._auto_expand()
        seen["expanded_now"] = window.collapsed is False
        seen["saved_state"] = uistate.get("collapsed")
        window._on_pointer_leave()
        seen["collapsed_again"] = window.collapsed
        app.quit()

    run_app(gtk_app, body)
    assert seen["expanded_now"] is True
    assert seen["saved_state"] is True
    assert seen["collapsed_again"] is True


def test_forgetting_uses_an_in_window_dialog(gtk_app, isolated_store):
    """The confirmation must draw inside the editor, not above it.

    The editor is a layer-shell overlay covering the output and holding the
    keyboard exclusively. A separate-toplevel dialog is composited underneath
    it and can never be answered — the editor just appears to freeze.
    """
    from context.editor import EditorPage
    from context.store import ContextStore

    store = ContextStore()
    ctx = store.create("doomed")
    seen = {}

    def body(app):
        page = EditorPage(ctx, lambda *a: None, lambda: None,
                          on_delete=lambda c: seen.setdefault("deleted", c),
                          is_new=False)
        window = Adw.Window(application=app)
        window.set_content(page)
        page._confirm_delete()
        # AdwDialog draws within its parent; AdwMessageDialog is a GtkWindow.
        seen["is_window"] = isinstance(_last_dialog(window), Gtk.Window)
        app.quit()

    run_app(gtk_app, body)
    assert seen["is_window"] is False


def _last_dialog(window):
    """The dialog presented on `window`, wherever it landed in the tree."""
    stack = [window.get_first_child()] if window.get_first_child() else []
    while stack:
        widget = stack.pop()
        if isinstance(widget, Adw.Dialog):
            return widget
        for nxt in (widget.get_next_sibling(), widget.get_first_child()):
            if nxt is not None:
                stack.append(nxt)
    return None


def test_forgetting_removes_the_context(gtk_app, isolated_store):
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    ctx = store.create("doomed")
    store.create("kept")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window._delete(ctx)
        seen["titles"] = [c.title for c in store.contexts]
        seen["rows"] = len(rows(window.listbox))
        app.quit()

    run_app(gtk_app, body)
    assert seen["titles"] == ["kept"]
    assert seen["rows"] == 1


def test_the_rail_folds_saved_the_same_way_the_list_does(gtk_app, isolated_store):
    """Collapsing the sidebar must not change which contexts are listed.

    The saved group is behind an accordion when expanded; the rail obeys the
    same answer rather than always showing everything.
    """
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    running = store.create("running")
    store.create("idle")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window._open_ids = {running.id}
        window.refresh()
        seen["expanded_shows_saved"] = window.saved_expander.get_expanded()

        window.toggle_collapsed()
        seen["rail_saved"] = _rail_saved_count(window)

        # Fold it from the rail, then check the expanded list agrees.
        _rail_toggle(window).emit("clicked")
        seen["rail_saved_after"] = _rail_saved_count(window)
        window.toggle_collapsed()
        seen["expanded_after"] = window.saved_expander.get_expanded()
        app.quit()

    run_app(gtk_app, body)
    # Something is open, so the saved group starts folded in both modes.
    assert seen["expanded_shows_saved"] is False
    assert seen["rail_saved"] == 0
    # Unfolding on the rail carries back to the expanded list.
    assert seen["rail_saved_after"] == 1
    assert seen["expanded_after"] is True


def test_the_rail_shows_saved_when_nothing_is_open(gtk_app, isolated_store):
    """With nothing running the saved list is the whole rail."""
    from context.store import ContextStore
    from context.window import LauncherWindow

    store = ContextStore()
    store.create("idle")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.toggle_collapsed()
        seen["saved"] = _rail_saved_count(window)
        seen["toggle"] = _rail_toggle(window)
        app.quit()

    run_app(gtk_app, body)
    assert seen["saved"] == 1
    # No fold control: a control that empties the whole rail is a trap.
    assert seen["toggle"] is None


def _rail_saved_count(window) -> int:
    return sum(
        1
        for c in _rail_children(window)
        if isinstance(c, Gtk.Button) and "ctx-saved" in c.get_css_classes()
    )


def _rail_toggle(window):
    for child in _rail_children(window):
        if isinstance(child, Gtk.Button) and "ctx-rail-toggle" in child.get_css_classes():
            return child
    return None


def test_hiding_gives_back_all_the_space(gtk_app, isolated_store, monkeypatch):
    """Hidden collapses to a sliver, not to the rail width."""
    from context import settings, sidebar
    from context.store import ContextStore
    from context.window import LauncherWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    widths = []
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        monkeypatch.setattr(sidebar, "available", lambda: True)
        monkeypatch.setattr(
            sidebar, "resize", lambda w, width, edge=None: widths.append(width)
        )
        settings.update(collapse_mode="hidden")
        window.toggle_collapsed()
        seen["page"] = window.mode_stack.get_visible_child_name()
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert widths[-1] == sidebar.HIDDEN_WIDTH
    assert seen["page"] == "hidden"


def test_hiding_always_reveals_on_hover(gtk_app, isolated_store, monkeypatch):
    """A setting that can strand the launcher is a trap, not a preference.

    With nothing on screen but a sliver, hover is the only way back short of a
    keybind — so it works whether or not expand-on-hover is switched on.
    """
    from context import settings, sidebar
    from context.store import ContextStore
    from context.window import LauncherWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        monkeypatch.setattr(sidebar, "available", lambda: True)
        monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)

        settings.update(collapse_mode="hidden", auto_expand=False)
        window.toggle_collapsed()
        window._on_pointer_enter()
        seen["scheduled_when_hidden"] = window._auto_expand_source is not None

        # A rail is visible on its own, so hover only expands when asked to.
        settings.update(collapse_mode="rail", auto_expand=False)
        window._auto_expand_source = None
        window._on_pointer_enter()
        seen["scheduled_when_rail"] = window._auto_expand_source is not None
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen["scheduled_when_hidden"] is True
    assert seen["scheduled_when_rail"] is False


def _mode(monkeypatch, isolated_store, **changes):
    from context import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(**changes)


def test_never_collapse_hides_the_button(gtk_app, isolated_store, monkeypatch):
    from context import sidebar
    from context.store import ContextStore
    from context.window import LauncherWindow

    _mode(monkeypatch, isolated_store, collapse_mode="none")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        monkeypatch.setattr(sidebar, "available", lambda: True)
        monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
        seen["collapses"] = window.collapses
        window.settings_changed()
        seen["button_visible"] = (
            window.collapse_button.get_visible()
            if window.collapse_button is not None
            else None
        )
        # The button is gone, so the toggle must not shrink it either.
        window.toggle_collapsed()
        seen["still_expanded"] = window.collapsed is False
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen["collapses"] is False
    assert seen["still_expanded"] is True


def test_switching_collapsing_off_expands_it_again(gtk_app, isolated_store, monkeypatch):
    """Otherwise the sidebar is left shrunk with no button to grow it."""
    from context import settings, sidebar, uistate
    from context.store import ContextStore
    from context.window import LauncherWindow

    _mode(monkeypatch, isolated_store, collapse_mode="rail")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        monkeypatch.setattr(sidebar, "available", lambda: True)
        monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)

        window.toggle_collapsed()
        seen["collapsed_first"] = window.collapsed

        settings.update(collapse_mode="none")
        window.settings_changed()
        seen["expanded_after"] = window.collapsed is False
        seen["state_cleared"] = uistate.get("collapsed") is False
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen["collapsed_first"] is True
    assert seen["expanded_after"] is True
    assert seen["state_cleared"] is True


def test_a_stored_collapse_is_ignored_when_collapsing_is_off(
    gtk_app, isolated_store, monkeypatch
):
    from context import uistate
    from context.store import ContextStore
    from context.window import LauncherWindow

    _mode(monkeypatch, isolated_store, collapse_mode="none")
    uistate.save(collapsed=True)
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        seen["collapsed"] = window.collapsed
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen["collapsed"] is False


def test_rail_and_never_collapse_stay_pinned(gtk_app, isolated_store, monkeypatch):
    """Only hiding unpins. A rail and never-collapse both reserve space."""
    from context import settings
    from context.store import ContextStore
    from context.window import LauncherWindow

    _mode(monkeypatch, isolated_store, collapse_mode="rail")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        seen["rail"] = window.hides_when_collapsed
        settings.update(collapse_mode="none")
        seen["none"] = window.hides_when_collapsed
        settings.update(collapse_mode="hidden")
        seen["hidden"] = window.hides_when_collapsed
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert seen["rail"] is False
    assert seen["none"] is False
    assert seen["hidden"] is True


def test_settings_rows_hide_when_they_do_nothing(gtk_app, isolated_store, monkeypatch):
    """A width that applies to no mode is noise, not a setting."""
    from context import settings
    from context.settings_page import SettingsPage
    from context.store import ContextStore
    from context.window import LauncherWindow

    _mode(monkeypatch, isolated_store, collapse_mode="rail", auto_expand=False)
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        page = SettingsPage(window)
        seen["rail_width_on_rail"] = page.rail_width_row.get_visible()
        seen["hover_on_rail"] = page.hover_row.get_visible()
        seen["delay_hidden_without_hover"] = not page.hover_delay_row.get_visible()

        settings.update(collapse_mode="hidden")
        page._sync_rows()
        seen["rail_width_when_hidden"] = page.rail_width_row.get_visible()
        # Hiding always reveals on hover, so the delay still matters.
        seen["delay_when_hidden"] = page.hover_delay_row.get_visible()

        settings.update(collapse_mode="none")
        page._sync_rows()
        seen["hover_when_never"] = page.hover_row.get_visible()
        seen["rail_width_when_never"] = page.rail_width_row.get_visible()
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert seen["rail_width_on_rail"] is True
    assert seen["hover_on_rail"] is True
    assert seen["delay_hidden_without_hover"] is True
    assert seen["rail_width_when_hidden"] is False
    assert seen["delay_when_hidden"] is True
    assert seen["hover_when_never"] is False
    assert seen["rail_width_when_never"] is False


def test_the_launcher_width_is_not_a_collapsing_setting(gtk_app, isolated_store, monkeypatch):
    """It applies in every mode, including the one that never collapses.

    Collapsing only decides what the launcher shrinks *to*, so that width sits
    with the collapse mode and this one sits with the rest of the appearance.
    """
    from context import settings
    from context.settings_page import SettingsPage
    from context.store import ContextStore
    from context.window import LauncherWindow

    _mode(monkeypatch, isolated_store, collapse_mode="none")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        page = SettingsPage(window)
        seen["titles"] = _row_titles(page)
        settings.update(sidebar_width=444)
        seen["applied"] = settings.current().sidebar_width
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert "Width" in seen["titles"]
    assert "Expanded width" not in seen["titles"]
    assert seen["applied"] == 444


def _row_titles(page) -> list[str]:
    titles = []
    stack = [page]
    while stack:
        widget = stack.pop()
        title = getattr(widget, "get_title", None)
        if title is not None and isinstance(widget, Adw.PreferencesRow):
            titles.append(widget.get_title())
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return titles


def test_a_restart_setting_offers_the_restart(gtk_app, isolated_store, monkeypatch):
    """Saying "applies on restart" is only useful with a way to do it."""
    from context import settings
    from context.store import ContextStore
    from context.window import LauncherWindow

    _mode(monkeypatch, isolated_store, collapse_mode="rail")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        added = []
        monkeypatch.setattr(
            window.toasts, "add_toast", lambda t: added.append(t)
        )
        window.settings_changed(needs_restart=True, changed={"sidebar_edge": "right"})
        seen["label"] = added[0].get_button_label() if added else None
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert seen["label"] == "Restart"


def test_restart_replaces_the_process(gtk_app, isolated_store, monkeypatch):
    """execv, so watchers see a restart rather than a disappearance."""
    from context.app import ContextApplication

    calls = []
    monkeypatch.setattr(
        "context.app.os.execv", lambda path, argv: calls.append((path, argv))
    )
    seen = {}

    def body(app):
        holder = ContextApplication.__new__(ContextApplication)
        holder.log = __import__("logging").getLogger("test.restart")
        holder.get_windows = lambda: []
        ContextApplication.restart(holder)
        # Queued on idle so the windows close first; run it.
        from gi.repository import GLib

        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        seen["calls"] = list(calls)
        app.quit()

    run_app(gtk_app, body)
    assert len(seen["calls"]) == 1
    path, argv = seen["calls"][0]
    assert argv[1:3] == ["-m", "context"]


def test_restart_is_a_command(gtk_app, isolated_store):
    from context.app import COMMANDS

    assert "restart" in COMMANDS


def test_dragging_a_window_off_a_preview_moves_it_to_the_next_screen(
    gtk_app, isolated_store, monkeypatch
):
    """The whole cross-screen gesture, from drag-begin to the assignment.

    It reported success at every step while doing nothing: the arrangement had
    one screen, so `assign` clamped the target back to where it started.
    """
    from context import settings
    from context.editor import EditorPage
    from context.resources import Resource
    from context.store import ContextStore

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(max_screens=2)
    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create(
            "probe", resources=[Resource(app_id="a"), Resource(app_id="b")]
        )
        page = EditorPage(ctx, lambda *x: None, lambda: None, on_delete=None)
        page.screen_count = 2
        page._build_previews()
        page._update_state()

        seen["screens"] = len(page.arrangement.screens)
        seen["before"] = dict(page.arrangement.assignments)

        preview = page.previews[0]
        preview.set_size_request(400, 300)
        # A drag that ends past the right edge of the drawn screen.
        preview._drag = ("move", 0, 10.0, 10.0, preview.layout.slots[0])
        preview._on_drag_update(None, 5000.0, 0.0)
        seen["leaving"] = preview._leaving
        seen["lit"] = page.previews[1]._drop_target
        preview._end_drag()

        seen["after"] = dict(page.arrangement.assignments)
        app.quit()

    run_app(gtk_app, body)
    # Two previews means two screens in the data, or the move has nowhere to go.
    assert seen["screens"] == 2
    assert seen["leaving"] == 1
    assert seen["lit"] is True
    assert seen["before"][0] == 0
    assert seen["after"][0] == 1


def test_a_lone_preview_has_nowhere_to_drag_to(gtk_app, isolated_store, monkeypatch):
    from context import settings
    from context.editor import EditorPage
    from context.resources import Resource
    from context.store import ContextStore

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(max_screens=1)
    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create("probe", resources=[Resource(app_id="a")])
        page = EditorPage(ctx, lambda *x: None, lambda: None, on_delete=None)
        page.screen_count = 1
        page._build_previews()
        page._update_state()

        preview = page.previews[0]
        preview._drag = ("move", 0, 10.0, 10.0, preview.layout.slots[0])
        preview._on_drag_update(None, 5000.0, 0.0)
        preview._end_drag()
        seen["assignments"] = dict(page.arrangement.assignments)
        app.quit()

    run_app(gtk_app, body)
    assert seen["assignments"][0] == 0


def test_the_edited_screen_mode_survives_done(gtk_app, isolated_store, monkeypatch):
    """The editor handed back only the flat layout, so mode edits were lost.

    Arranging two screens and pressing Done left the context exactly as it
    was — the arrangement was never given to anything that saves.
    """
    from context import settings
    from context.editor import EditorPage
    from context.resources import Resource
    from context.store import ContextStore

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(max_screens=2)
    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create(
            "probe", resources=[Resource(app_id="a"), Resource(app_id="b")]
        )
        page = EditorPage(ctx, lambda *a_: None, lambda: None, on_delete=None)
        page.screen_count = 2
        page._build_previews()
        page._update_state()
        page._move_to_screen(0, 1, 1)

        seen["before"] = sorted(ctx.arrangements)
        page.title_row.set_text("probe")
        page._commit()
        seen["after"] = sorted(ctx.arrangements)
        seen["assignments"] = dict(ctx.arrangement_for(2).assignments)
        app.quit()

    run_app(gtk_app, body)
    assert seen["before"] == []
    assert seen["after"] == [2]
    assert seen["assignments"][1] == 1


def test_switching_screen_mode_rebuilds_the_previews(
    gtk_app, isolated_store, monkeypatch
):
    from context import settings
    from context.editor import EditorPage
    from context.resources import Resource
    from context.store import ContextStore

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(max_screens=3)
    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create("probe", resources=[Resource(app_id="a")])
        page = EditorPage(ctx, lambda *a_: None, lambda: None, on_delete=None)

        page.mode_dropdown.set_selected(2)
        seen["three"] = (page.screen_count, len(page.previews))
        page.mode_dropdown.set_selected(0)
        seen["one"] = (page.screen_count, len(page.previews))
        app.quit()

    run_app(gtk_app, body)
    assert seen["three"] == (3, 3)
    assert seen["one"] == (1, 1)


def test_switching_mode_keeps_what_the_other_mode_had(
    gtk_app, isolated_store, monkeypatch
):
    """Each screen mode is a separate layout; editing one must not clear another."""
    from context import settings
    from context.editor import EditorPage
    from context.resources import Resource
    from context.store import ContextStore

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(max_screens=2)
    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create(
            "probe", resources=[Resource(app_id="a"), Resource(app_id="b")]
        )
        page = EditorPage(ctx, lambda *a_: None, lambda: None, on_delete=None)

        page.mode_dropdown.set_selected(1)      # two screens
        page._move_to_screen(0, 1, 1)
        page.mode_dropdown.set_selected(0)      # back to one
        seen["one_screen"] = dict(page.arrangement.assignments)
        page.mode_dropdown.set_selected(1)      # and back again
        seen["two_screen"] = dict(page.arrangement.assignments)
        app.quit()

    run_app(gtk_app, body)
    # One screen keeps everything on screen 0; the two-screen edit survived.
    assert set(seen["one_screen"].values()) == {0}
    assert seen["two_screen"][1] == 1


def test_the_sidebar_is_focusable_on_demand(gtk_app, isolated_store, monkeypatch):
    """The compositor decides focus, the way it does for an ordinary window.

    Driving it from the pointer made anything with a popover unusable: opening
    a dropdown sends the sidebar a pointer-leave, the keyboard was dropped, and
    the popover dismissed itself a frame later.
    """
    from context import sidebar

    modes = []

    class FakeLayerShell:
        class KeyboardMode:
            NONE = 0
            ON_DEMAND = 1
            EXCLUSIVE = 2

        class Layer:
            TOP = 0
            OVERLAY = 3

        class Edge:
            LEFT = RIGHT = TOP = BOTTOM = 0

        @staticmethod
        def init_for_window(_w):
            return None

        @staticmethod
        def set_namespace(_w, _n):
            return None

        @staticmethod
        def set_layer(_w, _l):
            return None

        @staticmethod
        def set_anchor(_w, _e, _a):
            return None

        @staticmethod
        def auto_exclusive_zone_enable(_w):
            return None

        @staticmethod
        def set_keyboard_mode(_w, mode):
            modes.append(mode)

    monkeypatch.setattr(sidebar, "LayerShell", FakeLayerShell)
    monkeypatch.setattr(sidebar, "available", lambda: True)
    monkeypatch.setattr(sidebar, "place", lambda *a_, **k_: None)

    def body(app):
        window = Adw.ApplicationWindow(application=app)
        sidebar.apply(window)
        app.quit()

    run_app(gtk_app, body)
    assert modes == [FakeLayerShell.KeyboardMode.ON_DEMAND]


def test_releasing_focus_returns_to_on_demand(gtk_app, isolated_store, monkeypatch):
    """There is no "unfocus me" request, so it drops out and straight back in.

    Staying on NONE would leave the sidebar unclickable for the rest of the
    session.
    """
    from context import sidebar

    modes = []

    class FakeLayerShell:
        class KeyboardMode:
            NONE = 0
            ON_DEMAND = 1

        @staticmethod
        def set_keyboard_mode(_w, mode):
            modes.append(mode)

    monkeypatch.setattr(sidebar, "LayerShell", FakeLayerShell)
    monkeypatch.setattr(sidebar, "available", lambda: True)

    def body(app):
        sidebar.release_focus(Adw.ApplicationWindow(application=app))
        app.quit()

    run_app(gtk_app, body)
    assert modes == [
        FakeLayerShell.KeyboardMode.NONE,
        FakeLayerShell.KeyboardMode.ON_DEMAND,
    ]


def test_hovering_no_longer_touches_the_keyboard(gtk_app, isolated_store, monkeypatch):
    """Hover is only about expanding a collapsed sidebar now."""
    from context import settings, sidebar
    from context.store import ContextStore
    from context.window import LauncherWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    released = []
    monkeypatch.setattr(sidebar, "release_focus", lambda _w: released.append(True))
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window._on_pointer_enter()
        window._on_pointer_leave()
        seen["released"] = list(released)
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen["released"] == []
