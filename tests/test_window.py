"""The launcher window.

These need a display. Run the suite under `xvfb-run` to include them; without
one they are skipped rather than failing.
"""

from __future__ import annotations

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from context.ui import widgets

from tests.conftest import needs_display, run_app

pytestmark = needs_display


def rows(listbox):
    """The context rows in a list."""
    out = []
    child = listbox.get_first_child()
    while child is not None:
        if hasattr(child, "ctx"):
            out.append(child)
        child = child.get_next_sibling()
    return out


@pytest.fixture
def window_factory(gtk_app, isolated_store):
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
        from context.ui.window import LauncherWindow

        window = LauncherWindow(app, store, lambda c: opened.append(c.title), None)
        window.entry.set_text("ALPHA")
        window._on_entry_activate(window.entry)
        app.quit()

    run_app(gtk_app, body)
    assert opened == ["alpha"]
    assert len(store.contexts) == 1


def test_urls_are_editable_rows_not_a_text_box(gtk_app, isolated_store):
    """Each URL is separately removable, rather than lines in one box."""
    from context.system.apps import App
    from context.ui.resource_page import ResourcePage
    from context.state.resources import Resource

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
    from context.system.apps import App
    from context.ui.resource_page import ResourcePage
    from context.state.resources import Resource

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
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state.store import ContextStore
    from context.ui import window as window_module

    store = ContextStore()
    ctx = store.create("work")
    seen = {}

    def body(app):
        win = window_module.LauncherWindow(app, store, lambda c: None, lambda c: None)
        from context.system.launcher import LiveState

        monkeypatch.setattr(
            window_module,
            "read_live_state",
            lambda contexts, backend=None: LiveState(
                open_ids={ctx.id}, active_id=ctx.id
            ),
        )
        win.refresh_open_state()
        seen["open"] = len(rows(win.open_listbox))
        seen["active"] = win._active_context().title
        app.quit()

    run_app(gtk_app, body)
    assert seen["open"] == 1
    assert seen["active"] == "work"


def test_the_sidebar_starts_expanded(gtk_app, isolated_store):
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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


def test_the_collapsed_state_survives_a_restart(gtk_app, isolated_store, monkeypatch):
    from context.state import uistate
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    store = ContextStore()
    store.create("alpha")
    seen = {}

    def body(app):
        first = LauncherWindow(app, store, lambda c: None, lambda c: None)
        first.is_sidebar = True  # Collapsing is only offered when docked.
        # Through the application, which is what owns the stored state now.
        holder = _fake_app_with([first])
        monkeypatch.setattr(first, "get_application", lambda: holder)
        first.toggle_collapsed()
        uistate.save(collapsed=first.collapsed)

        second = LauncherWindow(app, store, lambda c: None, lambda c: None)
        second.is_sidebar = True
        second.collapsed = bool(__import__(
            "context.state.uistate", fromlist=["get"]
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
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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


def test_settings_open_as_a_screen_of_their_own(gtk_app, isolated_store):
    """They outgrew the sidebar: twenty-odd controls in a 380px column meant
    scrolling past three groups to reach the fourth."""
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    store = ContextStore()
    opened = []

    def body(app):
        app.open_settings = lambda: opened.append(True)
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.settings_button.emit("clicked")
        # And not as a page pushed over the list.
        opened.append(window.nav.find_page("settings") is None)
        app.quit()

    run_app(gtk_app, body)
    assert opened == [True, True]


def test_changing_a_width_resizes_without_a_restart(gtk_app, isolated_store, monkeypatch):
    """The widths are the settings most worth seeing applied immediately."""
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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


def test_hover_expansion_does_not_change_the_saved_state(
    gtk_app, isolated_store, monkeypatch
):
    """Peeking is not a decision — the rail is still what it goes back to."""
    from context.state import uistate
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    store = ContextStore()
    store.create("alpha")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        holder = _fake_app_with([window])
        monkeypatch.setattr(window, "get_application", lambda: holder)
        window.toggle_collapsed()
        uistate.save(collapsed=window.collapsed)

        window._auto_expand()
        seen["expanded_now"] = window.collapsed is False
        # Peeking must not rewrite what was stored.
        seen["saved_state"] = uistate.get("collapsed")
        _instant_collapse(monkeypatch)
        window._on_pointer_leave()
        window._collapse_after_leave()  # the grace period, elapsed
        seen["collapsed_again"] = window.collapsed
        app.quit()

    run_app(gtk_app, body)
    assert seen["expanded_now"] is True
    assert seen["saved_state"] is True
    assert seen["collapsed_again"] is True


def test_forgetting_removes_the_context(gtk_app, isolated_store):
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
        # Collapsing shrinks the window out from under the pointer, so the
        # compositor sends a leave before any later hover.
        window._on_pointer_leave()
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


def _instant_collapse(monkeypatch) -> None:
    """No collapse delay, for the tests that are about something else."""
    from context.state import settings

    monkeypatch.setattr(
        settings, "_current", settings.current().replace(collapse_delay_ms=0)
    )


def _catch_notifications(monkeypatch) -> list:
    """Collect what would have gone to the notification daemon."""
    from context.system import notify

    sent = []

    def fake(app, key, title, body="", **extra):
        sent.append({"key": key, "title": title, "body": body, **extra})
        return True

    monkeypatch.setattr(notify, "send", fake)
    return sent


def _mode(monkeypatch, isolated_store, **changes):
    from context.state import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(**changes)


def test_never_collapse_hides_the_button(gtk_app, isolated_store, monkeypatch):
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state import settings, uistate
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state import uistate
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state import settings
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state import settings
    from context.ui.settings_page import SettingsPage
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
    from context.state import settings
    from context.ui.settings_page import SettingsPage
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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
        if title is not None and isinstance(widget, widgets.Row):
            titles.append(widget.get_title())
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return titles


def test_a_restart_setting_offers_the_restart(gtk_app, isolated_store, monkeypatch):
    """Saying "applies on restart" is only useful with a way to do it."""
    from context.state import settings
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    _mode(monkeypatch, isolated_store, collapse_mode="rail")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        sent = _catch_notifications(monkeypatch)
        restarted = []
        monkeypatch.setattr(window, "_restart_app", lambda: restarted.append(True))
        window.settings_changed(needs_restart=True, changed={"sidebar_edge": "right"})
        seen["label"] = sent[0]["button"] if sent else None
        # The button is only useful if it still reaches the restart.
        if sent:
            sent[0]["on_click"]()
        seen["restarted"] = restarted
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert seen["label"] == "Restart"
    assert seen["restarted"] == [True]


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
        # Restart is the one thing entitled to take the overview down.
        holder.overview = None
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
    from context.state import settings
    from context.ui.editor import EditorPage
    from context.state.resources import Resource
    from context.state.store import ContextStore

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
    from context.state import settings
    from context.ui.editor import EditorPage
    from context.state.resources import Resource
    from context.state.store import ContextStore

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
    from context.state import settings
    from context.ui.editor import EditorPage
    from context.state.resources import Resource
    from context.state.store import ContextStore

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
    from context.state import settings
    from context.ui.editor import EditorPage
    from context.state.resources import Resource
    from context.state.store import ContextStore

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(max_screens=3)
    seen = {}

    def body(app):
        store = ContextStore()
        ctx = store.create("probe", resources=[Resource(app_id="a")])
        page = EditorPage(ctx, lambda *a_: None, lambda: None, on_delete=None)

        page.mode_chooser.set_selected(2)
        seen["three"] = (page.screen_count, len(page.previews))
        page.mode_chooser.set_selected(0)
        seen["one"] = (page.screen_count, len(page.previews))
        app.quit()

    run_app(gtk_app, body)
    assert seen["three"] == (3, 3)
    assert seen["one"] == (1, 1)


def test_switching_mode_keeps_what_the_other_mode_had(
    gtk_app, isolated_store, monkeypatch
):
    """Each screen mode is a separate layout; editing one must not clear another."""
    from context.state import settings
    from context.ui.editor import EditorPage
    from context.state.resources import Resource
    from context.state.store import ContextStore

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

        page.mode_chooser.set_selected(1)      # two screens
        page._move_to_screen(0, 1, 1)
        page.mode_chooser.set_selected(0)      # back to one
        seen["one_screen"] = dict(page.arrangement.assignments)
        page.mode_chooser.set_selected(1)      # and back again
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
    from context.ui import sidebar

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

        @staticmethod
        def set_margin(_w, _e, _px):
            return None

    monkeypatch.setattr(sidebar, "LayerShell", FakeLayerShell)
    monkeypatch.setattr(sidebar, "available", lambda: True)
    monkeypatch.setattr(sidebar, "place", lambda *a_, **k_: None)

    def body(app):
        window = Gtk.ApplicationWindow(application=app)
        sidebar.apply(window)
        app.quit()

    run_app(gtk_app, body)
    assert modes == [FakeLayerShell.KeyboardMode.ON_DEMAND]


def test_releasing_focus_returns_to_on_demand(gtk_app, isolated_store, monkeypatch):
    """There is no "unfocus me" request, so it drops out and straight back in.

    Staying on NONE would leave the sidebar unclickable for the rest of the
    session.
    """
    from context.ui import sidebar

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
        sidebar.release_focus(Gtk.ApplicationWindow(application=app))
        app.quit()

    run_app(gtk_app, body)
    assert modes == [
        FakeLayerShell.KeyboardMode.NONE,
        FakeLayerShell.KeyboardMode.ON_DEMAND,
    ]


def test_hovering_no_longer_touches_the_keyboard(gtk_app, isolated_store, monkeypatch):
    """Hover is only about expanding a collapsed sidebar now."""
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

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


def test_clicking_a_row_opens_its_context(gtk_app, isolated_store):
    """A context row activates on click.

    Pinned because it shipped broken: ActionRow only armed its click gesture
    when `activatable` was passed to the constructor, and ContextRow calls
    `set_activatable(True)` afterwards — which set the GTK property and armed
    nothing, so clicking a context silently did nothing while the row's
    buttons kept working.
    """
    from context.ui.window import ContextRow
    from context.state.store import Context

    opened = []
    seen = {}

    def body(app):
        row = ContextRow(
            Context(title="clickable"),
            lambda c: opened.append(c),
            lambda c: None,
            lambda c: None,
        )
        gestures = [
            c for c in row.observe_controllers() if isinstance(c, Gtk.GestureClick)
        ]
        seen["armed"] = bool(gestures)
        for gesture in gestures:
            gesture.emit("released", 1, 0.0, 0.0)
        seen["opened"] = list(opened)
        app.quit()

    run_app(gtk_app, body)
    assert seen["armed"] is True
    assert [c.title for c in seen["opened"]] == ["clickable"]


# -- handing the keyboard back -----------------------------------------------
#
# Hyprland hands an on-demand layer the keyboard when it is clicked, but keeps
# counting the window underneath as the focused window — so clicking back into
# that same window never triggers a refocus, and typing lands in the sidebar
# until some *other* window is focused first. Every way of leaving the sidebar
# has to hand the keyboard back deliberately.


def test_the_compositor_sees_the_release(gtk_app, isolated_store, monkeypatch):
    """The NONE has to be committed before ON_DEMAND is restored.

    Keyboard interactivity is double-buffered: setting NONE and ON_DEMAND
    inside one commit collapses to no change at all, and the release never
    happened as far as the compositor is concerned.
    """
    from context.ui import sidebar

    modes = []

    class FakeLayerShell:
        class KeyboardMode:
            NONE = 0
            ON_DEMAND = 1

        @staticmethod
        def set_keyboard_mode(_w, mode):
            modes.append(mode)

    class FakeClock:
        def __init__(self):
            self.callbacks = {}
            self.serial = 1

        def connect(self, _signal, callback):
            handler, self.serial = self.serial, self.serial + 1
            self.callbacks[handler] = callback
            return handler

        def disconnect(self, handler):
            self.callbacks.pop(handler, None)

        def paint(self):
            for callback in list(self.callbacks.values()):
                callback(self)

    monkeypatch.setattr(sidebar, "LayerShell", FakeLayerShell)
    monkeypatch.setattr(sidebar, "available", lambda: True)
    seen = {}

    def body(app):
        window = Gtk.ApplicationWindow(application=app)
        clock = FakeClock()
        window.get_frame_clock = lambda: clock
        window.get_mapped = lambda: True
        window.queue_draw = lambda: None
        sidebar.release_focus(window)
        seen["at_release"] = list(modes)
        clock.paint()
        seen["after_commit"] = list(modes)
        # A second paint must not restore twice.
        clock.paint()
        seen["later"] = list(modes)
        app.quit()

    run_app(gtk_app, body)
    assert seen["at_release"] == [FakeLayerShell.KeyboardMode.NONE]
    assert seen["after_commit"] == [
        FakeLayerShell.KeyboardMode.NONE,
        FakeLayerShell.KeyboardMode.ON_DEMAND,
    ]
    assert seen["later"] == seen["after_commit"]


def test_releasing_hands_the_keyboard_to_the_last_window(
    gtk_app, isolated_store, monkeypatch, backend
):
    """Dropping the layer's keyboard mode alone is not enough.

    Hyprland reports the window active again without re-sending the keyboard
    enter, so the window is focused explicitly — the recovery that clicking
    another window and coming back performs, done automatically.
    """
    from context.ui import sidebar
    from context.system.backends.base import WindowInfo
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setattr(sidebar, "release_focus", lambda _w: None)
    backend.open_windows = [
        WindowInfo(id="0xrecent", title="editor", app_id="editor"),
        WindowInfo(id="0xolder", title="terminal", app_id="terminal"),
    ]
    seen = {}

    def body(app):
        app.backend = backend
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window._release_keyboard()
        seen["focused"] = [c for c in backend.calls if c[0] == "focus"]
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert seen["focused"] == [("focus", "0xrecent")]


def test_leaving_with_the_keyboard_hands_it_back(
    gtk_app, isolated_store, monkeypatch, backend
):
    """Clicking the sidebar then clicking back into the window is the report
    this exists to fix: the keyboard stayed here and typing went nowhere."""
    from context.ui import sidebar
    from context.system.backends.base import WindowInfo
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    released = []
    monkeypatch.setattr(sidebar, "release_focus", lambda _w: released.append(True))
    backend.open_windows = [WindowInfo(id="0xrecent", title="editor", app_id="editor")]
    seen = {}

    def body(app):
        app.backend = backend
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window._holds_keyboard = lambda: True
        window._on_pointer_enter()
        window._on_pointer_leave()
        seen["released"] = list(released)
        seen["focused"] = [c for c in backend.calls if c[0] == "focus"]
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert seen["released"] == [True]
    assert seen["focused"] == [("focus", "0xrecent")]


def test_a_popover_keeps_the_keyboard_until_it_closes(
    gtk_app, isolated_store, monkeypatch, backend
):
    """Opening a dropdown sends a synthetic pointer-leave; releasing on it
    dismissed the popover a frame later. The release waits for the popover,
    then still happens if the pointer stayed outside."""
    from gi.repository import GLib

    from context.ui import sidebar
    from context.system.backends.base import WindowInfo
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    released = []
    monkeypatch.setattr(sidebar, "release_focus", lambda _w: released.append(True))
    backend.open_windows = [WindowInfo(id="0xrecent", title="editor", app_id="editor")]
    seen = {}

    def body(app):
        app.backend = backend
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window._holds_keyboard = lambda: True
        window._popover_open = lambda: True
        window._on_pointer_enter()
        window._on_pointer_leave()
        seen["while_open"] = list(released)
        seen["watching"] = window._popover_watch is not None
        window._popover_open = lambda: False
        seen["outcome"] = window._release_after_popover()
        seen["after_close"] = list(released)
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert seen["while_open"] == []
    assert seen["watching"] is True
    assert seen["outcome"] == GLib.SOURCE_REMOVE
    assert seen["after_close"] == [True]


def test_leaving_without_the_keyboard_releases_nothing(
    gtk_app, isolated_store, monkeypatch, backend
):
    """Passing the pointer through the sidebar must not touch focus."""
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    released = []
    monkeypatch.setattr(sidebar, "release_focus", lambda _w: released.append(True))
    seen = {}

    def body(app):
        app.backend = backend
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window._on_pointer_enter()
        window._on_pointer_leave()
        seen["released"] = list(released)
        seen["focused"] = [c for c in backend.calls if c[0] == "focus"]
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert seen["released"] == []
    assert seen["focused"] == []


def test_opening_a_context_hands_the_keyboard_over(
    gtk_app, isolated_store, monkeypatch, backend
):
    """The windows being opened should get the keyboard, not the launcher."""
    from context.ui import sidebar
    from context.system.backends.base import WindowInfo
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setattr(sidebar, "release_focus", lambda _w: None)
    backend.open_windows = [WindowInfo(id="0xrecent", title="editor", app_id="editor")]
    seen = {}

    def body(app):
        app.backend = backend
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window._open(ctx)
        seen["focused"] = [c for c in backend.calls if c[0] == "focus"]
        app.quit()

    store = ContextStore()
    ctx = store.create("alpha")
    run_app(gtk_app, body)
    assert seen["focused"] == [("focus", "0xrecent")]


# -- more than one launcher --------------------------------------------------
#
# Every test above builds a single LauncherWindow directly. That is why two
# bugs shipped: collapsing applied to the launcher that was clicked while the
# stored state is global, so the other screen stayed expanded and disagreed
# with what was saved.


def _fake_app_with(launchers):
    """A stand-in application that owns several launchers."""

    class Holder:
        def __init__(self):
            self.launchers = launchers
            self.saved = []

        def set_collapsed(self, collapsed):
            self.saved.append(collapsed)
            for window in self.launchers:
                window.set_collapsed(collapsed)

    return Holder()


def test_collapsing_applies_to_every_launcher(gtk_app, isolated_store, monkeypatch):
    """The collapsed state is stored once, so the launchers cannot disagree.

    Collapsing one and leaving the other expanded meant whichever restarted
    last decided what the setting had been.
    """
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(collapse_mode="rail")
    monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
    seen = {}

    def body(app):
        store = ContextStore()
        store.create("alpha")
        first = LauncherWindow(app, store, lambda c: None, lambda c: None)
        second = LauncherWindow(app, store, lambda c: None, lambda c: None)
        for window in (first, second):
            window.is_sidebar = True

        holder = _fake_app_with([first, second])
        monkeypatch.setattr(first, "get_application", lambda: holder)

        first.toggle_collapsed()
        seen["both_collapsed"] = (first.collapsed, second.collapsed)
        seen["saved_once"] = holder.saved

        first.toggle_collapsed()
        seen["both_expanded"] = (first.collapsed, second.collapsed)
        app.quit()

    run_app(gtk_app, body)
    assert seen["both_collapsed"] == (True, True)
    assert seen["both_expanded"] == (False, False)
    assert seen["saved_once"] == [True, False]


def test_every_launcher_shows_the_rail(gtk_app, isolated_store, monkeypatch):
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(collapse_mode="rail")
    monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
    seen = {}

    def body(app):
        store = ContextStore()
        store.create("alpha")
        windows = [
            LauncherWindow(app, store, lambda c: None, lambda c: None) for _ in range(2)
        ]
        for window in windows:
            window.is_sidebar = True
        for window in windows:
            window.set_collapsed(True)
        seen["pages"] = [w.mode_stack.get_visible_child_name() for w in windows]
        app.quit()

    run_app(gtk_app, body)
    assert seen["pages"] == ["rail", "rail"]


def test_the_rail_reaches_the_width_it_was_set_to(gtk_app, isolated_store, monkeypatch):
    """Asserting the stack switched says nothing about how wide it ended up.

    The rail came out at 44px when set to 32, because a fixed icon size and the
    expand button's own minimum were both larger than the rail.
    """
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    seen = {}

    def body(app):
        store = ContextStore()
        store.create("alpha")
        for width in (settings.MIN_RAIL_WIDTH, 48, 80):
            settings.update(rail_width=width, collapse_mode="rail")
            window = LauncherWindow(app, store, lambda c: None, lambda c: None)
            window.present()
            window.mode_stack.set_visible_child_name("rail")
            window.header.set_visible(False)
            window.collapsed = True
            window.refresh()
            window.set_size_request(width, -1)
            minimum, _, _, _ = window.measure(Gtk.Orientation.HORIZONTAL, -1)
            seen[width] = minimum
            window.destroy()
        app.quit()

    run_app(gtk_app, body)
    # Every offered width has to be one the rail can actually render.
    for asked, got in seen.items():
        assert got <= asked, f"asked for {asked}px, rail floors at {got}px"


def test_the_rail_icon_fits_the_rail(isolated_store, monkeypatch):
    from context.ui import window as window_module
    from context.state import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)

    settings.update(rail_width=80)
    assert window_module.rail_icon_size() < 80

    settings.update(rail_width=settings.MIN_RAIL_WIDTH)
    assert window_module.rail_icon_size() < settings.MIN_RAIL_WIDTH


def test_collapsing_is_not_undone_by_the_pointer_still_being_there(
    gtk_app, isolated_store, monkeypatch
):
    """The collapse button sits inside the sidebar it collapses.

    With hover-to-expand on, clicking it collapsed the sidebar and the pointer
    — which had not moved — expanded it again a moment later, so the button
    appeared to do nothing at all.
    """
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(collapse_mode="rail", auto_expand=True)
    monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
    seen = {}

    def body(app):
        store = ContextStore()
        store.create("alpha")
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True

        window.set_collapsed(True)
        # The pointer never left, so this is the enter that follows the click.
        window._on_pointer_enter()
        seen["hover_scheduled"] = window._auto_expand_source is not None
        seen["still_collapsed"] = window.collapsed

        # Once the pointer has left and come back, hover works again.
        window._on_pointer_leave()
        window._on_pointer_enter()
        seen["hover_after_leaving"] = window._auto_expand_source is not None
        app.quit()

    run_app(gtk_app, body)
    assert seen["hover_scheduled"] is False
    assert seen["still_collapsed"] is True
    assert seen["hover_after_leaving"] is True


def test_a_pending_hover_expand_is_cancelled_by_collapsing(
    gtk_app, isolated_store, monkeypatch
):
    """A queued expand must not fire after a deliberate collapse."""
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(collapse_mode="rail", auto_expand=True)
    monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
    seen = {}

    def body(app):
        store = ContextStore()
        store.create("alpha")
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window.collapsed = True
        window._on_pointer_enter()
        seen["queued"] = window._auto_expand_source is not None

        window.set_collapsed(True)
        seen["cancelled"] = window._auto_expand_source is None
        app.quit()

    run_app(gtk_app, body)
    assert seen["queued"] is True
    assert seen["cancelled"] is True


@needs_display
def test_clicking_into_the_sidebar_focuses_the_search(
    gtk_app, isolated_store, monkeypatch
):
    """Becoming active has to leave a widget focused, not just the window.

    ON_DEMAND gives the layer the keyboard when it is clicked, but the
    compositor says nothing about which widget should have it, and GTK picks
    none on its own for a layer surface. Typing then went nowhere until the
    sidebar was left and re-entered — a focus change GTK finally acted on.

    Asserted on the focused widget rather than on the keyboard mode, because
    the mode was already right while the bug was live.
    """
    from context.state import settings
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window.collapsed = False
        window.present()
        # No compositor to make the window active, so drive the branch the
        # signal would: a click that landed on no control.
        window._clicked_widget = None
        window._focus_search_if_idle()
        seen["focused"] = window.get_focus()
        seen["entry"] = window.entry
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    # `Gtk.Entry` delegates focus to an internal `GtkText`, so the focused
    # widget is inside the entry rather than the entry itself.
    focused = seen["focused"]
    assert focused is not None
    assert focused is seen["entry"] or focused.get_ancestor(Gtk.Entry) is seen["entry"]


@needs_display
def test_focusing_does_not_steal_from_what_was_clicked(
    gtk_app, isolated_store, monkeypatch
):
    """Clicking a button must keep focus on the button.

    The search box is only a fallback for a click that landed on nothing; a
    sidebar that grabbed focus back unconditionally would undo every click.
    """
    from context.state import settings
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_store / "config"))
    monkeypatch.setattr(settings, "_current", None)
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window.collapsed = False
        window.present()
        window.settings_button.grab_focus()
        # As if the press had landed on the settings button.
        window._clicked_widget = window.settings_button
        window._focus_search_if_idle()
        seen["focused"] = window.get_focus()
        seen["button"] = window.settings_button
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen["focused"] is seen["button"]


def test_new_context_goes_to_the_overview(gtk_app, isolated_store):
    """A blank context opened the editor on an empty layout, which is a screen
    asking which applications this is going to be — and the overview already is
    that. Starting from an app names the context after it, so the trip through
    a blank editor was a naming step that answered itself."""
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    store = ContextStore()
    seen = {"overview": 0}

    def body(app):
        app.open_overview = lambda: seen.update(overview=seen["overview"] + 1)
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.create_row.emit("activated")
        app.quit()

    run_app(gtk_app, body)
    assert seen["overview"] == 1
    # Nothing is created on the way: what the context becomes is chosen there.
    assert store.contexts == []


def test_a_typed_name_still_starts_that_context(gtk_app, isolated_store):
    """The row means two things by what is in the search box, and only the
    blank half moved."""
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    store = ContextStore()
    seen = {"overview": 0, "opened": []}

    def body(app):
        app.open_overview = lambda: seen.update(overview=seen["overview"] + 1)
        window = LauncherWindow(app, store, seen["opened"].append, lambda c: None)
        window.entry.set_text("plan the week")
        window.create_row.emit("activated")
        app.quit()

    run_app(gtk_app, body)
    assert seen["overview"] == 0
    assert [c.title for c in store.contexts] == ["plan the week"]


def test_a_grazed_edge_does_not_snap_the_sidebar_shut(gtk_app, isolated_store, monkeypatch):
    """Auto-collapse waits out a grace period, so the leave/enter pair the
    expand's own resize produces — or a pointer grazing the floating gap —
    does not close the sidebar under the pointer."""
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setattr(sidebar, "available", lambda: True)
    monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
    monkeypatch.setattr(sidebar, "release_focus", lambda _w: None)
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window.collapsed = True
        window._apply_collapsed()
        window._auto_expand()  # hover already fired; sidebar is open

        _instant_collapse(monkeypatch)
        window._on_pointer_leave()
        seen["still_open_during_grace"] = not window.collapsed
        window._on_pointer_enter()  # pointer came straight back
        seen["timer_cancelled"] = window._collapse_source is None

        window._on_pointer_leave()
        window._pointer_inside = False
        seen["collapses_when_gone"] = window._collapse_after_leave() in (
            False,
            0,
        ) and window.collapsed
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen["still_open_during_grace"] is True
    assert seen["timer_cancelled"] is True
    assert seen["collapses_when_gone"] is True


def test_the_hover_zone_holds_the_sidebar_open(
    gtk_app, isolated_store, backend, monkeypatch
):
    """Once hover expands the sidebar, its zone — size plus margins from the
    docked edge — is what keeps it open. The gap between the trigger and the
    surface sends pointer-leave while the cursor never left the zone."""
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setattr(sidebar, "available", lambda: True)
    monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
    monkeypatch.setattr(sidebar, "release_focus", lambda _w: None)
    seen = {}

    def body(app):
        app.backend = backend
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window.collapsed = True
        window._apply_collapsed()
        window._auto_expand()
        _instant_collapse(monkeypatch)
        window._on_pointer_leave()
        window._pointer_inside = False

        backend.cursor = (200, 500)  # inside the zone: 380 + two 8px margins
        window._collapse_after_leave()
        seen["held_open"] = not window.collapsed
        seen["still_watching"] = window._collapse_source is not None
        if window._collapse_source is not None:
            from gi.repository import GLib

            GLib.source_remove(window._collapse_source)
            window._collapse_source = None

        backend.cursor = (900, 500)  # well outside the zone
        window._collapse_after_leave()
        seen["retracted"] = window.collapsed
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen["held_open"] is True
    assert seen["still_watching"] is True
    assert seen["retracted"] is True


def test_searching_finds_apps_as_well_as_contexts(gtk_app, isolated_store, monkeypatch):
    """Parity with the overview: the sidebar can start something new too."""
    from context.ui import window as window_module
    from context.system.apps import App
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    apps = [
        App(id="firefox.desktop", name="Firefox", description="Browser", icon=None),
        App(id="kicad.desktop", name="KiCad", description="EDA", icon=None),
    ]
    monkeypatch.setattr(window_module, "installed_apps", lambda: apps)
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        seen["idle"] = window.apps_listbox.get_visible()
        window.entry.set_text("fire")
        seen["matches"] = [
            r.app_info.name for r in _list_rows(window.apps_listbox)
        ]
        seen["shown"] = window.apps_listbox.get_visible()
        seen["heading"] = window.apps_label.get_label()
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    # Not while idle: unfiltered it is every app installed, which would bury
    # the contexts the sidebar is for.
    assert seen["idle"] is False
    assert seen["matches"] == ["Firefox"]
    assert seen["shown"] is True
    assert seen["heading"] == "Apps · 1"


def test_an_app_from_the_sidebar_becomes_a_context_and_opens(
    gtk_app, isolated_store, monkeypatch
):
    from context.ui import window as window_module
    from context.system.apps import App
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    apps = [App(id="kicad.desktop", name="KiCad", description="EDA", icon=None)]
    monkeypatch.setattr(window_module, "installed_apps", lambda: apps)
    opened = []

    def body(app):
        window = LauncherWindow(app, store, opened.append, lambda c: None)
        window.entry.set_text("kic")
        _list_rows(window.apps_listbox)[0].emit("activated")
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert [c.title for c in opened] == ["KiCad"]
    assert [r.app_id for r in opened[0].resources] == ["kicad.desktop"]
    # Persisted, so it is a real context rather than a one-off launch.
    assert "KiCad" in [c.title for c in ContextStore().contexts]


def _list_rows(listbox) -> list:
    found = []
    row = listbox.get_first_child()
    while row is not None:
        found.append(row)
        row = row.get_next_sibling()
    return found


def test_the_rail_is_inset_from_its_card(gtk_app, isolated_store, monkeypatch):
    """The rail's icons ran into the surface's border.

    Everything in the expanded sidebar is inset from the edge; collapsed, the
    buttons were flush against it.
    """
    from context.ui import window as window_module
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    _mode(monkeypatch, isolated_store, collapse_mode="rail", rail_width=56)
    monkeypatch.setattr(sidebar, "available", lambda: True)
    monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        seen["start"] = window.rail_box.get_margin_start()
        seen["end"] = window.rail_box.get_margin_end()
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert seen["start"] > 0 and seen["end"] > 0
    # And the icon gives that room back, so the rail still renders at the
    # width it was set to.
    assert window_module.rail_icon_size() + 2 * window_module.RAIL_MARGIN < 56


def test_the_sidebar_waits_out_the_collapse_delay(
    gtk_app, isolated_store, backend, monkeypatch
):
    """Leaving the zone starts a clock, it does not close the sidebar.

    Cutting the corner on the way to a window leaves the zone for a moment,
    and retracting on the first frame outside it made the sidebar feel like it
    was running away.
    """
    from context.ui import window as window_module
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setattr(sidebar, "available", lambda: True)
    monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
    monkeypatch.setattr(sidebar, "release_focus", lambda _w: None)
    monkeypatch.setattr(
        settings, "_current", settings.current().replace(collapse_delay_ms=500)
    )
    clock = {"now": 1000.0}
    monkeypatch.setattr(window_module.time, "monotonic", lambda: clock["now"])
    seen = {}

    def body(app):
        app.backend = backend
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window.collapsed = True
        window._apply_collapsed()
        window._auto_expand()
        window._on_pointer_leave()
        window._pointer_inside = False

        backend.cursor = (900, 500)  # outside the zone
        window._collapse_after_leave()
        seen["waiting"] = not window.collapsed
        seen["still_watching"] = window._collapse_source is not None
        _drop_timer(window)

        # Back inside before the delay is up: the clock starts again.
        backend.cursor = (200, 500)
        clock["now"] += 0.3
        window._collapse_after_leave()
        seen["held"] = not window.collapsed
        seen["clock_reset"] = window._left_zone_at is None
        _drop_timer(window)

        backend.cursor = (900, 500)
        window._collapse_after_leave()  # leaves again; delay starts now
        _drop_timer(window)
        clock["now"] += 0.6
        window._collapse_after_leave()
        seen["retracted"] = window.collapsed
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen["waiting"] is True
    assert seen["still_watching"] is True
    assert seen["held"] is True
    assert seen["clock_reset"] is True
    assert seen["retracted"] is True


def _drop_timer(window) -> None:
    from gi.repository import GLib

    if window._collapse_source is not None:
        GLib.source_remove(window._collapse_source)
        window._collapse_source = None


def test_what_the_launcher_reports_goes_to_the_desktop(
    gtk_app, isolated_store, monkeypatch
):
    """A toast over the sidebar is invisible while it is a rail, which is most
    of the time. The desktop's notification daemon is where these belong."""
    from context.system.launcher import CloseResult, LaunchResult
    from context.state.resources import Resource
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        sent = _catch_notifications(monkeypatch)
        ctx = store.create("alpha", resources=[Resource(app_id="a.desktop")])
        window.report_launch(ctx, LaunchResult(backend="fake", launched=["a.desktop"]))
        window.report_close(ctx, CloseResult(was_open=True, closed=2))
        seen["sent"] = [(n["key"], n["title"], n["body"]) for n in sent]
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    keys = [key for key, _title, _body in seen["sent"]]
    assert keys == ["launch", "close"]
    assert all(title == "alpha" for _key, title, _body in seen["sent"])
    assert "Opened 1 app" in seen["sent"][0][2]
    assert "Closed 2 windows" in seen["sent"][1][2]


def test_notifications_can_be_switched_off(gtk_app, isolated_store, monkeypatch):
    from context.state import settings
    from context.system import notify

    monkeypatch.setattr(
        settings, "_current", settings.current().replace(notifications=False)
    )
    assert notify.enabled() is False

    sent = []

    class FakeApp:
        def send_notification(self, key, note):
            sent.append(key)

        def lookup_action(self, _name):
            return None

        def add_action(self, _action):
            return None

    assert notify.send(FakeApp(), "launch", "alpha", "opened") is False
    assert sent == []

    monkeypatch.setattr(
        settings, "_current", settings.current().replace(notifications=True)
    )
    assert notify.send(FakeApp(), "launch", "alpha", "opened") is True
    assert sent == ["launch"]


def test_a_drifted_context_offers_to_be_saved(gtk_app, isolated_store, backend):
    """The prompt is the button's presence: it is there while there is
    something to keep, and gone once there is not."""
    from context.state.layout import Layout, Slot
    from context.state.resources import Resource
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    store = ContextStore()
    ctx = store.create(
        "work",
        resources=[Resource(app_id="a.desktop"), Resource(app_id="b.desktop")],
    )
    ctx.set_handle("fake", "ctx-work")
    ctx.layout = Layout(slots=[Slot(0.0, 0.0, 0.5, 1.0), Slot(0.5, 0.0, 0.5, 1.0)])
    backend.workspaces["ctx-work"] = 2
    seen = {"saved": []}

    def body(app):
        app.backend = backend
        app.save_context = seen["saved"].append
        # Where the windows actually are matches what was saved.
        backend.geometry["ctx-work"] = [
            {"id": "0x1", "app_id": "a.desktop", "x": 0, "y": 0,
             "width": 960, "height": 1080},
            {"id": "0x2", "app_id": "b.desktop", "x": 960, "y": 0,
             "width": 960, "height": 1080},
        ]
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.refresh_open_state()
        seen["settled"] = _list_rows(window.open_listbox)[0].save.get_visible()

        # The split has been dragged: that is drift.
        backend.geometry["ctx-work"] = [
            {"id": "0x1", "app_id": "a.desktop", "x": 0, "y": 0,
             "width": 1400, "height": 1080},
            {"id": "0x2", "app_id": "b.desktop", "x": 1400, "y": 0,
             "width": 520, "height": 1080},
        ]
        window.refresh_open_state()
        row = _list_rows(window.open_listbox)[0]
        seen["drifted"] = row.save.get_visible()
        row.save.emit("clicked")
        app.quit()

    run_app(gtk_app, body)
    assert seen["settled"] is False
    assert seen["drifted"] is True
    assert [c.title for c in seen["saved"]] == ["work"]


def test_windows_in_no_context_are_listed_as_one(gtk_app, isolated_store, backend):
    from context.system.launcher import NO_CONTEXT_ID
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    store = ContextStore()
    seen = {"opened": [], "saved": []}

    def body(app):
        app.backend = backend
        app.save_context = seen["saved"].append
        backend.geometry["stray"] = [
            {"id": "0x9", "app_id": "kicad.desktop", "x": 0, "y": 0,
             "width": 960, "height": 1080}
        ]
        window = LauncherWindow(
            app, store, seen["opened"].append, lambda c: None
        )
        window.refresh_open_state()
        rows = _list_rows(window.open_listbox)
        seen["titles"] = [r.ctx.title for r in rows]
        seen["subtitle"] = rows[0].get_subtitle()
        rows[0].save.emit("clicked")
        rows[0].emit("activated")
        app.quit()

    run_app(gtk_app, body)
    assert seen["titles"] == ["No context"]
    assert seen["subtitle"].startswith("1 window in no context")
    assert [c.id for c in seen["saved"]] == [NO_CONTEXT_ID]
    # Opening it goes to those windows rather than launching anything.
    assert [c.id for c in seen["opened"]] == [NO_CONTEXT_ID]


def test_the_collapse_button_pins_when_the_sidebar_opens_itself(
    gtk_app, isolated_store, monkeypatch
):
    """"Collapse" is the wrong word for a sidebar the pointer is holding open —
    and pressing it while peeking used to close it under the pointer."""
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    _mode(monkeypatch, isolated_store, collapse_mode="rail", auto_expand=True)
    monkeypatch.setattr(sidebar, "available", lambda: True)
    monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        holder = _fake_app_with([window])
        monkeypatch.setattr(window, "get_application", lambda: holder)
        window.set_collapsed(True)
        window._auto_expand()  # the pointer is holding it open

        seen["peeking_icon"] = window.collapse_button.get_icon_name()
        window.collapse_button.emit("clicked")
        seen["pinned_open"] = not window.collapsed
        seen["no_longer_peeking"] = window._auto_expanded is False
        seen["pinned_icon"] = window.collapse_button.get_icon_name()

        window.collapse_button.emit("clicked")
        seen["unpinned"] = window.collapsed
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen["peeking_icon"] == "view-pin-symbolic"
    assert seen["pinned_open"] is True
    assert seen["no_longer_peeking"] is True
    # Pinned, it offers the opposite again.
    assert seen["pinned_icon"] == "sidebar-show-symbolic"
    assert seen["unpinned"] is True


def test_the_sidebar_shows_only_what_is_switched_on(
    gtk_app, isolated_store, monkeypatch
):
    from context.ui import window as window_module
    from context.state import settings
    from context.system.apps import App
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setattr(
        window_module,
        "installed_apps",
        lambda: [App(id="f.desktop", name="Firefox", description="", icon=None)],
    )
    _mode(
        monkeypatch,
        isolated_store,
        show_search=False,
        show_new_context=False,
        show_saved=False,
        show_apps=False,
    )
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.refresh()
        seen["search"] = window.entry.get_visible()
        seen["create"] = window.create_list.get_visible()
        seen["overview"] = bool(rows(window.open_listbox))
        seen["saved"] = window.saved_expander.get_visible()
        window.entry.set_text("fire")
        window.refresh()
        seen["apps"] = window.apps_listbox.get_visible()

        window.entry.set_text("")
        # Each part is its own switch: the search box comes back without the
        # row that used to be tied to it.
        settings.update(show_search=True, show_saved=True)
        window.settings_changed()
        seen["search_again"] = window.entry.get_visible()
        seen["saved_again"] = window.saved_expander.get_visible()
        seen["create_still_off"] = window.create_list.get_visible() is False
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen == {
        "search": False,
        "create": False,
        "overview": False,
        "saved": False,
        "apps": False,
        "search_again": True,
        "saved_again": True,
        "create_still_off": True,
    }


def test_the_overview_appears_when_the_last_context_closes(gtk_app, isolated_store):
    """An empty desktop is what the overview is for — but only on the way to
    empty, not every couple of seconds afterwards."""
    import logging

    from context.app import ContextApplication

    seen = {"opened": 0}

    def body(app):
        holder = ContextApplication.__new__(ContextApplication)
        holder.log = logging.getLogger("test.overview-when-empty")
        holder.switcher = None
        holder._had_open = True
        holder.get_windows = lambda: []
        holder.window = None
        holder.extra_windows = []
        holder.overview = None
        holder.open_overview = lambda: seen.update(opened=seen["opened"] + 1)

        holder.note_open_contexts(2)      # still working
        seen["while_open"] = seen["opened"]
        holder.note_open_contexts(0)      # the last one closed
        seen["on_empty"] = seen["opened"]
        holder.note_open_contexts(0)      # and stays closed
        seen["still_empty"] = seen["opened"]
        holder.note_open_contexts(1)      # something opened again
        holder.note_open_contexts(0)      # and closed again
        seen["second_time"] = seen["opened"]
        app.quit()

    run_app(gtk_app, body)
    assert seen["while_open"] == 0
    assert seen["on_empty"] == 1
    assert seen["still_empty"] == 1
    assert seen["second_time"] == 2


def test_nothing_opens_over_an_editor(gtk_app, isolated_store):
    """Switching the workspace under an editor would be invisible until it
    closed, and would then have moved the user somewhere they did not ask to
    go."""
    import logging

    from context.app import ContextApplication

    seen = {"opened": 0}

    class Visible:
        def get_visible(self):
            return True

    def body(app):
        holder = ContextApplication.__new__(ContextApplication)
        holder.log = logging.getLogger("test.overview-covered")
        holder.switcher = None
        holder._had_open = True
        holder.window = None
        holder.extra_windows = []
        holder.overview = None
        holder.get_windows = lambda: [Visible()]
        holder.open_overview = lambda: seen.update(opened=seen["opened"] + 1)

        holder.note_open_contexts(0)
        app.quit()

    run_app(gtk_app, body)
    assert seen["opened"] == 0


def test_the_overview_itself_does_not_block_going_home(gtk_app, isolated_store):
    """It is what going home shows, so counting it as something in the way
    meant home was never reached again after the first visit."""
    import logging

    from context.app import ContextApplication

    seen = {"opened": 0}

    class Visible:
        def get_visible(self):
            return True

    def body(app):
        holder = ContextApplication.__new__(ContextApplication)
        holder.log = logging.getLogger("test.overview-is-not-in-the-way")
        holder.switcher = None
        holder._had_open = True
        holder.window = None
        holder.extra_windows = []
        holder.overview = Visible()
        holder.get_windows = lambda: [holder.overview]
        holder.open_overview = lambda: seen.update(opened=seen["opened"] + 1)

        holder.note_open_contexts(0)
        app.quit()

    run_app(gtk_app, body)
    assert seen["opened"] == 1


def test_home_is_pinned_before_its_window_exists(gtk_app, isolated_store, backend):
    """The rule has to be in place before the window is built, not after: a
    window mapped before it maps wherever you happened to be, and present()
    is the thing that races. Order, not merely presence."""
    from context.app import ContextApplication, OVERVIEW_TITLE

    seen = {}

    from context.state.scratchpad import NoteStore
    from context.state.store import ContextStore

    def body(app):
        # The real application, carrying the state ensure_overview reads: a
        # window needs a GApplication that has actually started.
        app.log = __import__("logging").getLogger("test.prepare-home")
        app.backend = backend
        app.overview = None
        app.store = ContextStore()
        app.notes = NoteStore()
        for name in (
            "go_to_context", "edit_context", "close_context", "open_app_in_context",
            "add_app_to_context", "edit_note", "restore_context", "leave_home",
            "_on_overview_destroyed",
        ):
            setattr(app, name, lambda *a, **k: None)
        backend.calls.clear()

        window = ContextApplication.ensure_overview(app)
        seen["bound"] = [c for c in backend.calls if c[0] == "bind-home"]
        seen["undecorated"] = [c for c in backend.calls if c[0] == "hide-titlebar"]
        # The rules are in place before the window exists, not after it maps.
        seen["bound_first"] = backend.calls[0][0] == "bind-home"
        # Built once and kept — home cannot be left without its window.
        seen["same"] = ContextApplication.ensure_overview(app) is window
        window.permanent = False
        window.destroy()
        app.quit()

    run_app(gtk_app, body)
    assert seen["bound"] == [
        ("bind-home", gtk_app.get_application_id(), OVERVIEW_TITLE)
    ]
    # A fixture is not a window you manage: no titlebar offering to close it.
    assert seen["undecorated"] == [
        ("hide-titlebar", gtk_app.get_application_id(), OVERVIEW_TITLE)
    ]
    assert seen["bound_first"] is True
    assert seen["same"] is True


def test_the_settings_screen_carries_the_page(gtk_app, isolated_store, monkeypatch):
    """Same page, new home: its back button closes the screen rather than
    popping a stack that is not there."""
    from context.ui.settings_window import SettingsWindow
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    seen = {}

    def body(app):
        window = LauncherWindow(app, ContextStore(), lambda c: None, lambda c: None)
        screen = SettingsWindow(app, window)
        closed = []
        screen.close = lambda: closed.append(True)
        seen["titles"] = _row_titles(screen.page)
        screen.page.back_button.emit("clicked")
        seen["closed"] = closed
        app.quit()

    run_app(gtk_app, body)
    assert "Collapse mode" in seen["titles"]
    assert "Notifications" in seen["titles"]
    assert seen["closed"] == [True]


# -- setting permutations ------------------------------------------------------


def test_the_top_row_is_the_search_box_alone(gtk_app, isolated_store, monkeypatch):
    """It held the search box and an Overview button. The overview moved into
    the list, so the row goes when the search box does."""
    from context.state import settings
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    _mode(monkeypatch, isolated_store, show_search=True)
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        seen["row"] = window.top_row.get_visible()

        settings.update(show_search=False)
        window.settings_changed()
        seen["empty_row"] = window.top_row.get_visible()
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert seen["row"] is True
    assert seen["empty_row"] is False


def test_the_empty_state_never_points_at_a_hidden_control(
    gtk_app, isolated_store, monkeypatch
):
    """"Type a name above" with the search box switched off pointed at
    nothing; "no contexts yet" over hidden saved contexts was a lie."""
    from context.state import settings
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    _mode(monkeypatch, isolated_store, show_search=False, show_new_context=False)
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.refresh()
        seen["no_contexts"] = window.empty_state._description.get_label()

        store.create("parked")
        settings.update(show_saved=False)
        window.settings_changed()
        seen["hidden_saved"] = (
            window.empty_state._title.get_label(),
            window.empty_state._description.get_label(),
        )
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert "type a name" not in seen["no_contexts"].casefold()
    assert "overview" in seen["no_contexts"].casefold()
    title, description = seen["hidden_saved"]
    assert title == "Nothing open"
    assert "hidden" in description


def test_the_rail_hides_saved_contexts_with_the_list(
    gtk_app, isolated_store, monkeypatch
):
    """A rail that kept showing them disagreed with what it expands into."""
    from context.state import settings
    from context.ui import sidebar
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    _mode(monkeypatch, isolated_store, collapse_mode="rail", show_saved=False)
    monkeypatch.setattr(sidebar, "available", lambda: True)
    monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window.collapsed = True
        window.refresh()
        seen["hidden"] = _rail_buttons(window)
        settings.update(show_saved=True)
        window.settings_changed()
        seen["shown"] = _rail_buttons(window)
        app.quit()

    store = ContextStore()
    store.create("alpha")
    store.create("beta")
    run_app(gtk_app, body)
    assert seen["hidden"] == 0
    assert seen["shown"] == 2


def test_a_horizontal_sidebar_gets_a_horizontal_rail(
    gtk_app, isolated_store, monkeypatch
):
    """Docked to the top, the rail is a row along the strip. Built as a column
    regardless, it overflowed a 56px-tall bar after two icons."""
    from context.ui import rail as rail_module
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    _mode(monkeypatch, isolated_store, sidebar_edge="top", collapse_mode="rail")
    seen = {}

    def body(app):
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        seen["rail"] = window.rail_box.get_orientation()
        seen["buttons"] = window.rail.get_orientation()
        seen["expand_icon"] = window.expand_button.get_icon_name()
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert seen["rail"] == Gtk.Orientation.HORIZONTAL
    assert seen["buttons"] == Gtk.Orientation.HORIZONTAL
    # Expansion grows downward from a top bar, and the icon says so.
    # Adwaita has no top/bottom variant, so a horizontal dock takes the left one.
    assert seen["expand_icon"] == "sidebar-show-symbolic"


def test_search_matches_whatever_the_case(gtk_app, isolated_store):
    from context.state.store import ContextStore

    store = ContextStore()
    store.create("Review Todos & Notes")
    assert [c.title for c in store.search("reVIEW")] == ["Review Todos & Notes"]
    assert [c.title for c in store.search("TODOS")] == ["Review Todos & Notes"]
    # casefold, not lower: ß is findable by what a user types for it.
    store.create("Straße planen")
    assert [c.title for c in store.search("strasse")] == ["Straße planen"]


def test_an_app_from_search_can_join_the_current_context(
    gtk_app, isolated_store, monkeypatch
):
    """Where a searched app lands is asked on the row rather than set in
    advance. Joining needs somewhere to join, so that button is only there while
    a context is active — otherwise it would say something untrue."""
    from context.ui import window as window_module
    from context.system.apps import App
    from context.system.launcher import LiveState
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    info = App(id="kicad.desktop", name="KiCad", description="", icon=None)
    monkeypatch.setattr(window_module, "installed_apps", lambda: [info])
    _mode(monkeypatch, isolated_store)
    seen = {"added": [], "opened": []}

    class Holder:
        backend = None

        def add_app_to_active(self, picked):
            seen["added"].append(picked.id)
            return True

    def body(app):
        window = LauncherWindow(app, store, seen["opened"].append, lambda c: None)
        holder = Holder()
        monkeypatch.setattr(window, "get_application", lambda: holder)
        monkeypatch.setattr(window, "_release_keyboard", lambda: None)

        ctx = store.create("work")
        # `current_id`, not `active_id`: the row asks which context an app
        # would join, which on home is the one you came from.
        window._live = LiveState(current_id=ctx.id)
        window.entry.set_text("kic")
        row = _list_rows(window.apps_listbox)[0]
        seen["here_offered"] = row.here.get_visible()
        seen["named"] = row.here.get_tooltip_text()
        row.here.emit("clicked")
        seen["joined"] = list(seen["added"])

        # The other button always makes a new context, whatever is open.
        window.entry.set_text("kic")
        _list_rows(window.apps_listbox)[0].fresh.emit("clicked")
        seen["new"] = [c.title for c in seen["opened"]]

        # Nothing to join: the button is not offered, and activating the row
        # still works because a new context is always available.
        window._live = LiveState()
        window.entry.set_text("kic")
        row = _list_rows(window.apps_listbox)[0]
        seen["here_hidden"] = row.here.get_visible()
        row.emit("activated")
        seen["fallback"] = [c.title for c in seen["opened"]]
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    assert seen["here_offered"] is True
    # By name, not "this context": standing on home, "here" is the overview.
    assert "work" in seen["named"]
    assert seen["joined"] == ["kicad.desktop"]
    assert seen["new"] == ["KiCad"]
    assert seen["here_hidden"] is False
    assert seen["fallback"] == ["KiCad", "KiCad"]


def test_notifications_off_does_not_eat_the_drift_prompt(gtk_app, isolated_store, monkeypatch):
    """Marking a context "asked" before knowing whether the notification went
    out consumed the prompt without it ever appearing."""
    import logging

    from context.state import settings
    from context.system import notify
    from context.app import ContextApplication
    from context.state.store import ContextStore

    seen = {}

    def body(app):
        holder = ContextApplication.__new__(ContextApplication)
        holder.log = logging.getLogger("test.drift-consumed")
        holder.asked_about = set()
        store = ContextStore()
        ctx = store.create("work")

        monkeypatch.setattr(
            settings, "_current", settings.current().replace(notifications=False)
        )
        holder._ask_to_save(ctx)
        seen["consumed_while_off"] = ctx.id in holder.asked_about

        monkeypatch.setattr(
            settings, "_current", settings.current().replace(notifications=True)
        )
        sent = []
        monkeypatch.setattr(
            notify, "send", lambda *a, **k: (sent.append(a[1]), True)[1]
        )
        holder._ask_to_save(ctx)
        seen["asked_when_on"] = (list(sent), ctx.id in holder.asked_about)
        app.quit()

    run_app(gtk_app, body)
    assert seen["consumed_while_off"] is False
    assert seen["asked_when_on"] == (["drift"], True)


def test_the_restart_prompt_survives_notifications_being_off(
    gtk_app, isolated_store, monkeypatch
):
    """It is the only path to the restart the change needs: the setting
    silences reports, and a control is not a report."""
    from context.state import settings
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    monkeypatch.setattr(
        settings, "_current", settings.current().replace(notifications=False)
    )
    seen = {}

    class Holder:
        backend = None

        def send_notification(self, key, note):
            seen["sent"] = key

        def lookup_action(self, _name):
            return None

        def add_action(self, _action):
            return None

    def body(app):
        window = LauncherWindow(app, ContextStore(), lambda c: None, lambda c: None)
        monkeypatch.setattr(window, "get_application", lambda: Holder())
        window.settings_changed(needs_restart=True, changed={"sidebar_edge": "right"})
        app.quit()

    run_app(gtk_app, body)
    assert seen["sent"] == "restart"


def test_the_apps_switch_needs_the_search_box(gtk_app, isolated_store, monkeypatch):
    """App results only ever appear while searching; a live switch with the
    search box off looked like a broken feature rather than a dependency."""
    from context.state import settings
    from context.ui.settings_page import SettingsPage
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    _mode(monkeypatch, isolated_store, show_search=False)
    seen = {}

    def body(app):
        window = LauncherWindow(app, ContextStore(), lambda c: None, lambda c: None)
        page = SettingsPage(window)
        seen["without_search"] = page.apps_switch_row.get_sensitive()
        settings.update(show_search=True)
        page._sync_sidebar_rows()
        seen["with_search"] = page.apps_switch_row.get_sensitive()
        app.quit()

    run_app(gtk_app, body)
    assert seen["without_search"] is False
    assert seen["with_search"] is True


def test_the_sidebar_stands_open_on_home(gtk_app, isolated_store, monkeypatch):
    """Home is the screen you go to to choose what to do next, and the sidebar
    beside it is half of that — hidden to a sliver it is the missing half. So
    it is not a default a setting turns off: it holds while you are there,
    whatever the collapse mode says, and the button that would shrink it is not
    offered. Leaving goes back to what was chosen, which is never written."""
    from context.state import uistate
    from context.ui import sidebar
    from context.system.launcher import LiveState
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    _mode(monkeypatch, isolated_store, collapse_mode="hidden", auto_expand=False)
    uistate.save(collapsed=True)
    seen = {}

    def body(app):
        monkeypatch.setattr(sidebar, "available", lambda: True)
        monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window._live = LiveState()
        window.refresh()
        seen["away"] = window.collapsed
        seen["button_away"] = window.collapse_button.get_visible()

        window._live = LiveState(at_home=True)
        window.refresh()
        seen["home"] = window.collapsed
        seen["button_home"] = window.collapse_button.get_visible()

        # The keybind reaches it even with the button gone; it must not shrink.
        window.toggle_collapsed()
        seen["after_toggle"] = window.collapsed
        # Nothing written: leaving has to find what was chosen, not what home
        # needed.
        seen["stored"] = uistate.get("collapsed")

        window._live = LiveState()
        window.refresh()
        seen["back"] = window.collapsed
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen["away"] is True
    assert seen["button_away"] is True
    assert seen["home"] is False
    assert seen["button_home"] is False
    assert seen["after_toggle"] is False
    assert seen["stored"] is True
    assert seen["back"] is True


def test_home_shows_every_part_of_the_sidebar(gtk_app, isolated_store, monkeypatch):
    """A session that keeps the search box and the saved group switched off for
    the narrow column the sidebar usually is would arrive on home with the
    tools missing. The parts are on there whatever the settings say."""
    from context.ui import sidebar
    from context.system.launcher import LiveState
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    _mode(
        monkeypatch,
        isolated_store,
        collapse_mode="none",
        show_search=False,
        show_new_context=False,
        show_saved=False,
    )
    seen = {}

    def body(app):
        monkeypatch.setattr(sidebar, "available", lambda: True)
        monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True

        window._live = LiveState()
        window.refresh()
        seen["away"] = (
            window.entry.get_visible(),
            window.create_list.get_visible(),
            window.saved_expander.get_visible(),
        )

        window._live = LiveState(at_home=True)
        window.refresh()
        seen["home"] = (
            window.entry.get_visible(),
            window.create_list.get_visible(),
            window.saved_expander.get_visible(),
        )
        app.quit()

    store = ContextStore()
    store.create("alpha")
    run_app(gtk_app, body)
    assert seen["away"] == (False, False, False)
    assert seen["home"] == (True, True, True)


def test_the_scratchpad_master_switch_still_holds_on_home(
    gtk_app, isolated_store, monkeypatch
):
    """`show_notes` is where the scratchpad appears, which is a sidebar part.
    `scratchpad` is whether the feature exists at all — turning a feature on
    because of where you are standing is a different thing entirely."""
    from context.ui import sidebar
    from context.system.launcher import LiveState
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    _mode(
        monkeypatch, isolated_store,
        collapse_mode="none", scratchpad=False, show_notes=False,
    )
    seen = {}

    def body(app):
        monkeypatch.setattr(sidebar, "available", lambda: True)
        monkeypatch.setattr(sidebar, "resize", lambda w, width, edge=None: None)
        window = LauncherWindow(app, store, lambda c: None, lambda c: None)
        window.is_sidebar = True
        window._live = LiveState(at_home=True)
        window.refresh()
        seen["shown"] = window.scratchpad_box.get_visible()
        seen["forced"] = window._sections().show_notes
        app.quit()

    store = ContextStore()
    run_app(gtk_app, body)
    # The placement is forced on; the feature being off still wins.
    assert seen["forced"] is True
    assert seen["shown"] is False
