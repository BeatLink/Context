"""Launcher window: a text bar to start a new context, and a list of previous ones."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from . import notify, settings, sidebar, theme, uistate, widgets
from .apps import App, installed_apps, search_apps
from .editor_window import EditorWindow
from .launcher import hand_keyboard_back, open_state
from .layout import Layout
from .logging_setup import get_logger
from .resources import Resource
from .rows import AppRow, ContextRow, context_for_app, relative_time
from .store import Context, ContextStore

log = get_logger("window")

# How far the rail's contents sit from the surface's edges. Without it the
# buttons ran into the card's border, which the expanded sidebar never does —
# everything in it is inset from the edge.
RAIL_MARGIN = 4
# The icon fills the rail minus its button's padding and border, and minus the
# margins either side. Derived rather than fixed: a 32px icon in a 32px rail
# cannot fit, so the rail silently came out wider than it was set to.
RAIL_ICON_PADDING = 16
MIN_RAIL_ICON = 12

# How many application results the sidebar's list shows. The full set is
# hundreds of rows rebuilt on every keystroke, and the point of the sidebar's
# search is the first few hits; the heading says when there are more.
APP_RESULTS = 8

# How often the cursor is asked for while the sidebar waits to retract. It has
# left the surface by then, so there are no motion events to go on.
ZONE_POLL_MS = 200


def rail_icon_size() -> int:
    room = sidebar.rail_width() - RAIL_ICON_PADDING - 2 * RAIL_MARGIN
    return max(MIN_RAIL_ICON, room)


class LauncherWindow(Gtk.ApplicationWindow):
    def __init__(
        self,
        app: Gtk.Application,
        store: ContextStore,
        on_open,
        on_close=None,
        monitor: str | None = None,
    ) -> None:
        super().__init__(application=app, title="Context")
        # Which screen this launcher docks to. None means the setting decides,
        # which is what a single launcher uses.
        self.monitor = monitor
        self.store = store
        self.on_open = on_open
        self.on_close = on_close
        self._open_ids: set[str] = set()
        self._active_id: str | None = None
        self._open_signature: tuple | None = None
        # Installed applications, read on the first search rather than at start.
        self._apps: list[App] | None = None
        self._auto_expanded = False
        self._auto_expand_source: int | None = None
        # A pending collapse, waiting out the grace period after a leave.
        self._collapse_source: int | None = None
        # When the cursor left the hover zone, for the collapse delay to
        # measure from. Cleared whenever it comes back.
        self._left_zone_at: float | None = None
        # Set when collapsing deliberately, cleared when the pointer leaves.
        self._suppress_hover = False
        self._pointer_inside = False
        # A pending "release once the popover closes" check.
        self._popover_watch: int | None = None

        self.set_default_size(560, 620)
        # Context paints its own surface rather than inheriting whatever theme
        # the desktop happens to have. Without this the window background comes
        # from the system theme while the cards come from ours, and a light
        # desktop gets light chrome around dark rows.
        self.add_css_class("ctx-window")
        # The rail's buttons are styled by the theme, so the stylesheet has to
        # be on the display before one is built.
        theme.install()
        # Docks the window to a screen edge where the compositor supports it.
        self.is_sidebar = sidebar.apply(self, monitor=self.monitor)

        self.nav = widgets.NavigationView()
        # The rounded, bordered card every Context surface wears; clipped so
        # square children cannot poke out of the corners.
        self.nav.add_css_class("ctx-surface")
        self.nav.set_overflow(Gtk.Overflow.HIDDEN)

        self.toolbar = widgets.ToolbarView()
        self.header = widgets.HeaderBar(title="Context")
        self.header.add_css_class("flat")
        if self.is_sidebar:
            # Nothing to minimise or close when docked.
            self.header.set_show_start_title_buttons(False)
            self.header.set_show_end_title_buttons(False)

        self.new_button = Gtk.Button(icon_name="list-add-symbolic")
        self.new_button.add_css_class("flat")
        self.new_button.set_tooltip_text("New context")
        self.new_button.connect("clicked", lambda _b: self._new_context())
        self.header.pack_start(self.new_button)

        self.settings_button = Gtk.Button(icon_name="preferences-system-symbolic")
        self.settings_button.add_css_class("flat")
        self.settings_button.set_tooltip_text("Settings")
        self.settings_button.connect("clicked", lambda _b: self.open_settings())
        self.header.pack_start(self.settings_button)

        # Collapsing is only meaningful for a docked sidebar; as an ordinary
        # window there is no reserved space to give back.
        self.collapse_button: Gtk.Button | None = None
        if self.is_sidebar:
            self.collapse_button = Gtk.Button(icon_name="go-previous-symbolic")
            self.collapse_button.add_css_class("flat")
            self.collapse_button.set_tooltip_text("Collapse to a rail")
            self.collapse_button.connect("clicked", lambda _b: self.toggle_collapsed())
            self.collapse_button.set_visible(self.collapses)
            self.header.pack_end(self.collapse_button)
        self.toolbar.add_top_bar(self.header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(12)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        self.entry = Gtk.Entry(
            placeholder_text="Search or create a context",
            activates_default=False,
        )
        self.entry.set_hexpand(True)
        self.entry.connect("changed", self._on_entry_changed)
        self.entry.connect("activate", self._on_entry_activate)

        # No Start button: Enter is the trigger, and the row below is the
        # clickable path — a button that mirrored the row said the same thing
        # twice in half the sidebar's width.
        content.append(self.entry)

        # A built-in way to start something new, always in the list. With a
        # name typed it starts that; blank, it opens the editor to be named.
        self.create_row = widgets.ActionRow(title="New context")
        self.create_row.set_activatable(True)
        self.create_row.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        self.create_row.connect("activated", lambda _r: self._create_from_entry())
        self.create_row.set_subtitle("Name it in the editor")
        self.create_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.create_list.add_css_class("boxed-list")
        self.create_list.append(self.create_row)
        content.append(self.create_list)

        self.list_label = Gtk.Label(xalign=0.0)
        self.list_label.add_css_class("heading")
        self.list_label.add_css_class("dim-label")

        # Open and saved contexts are different things and want different
        # actions, so they get their own groups rather than one mixed list.
        self.open_label = Gtk.Label(xalign=0.0)
        self.open_label.add_css_class("heading")
        self.open_label.add_css_class("dim-label")

        self.open_listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.open_listbox.add_css_class("boxed-list")

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")

        # Saved contexts live behind an expander. With nothing open they are the
        # only thing to show, so it starts expanded; once a context is running
        # the open list is what matters and this collapses out of the way,
        # still one click from being reopened.
        self.saved_expander = Gtk.Expander()
        self.saved_expander.set_label_widget(self.list_label)
        self.saved_expander.set_child(self.listbox)
        self.saved_expander.connect("notify::expanded", self._on_saved_toggled)
        # Remembers a deliberate expansion, so a refresh does not undo it.
        # None until the user says either way, so the group can decide for
        # itself; once they have chosen, the choice holds in both modes.
        self._saved_pinned_open: bool | None = None

        # Applications, so the sidebar can start something that has no context
        # yet — the same reach the overview has, in the shape a narrow column
        # allows. Only ever while searching: unfiltered it is every app
        # installed, which would bury the contexts the sidebar is for.
        self.apps_label = Gtk.Label(xalign=0.0)
        self.apps_label.add_css_class("heading")
        self.apps_label.add_css_class("dim-label")

        self.apps_listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.apps_listbox.add_css_class("boxed-list")

        groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        groups.append(self.open_label)
        groups.append(self.open_listbox)
        groups.append(self.saved_expander)
        groups.append(self.apps_label)
        groups.append(self.apps_listbox)

        self.empty_state = widgets.StatusPage(
            icon_name="view-grid-symbolic",
            title="No contexts yet",
            description="Type a name above to create your first one.",
        )
        self.empty_state.set_vexpand(True)

        self.stack = Gtk.Stack()
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(groups)
        self.stack.add_named(scroller, "list")
        self.stack.add_named(self.empty_state, "empty")
        content.append(self.stack)

        # Collapsed, the sidebar is a strip of icons — one per context, the way
        # the bar shows windows. The full launcher is swapped out rather than
        # squeezed, because search and titles have nowhere to go at rail width.
        self.rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.rail.set_margin_top(8)
        self.rail.set_margin_bottom(8)

        self.expand_button = Gtk.Button(icon_name="go-next-symbolic")
        self.expand_button.add_css_class("flat")
        # Or Adwaita's default button width becomes the rail's floor, and a
        # narrow rail comes out wider than it was set to.
        self.expand_button.add_css_class("ctx-rail-toggle")
        self.expand_button.set_tooltip_text("Expand the launcher")
        self.expand_button.connect("clicked", lambda _b: self.toggle_collapsed())

        rail_scroller = Gtk.ScrolledWindow(vexpand=True)
        rail_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rail_scroller.set_child(self.rail)

        rail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        # Inset from the card the same way everything in the expanded sidebar
        # is; `rail_icon_size` gives the margins back out of the icon so the
        # rail still renders at the width it was set to.
        for setter in (
            "set_margin_top",
            "set_margin_bottom",
            "set_margin_start",
            "set_margin_end",
        ):
            getattr(rail_box, setter)(RAIL_MARGIN)
        rail_box.append(self.expand_button)
        rail_box.append(rail_scroller)
        self.rail_box = rail_box

        self.mode_stack = Gtk.Stack()
        # A homogeneous stack requests the largest child's size, so the full
        # launcher would hold the window at 380px however narrow the rail is.
        self.mode_stack.set_hhomogeneous(False)
        self.mode_stack.set_vhomogeneous(False)
        self.mode_stack.add_named(content, "full")
        self.mode_stack.add_named(rail_box, "rail")
        # Hidden: an empty page behind the hover sliver.
        self.mode_stack.add_named(Gtk.Box(), "hidden")
        self.toolbar.set_content(self.mode_stack)

        self.home_page = widgets.NavigationPage(
            child=self.toolbar, title="Context", tag="home"
        )
        self.nav.add(self.home_page)
        self.set_child(self.nav)

        escape = Gtk.ShortcutController()
        escape.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.CallbackAction.new(lambda *_a: self._on_escape()),
            )
        )
        self.add_controller(escape)

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key_pressed)
        self.entry.add_controller(key)

        # Hover is only ever about expanding a collapsed sidebar. Keyboard
        # focus is the compositor's job — the layer is ON_DEMAND, so clicking
        # in gives it the keyboard and clicking away takes it back, exactly as
        # for an ordinary window.
        #
        # Driving focus from the pointer instead made anything with a popover
        # unusable: a dropdown opening sends the sidebar a pointer-leave, the
        # keyboard was dropped, and the popover dismissed itself a frame later.
        pointer = Gtk.EventControllerMotion()
        pointer.connect("enter", lambda *_a: self._on_pointer_enter())
        pointer.connect("leave", lambda *_a: self._on_pointer_leave())
        self.add_controller(pointer)

        # The compositor hands the layer the keyboard when it is clicked, but
        # says nothing about which widget inside should have it — and GTK does
        # not choose one on its own for a layer surface. Nothing useful was
        # focused, so typing went nowhere until the sidebar was left and
        # re-entered, which finally produced a focus change GTK acted on.
        self.connect("notify::is-active", self._on_active_changed)

        # What the click landed on, so becoming active can tell "clicked a
        # button" from "clicked the empty part of the sidebar". Captured on the
        # way down, before the target widget handles the press.
        self._clicked_widget = None
        press = Gtk.GestureClick()
        press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", self._on_press_anywhere)
        self.add_controller(press)

        self._read_open_state()
        # A stored collapsed state is ignored when collapsing is switched off,
        # rather than leaving the sidebar shrunk with no button to grow it.
        self.collapsed = bool(
            self.is_sidebar and self.collapses and uistate.get("collapsed", False)
        )
        self._apply_collapsed()
        self.refresh()

        # Which contexts are open changes outside this window — a context is
        # launched, its last window closes, you switch workspaces by keyboard.
        # Nothing notifies the launcher, so the open list is re-checked on a
        # timer; without it the list only updated when the user acted here.
        self._poll_source: int | None = None
        self._restart_poll()

    def _read_open_state(self) -> bool:
        """Ask the backend what is open. Returns whether anything changed."""
        if self.on_close is None:
            self._open_ids, self._active_id = set(), None
            return False
        try:
            open_ids, active_id = open_state(self.store.contexts)
        except OSError:
            return False

        signature = (frozenset(open_ids), active_id)
        if signature == self._open_signature:
            return False
        self._open_signature = signature
        self._open_ids, self._active_id = open_ids, active_id
        return True

    def _poll_open_state(self) -> bool:
        """Re-read which contexts are open, refreshing only when it changed."""
        if self._read_open_state():
            log.debug("open contexts changed: %d open", len(self._open_ids))
            self.refresh()
        # The "whenever it changes" save prompt rides along here rather than
        # running a timer of its own: it needs the same compositor query, and
        # `offer_to_save` does nothing unless that is the moment chosen.
        app = self.get_application()
        if app is not None and hasattr(app, "offer_to_save"):
            app.offer_to_save("change")
        return True

    def open_settings(self) -> None:
        from .settings_page import SettingsPage

        if self.nav.find_page("settings") is None:
            self.nav.push(SettingsPage(self))
        else:
            self.nav.pop_to_tag("settings")

    def settings_changed(self, needs_restart: bool = False, changed=None) -> None:
        """Honour what can be applied now; say so for what cannot."""
        # Switching collapsing off while collapsed would leave the sidebar
        # shrunk with no way to grow it, since the button has just gone.
        if self.collapsed and not self.collapses:
            # Through the application, so every launcher expands and the stored
            # state is written once rather than once per window.
            app = self.get_application()
            if app is not None and hasattr(app, "set_collapsed"):
                app.set_collapsed(False)
            else:
                self.collapsed = False
                uistate.save(collapsed=False)
        if self.collapse_button is not None:
            self.collapse_button.set_visible(self.collapses)
        self._apply_collapsed()
        self._restart_poll()
        if needs_restart:
            names = ", ".join(sorted(changed or {}))
            # The notification carries the restart rather than only mentioning
            # it, since the setting is otherwise stuck until Context is found
            # and relaunched by hand.
            self.notify(
                "restart",
                "Restart to apply",
                f"{names} applies when Context restarts",
                button="Restart",
                on_click=self._restart_app,
            )

    def _restart_app(self) -> None:
        app = self.get_application()
        if app is not None:
            app.restart()

    def notify(self, key: str, title: str, body: str = "", **extra) -> bool:
        """Say something to the desktop rather than to the sidebar.

        What the launcher reports — a context opened, a close that found
        nothing, a setting needing a restart — used to be a toast over its own
        list, which nobody sees while it is collapsed to a rail or hidden.
        """
        return notify.send(self.get_application(), key, title, body, **extra)

    def _restart_poll(self) -> None:
        if self._poll_source is not None:
            GLib.source_remove(self._poll_source)
        self._poll_source = GLib.timeout_add_seconds(
            settings.current().poll_seconds, self._poll_open_state
        )

    def _on_pointer_enter(self) -> None:
        self._pointer_inside = True
        if self._collapse_source is not None:
            GLib.source_remove(self._collapse_source)
            self._collapse_source = None
        if self._suppress_hover or not (self.collapsed and self.collapses):
            return
        # Hiding always reveals on hover, whatever the setting says: with
        # nothing on screen but a two-pixel sliver, hover is the only way back
        # short of a keybind, and a setting that can strand the launcher is a
        # trap rather than a preference.
        if not (settings.current().auto_expand or self.hides_when_collapsed):
            return
        # Peek, without changing what the sidebar goes back to on leave.
        delay = settings.current().auto_expand_delay_ms
        self._auto_expand_source = GLib.timeout_add(delay, self._auto_expand)

    def _auto_expand(self) -> bool:
        self._auto_expand_source = None
        if not self.collapsed:
            return False
        self._auto_expanded = True
        self.collapsed = False
        self._apply_collapsed()
        self.refresh()
        return False

    def _on_pointer_leave(self) -> None:
        self._pointer_inside = False
        # Leaving is what makes a later hover meaningful again.
        self._suppress_hover = False
        self._maybe_release_keyboard()
        if self._auto_expand_source is not None:
            GLib.source_remove(self._auto_expand_source)
            self._auto_expand_source = None
        if not self._auto_expanded:
            return
        # Not collapsed on the spot: triggering the expansion established a
        # zone — the sidebar's column plus its floating margins — and the
        # sidebar retracts only once the cursor leaves it. The gaps around
        # the surface send pointer-leave while the cursor is, visibly, still
        # at the sidebar, so leaving the *surface* proves nothing.
        self._left_zone_at = None
        if self._collapse_source is None:
            self._collapse_source = GLib.timeout_add(
                ZONE_POLL_MS, self._collapse_after_leave
            )

    def _collapse_after_leave(self) -> bool:
        self._collapse_source = None
        if self._pointer_inside or not self._auto_expanded:
            return GLib.SOURCE_REMOVE
        if self._inside_hover_zone():
            # Back inside: the delay starts again from the next departure.
            self._left_zone_at = None
            return self._keep_watching()
        # Outside, but not necessarily gone: cutting the corner on the way to
        # a window, or rounding a menu, leaves the zone for a moment. The
        # delay is what tells that apart from actually walking away.
        now = time.monotonic()
        if self._left_zone_at is None:
            self._left_zone_at = now
        delay = settings.current().collapse_delay_ms / 1000
        if now - self._left_zone_at < delay:
            return self._keep_watching()
        self._left_zone_at = None
        self._auto_expanded = False
        self.collapsed = True
        self._apply_collapsed()
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _keep_watching(self) -> bool:
        self._collapse_source = GLib.timeout_add(
            ZONE_POLL_MS, self._collapse_after_leave
        )
        return GLib.SOURCE_REMOVE

    def _inside_hover_zone(self) -> bool:
        """Whether the cursor is still inside the expanded sidebar's zone.

        The zone is the sidebar's size plus its margins, measured from the
        docked edge. The window cannot see the cursor once it leaves the
        surface — the compositor can, so it is asked.
        """
        backend = getattr(self.get_application(), "backend", None)
        if backend is None:
            return False
        position = backend.cursor_position()
        if position is None:
            return False
        monitor = self._own_monitor(backend)
        if monitor is None:
            return False
        x, y = position
        if not (
            monitor.x <= x < monitor.x + monitor.width
            and monitor.y <= y < monitor.y + monitor.height
        ):
            return False
        band = sidebar.configured_width() + 2 * sidebar.GAP
        edge = sidebar.configured_edge()
        if edge == "left":
            return x - monitor.x <= band
        if edge == "right":
            return (monitor.x + monitor.width) - x <= band
        if edge == "top":
            return y - monitor.y <= band
        return (monitor.y + monitor.height) - y <= band

    def _own_monitor(self, backend):
        found = backend.monitors()
        if not found:
            return None
        if self.monitor:
            for monitor in found:
                if monitor.name == self.monitor:
                    return monitor
        return next((m for m in found if m.focused), found[0])

    def toggle_collapsed(self) -> None:
        if not self.collapses:
            log.info("collapsing is switched off")
            return
        wanted = not self.collapsed
        # Whether the launcher is collapsed is one thing, not one per screen:
        # the state is stored once, so letting each window decide separately
        # meant the two disagreed and whichever restarted last won. The
        # application applies it to all of them.
        app = self.get_application()
        if app is not None and hasattr(app, "set_collapsed"):
            app.set_collapsed(wanted)
        else:
            self.set_collapsed(wanted)

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapse or expand this launcher, without touching the others."""
        # A deliberate toggle ends any hover peek, so the state that gets saved
        # is the one the user chose rather than the one hovering produced.
        self._auto_expanded = False
        if self._auto_expand_source is not None:
            GLib.source_remove(self._auto_expand_source)
            self._auto_expand_source = None
        if self._collapse_source is not None:
            GLib.source_remove(self._collapse_source)
            self._collapse_source = None
        self._left_zone_at = None
        # Collapsing happens with the pointer on the button, which is inside
        # the sidebar — so hover would expand it again a moment later and the
        # button would appear to do nothing. Hovering is suppressed until the
        # pointer has actually left.
        self._suppress_hover = collapsed
        self.collapsed = collapsed
        log.info("sidebar %s", "collapsed" if collapsed else "expanded")
        self._apply_collapsed()
        self.refresh()

    @property
    def collapses(self) -> bool:
        """Whether collapsing is offered at all."""
        return settings.current().collapse_mode != "none"

    @property
    def hides_when_collapsed(self) -> bool:
        """Whether collapsing unpins the sidebar rather than shrinking it.

        Only hiding does. A rail and never-collapse both stay pinned to the
        edge, reserving space and keeping something on screen — they differ
        only in how much.
        """
        return settings.current().collapse_mode == "hidden"

    def _apply_collapsed(self) -> None:
        """Swap the content and give the reserved space back."""
        self.mode_stack.set_visible_child_name("rail" if self.collapsed else "full")
        # The header carries the title and collapse button, neither of which
        # fits at rail width; the rail has an expand button of its own.
        self.header.set_visible(not self.collapsed)
        if not self.is_sidebar:
            return

        if self.collapsed and self.hides_when_collapsed:
            self.mode_stack.set_visible_child_name("hidden")
            sidebar.resize(self, sidebar.HIDDEN_WIDTH)
            log.debug("sidebar hidden at %dpx", sidebar.HIDDEN_WIDTH)
            return

        width = sidebar.rail_width() if self.collapsed else sidebar.configured_width()
        sidebar.resize(self, width)
        log.debug(
            "sidebar %s at %dpx", "collapsed" if self.collapsed else "expanded", width
        )

    def _build_rail(self, opened, saved, shown: bool = True) -> None:
        """Open contexts, a divider, then saved ones if the group is showing.

        The same two groups the expanded list shows, folding the same way. There
        is no room for their headings, so the split is drawn the way a browser
        separates pinned tabs from the rest when its tab strip is collapsed: a
        rule, and a control to fold the group away.
        """
        child = self.rail.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.rail.remove(child)
            child = following

        for ctx in opened:
            self.rail.append(self._rail_button(ctx, is_open=True))

        if not saved:
            return

        if opened:
            divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            divider.add_css_class("ctx-rail-divider")
            self.rail.append(divider)

        # Only when the group could actually fold. With nothing open the saved
        # list is the whole rail, and a control that empties it is a trap.
        if opened:
            self.rail.append(self._saved_toggle(len(saved), shown))

        if shown:
            for ctx in saved:
                self.rail.append(self._rail_button(ctx, is_open=False))

    def _saved_toggle(self, count: int, shown: bool) -> Gtk.Button:
        button = Gtk.Button(
            halign=Gtk.Align.CENTER,
            icon_name="go-up-symbolic" if shown else "go-down-symbolic",
        )
        button.add_css_class("flat")
        button.add_css_class("ctx-rail-toggle")
        button.set_tooltip_text(
            "Hide saved contexts"
            if shown
            else f"Show {count} saved context{'s' if count != 1 else ''}"
        )
        button.connect("clicked", lambda _b: self._toggle_saved(not shown))
        return button

    def _rail_button(self, ctx: Context, is_open: bool) -> Gtk.Button:
        button = Gtk.Button(halign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.add_css_class("ctx-rail-button")
        button.set_child(self._rail_icon(ctx))

        is_active = self._active_id is not None and ctx.id == self._active_id
        if is_active:
            button.add_css_class("ctx-active")
            state = "here now"
        elif is_open:
            button.add_css_class("ctx-open")
            state = "open"
        else:
            button.add_css_class("ctx-saved")
            state = "saved"

        button.set_tooltip_text(f"{ctx.title} · {state}")
        button.connect("clicked", lambda _b, c=ctx: self._open(c))
        return button

    def _rail_icon(self, ctx: Context) -> Gtk.Image:
        """The first app's icon, so a context is recognisable without its name."""
        image = None
        for resource in ctx.resources:
            try:
                info = Gio.DesktopAppInfo.new(resource.app_id)
            except TypeError:
                info = None
            icon = info.get_icon() if info is not None else None
            if icon is not None:
                image = Gtk.Image.new_from_gicon(icon)
                break
        if image is None:
            image = Gtk.Image.new_from_icon_name("view-grid-symbolic")
        # The icon is the only thing identifying a context on the rail, so it
        # gets the room the label would otherwise have taken.
        image.set_pixel_size(rail_icon_size())
        return image

    def refresh_open_state(self) -> None:
        """Re-read the open list now, rather than waiting for the next poll.

        Called when a launch finishes, so the context moves from Saved to Open
        as soon as its windows are up instead of up to a poll interval later.
        """
        self._read_open_state()
        self.refresh()

    def _visible_rows(self) -> list[ContextRow]:
        rows = []
        row = self.listbox.get_first_child()
        while row is not None:
            if isinstance(row, ContextRow):
                rows.append(row)
            row = row.get_next_sibling()
        return rows

    def refresh(self) -> None:
        """Open contexts on top, saved ones in an expander beneath them.

        With nothing running the saved list is all there is, so it shows
        expanded. Once a context is open it collapses — the open list is what
        you want then — but stays one click away rather than hidden behind a
        search.
        """
        query = self.entry.get_text().strip()
        searching = bool(query)
        matches = self.store.search(query)

        active = self._active_context()
        opened = [c for c in matches if self._is_open(c)]
        saved = [c for c in matches if c not in opened]

        # The rail shows every context, never a search result: there is no
        # search bar at rail width to explain why some are missing.
        if self.collapsed:
            all_contexts = self.store.contexts
            rail_open = [c for c in all_contexts if self._is_open(c)]
            rail_saved = [c for c in all_contexts if not self._is_open(c)]
            self._build_rail(
                rail_open,
                rail_saved,
                # Searching is not a thing at rail width, so it plays no part.
                shown=self._saved_group_shown(rail_open, rail_saved, searching=False),
            )
            return

        self.open_listbox.remove_all()
        for ctx in opened:
            self.open_listbox.append(
                self._context_row(
                    ctx,
                    is_open=True,
                    is_active=active is not None and ctx.id == active.id,
                )
            )

        self.listbox.remove_all()
        for ctx in saved:
            self.listbox.append(self._context_row(ctx, is_open=False))

        app_matches = self._app_matches(query)
        self.apps_listbox.remove_all()
        for info in app_matches[:APP_RESULTS]:
            self.apps_listbox.append(AppRow(info, self._open_app))

        shown = min(len(app_matches), APP_RESULTS)
        self.apps_label.set_label(
            f"Apps · {shown} of {len(app_matches)}"
            if len(app_matches) > shown
            else f"Apps · {shown}"
        )
        self.apps_label.set_visible(bool(app_matches))
        self.apps_listbox.set_visible(bool(app_matches))

        self.open_label.set_visible(bool(opened))
        self.open_listbox.set_visible(bool(opened))
        self.open_label.set_label("Open")

        self.saved_expander.set_visible(bool(saved))
        self.list_label.set_visible(bool(saved))
        self.list_label.set_label(f"Saved · {len(saved)}")

        should_expand = self._saved_group_shown(opened, saved, searching)
        if self.saved_expander.get_expanded() != should_expand:
            self._suppress_toggle = True
            self.saved_expander.set_expanded(should_expand)
            self._suppress_toggle = False

        if opened or saved or app_matches:
            self.stack.set_visible_child_name("list")
            return

        self.stack.set_visible_child_name("empty")
        if searching:
            self.empty_state.set_title("No matches")
            self.empty_state.set_description(
                "Press Enter to start a new context with this name."
            )
        else:
            self.empty_state.set_title("No contexts yet")
            self.empty_state.set_description("Type a name above to create your first one.")

    def _context_row(self, ctx: Context, is_open: bool, is_active: bool = False):
        return ContextRow(
            ctx,
            self._open,
            self._edit,
            self._close,
            is_open=is_open,
            is_active=is_active,
            on_forget=self._delete,
            on_add_app=self._add_app_to_context,
        )

    def _add_app_to_context(self, ctx: Context) -> None:
        """Pick an app to join this context, from the row's menu."""
        app = self.get_application()
        if app is None or not hasattr(app, "open_app_in_context"):
            return
        self._release_keyboard()
        app.open_app_in_context(ctx)

    def edit_context(self, ctx: Context, is_new: bool = False) -> None:
        """Open the editor for a context, wherever it was asked for.

        The overview and the switcher have no editor of their own — they close
        and hand the context here, since the editor is an overlay and two of
        those stacked leaves one covering the other.
        """
        if is_new:
            self._pick_apps(ctx)
        else:
            self._edit(ctx)

    def _app_matches(self, query: str) -> list[App]:
        """Installed apps matching the search, or nothing when not searching.

        The list is read the first time it is needed rather than at startup:
        scanning every desktop entry costs a noticeable slice of the launcher's
        start, and most sessions never search for an app at all.
        """
        if not query:
            return []
        if self._apps is None:
            self._apps = installed_apps()
            log.debug("read %d installed apps", len(self._apps))
        return search_apps(self._apps, query)

    def _open_app(self, info: App) -> None:
        """Start a context around one app, the overview's fast path in a list."""
        log.info("new context around %s", info.id)
        self.entry.set_text("")
        self._open(context_for_app(self.store, info))

    def _saved_group_shown(self, opened, saved, searching: bool) -> bool:
        """Whether the saved group is on show.

        The rail asks this too, so collapsing the sidebar never changes which
        contexts are listed — only how much room they take.

        With nothing pinned either way it answers itself: the saved list is all
        there is when nothing is running, and gets out of the way once something
        is. A search always shows it, since results are the point of searching.
        """
        if not saved:
            return False
        if searching:
            return True
        if self._saved_pinned_open is not None:
            return self._saved_pinned_open
        return not opened

    def _on_saved_toggled(self, expander, _param) -> None:
        """Remember a deliberate expand, so the next refresh does not undo it."""
        if getattr(self, "_suppress_toggle", False):
            return
        self._saved_pinned_open = expander.get_expanded()

    def _toggle_saved(self, show: bool) -> None:
        """The rail's equivalent of clicking the expander.

        Takes the wanted state rather than inverting the expander's: refresh
        leaves early in rail mode, so the expander holds whatever it was last
        set to while expanded, which is not necessarily what the rail shows.
        """
        self._saved_pinned_open = show
        self._suppress_toggle = True
        self.saved_expander.set_expanded(show)
        self._suppress_toggle = False
        self.refresh()

    def _on_entry_changed(self, entry: Gtk.Entry) -> None:
        text = entry.get_text().strip()
        if not text:
            subtitle = "Name it in the editor"
        elif any(c.title.lower() == text.lower() for c in self.store.contexts):
            subtitle = f"Open “{text}”"
        else:
            subtitle = f"Start “{text}”"
        self.create_row.set_subtitle(subtitle)
        self.refresh()

    def _on_entry_activate(self, entry: Gtk.Entry) -> None:
        title = entry.get_text().strip()
        if not title:
            return

        for ctx in self.store.contexts:
            if ctx.title.lower() == title.lower():
                self._open(ctx)
                return

        self._pick_apps(self.store.create(title))

    def _on_key_pressed(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval == Gdk.KEY_Down:
            rows = self._visible_rows()
            if rows:
                rows[0].grab_focus()
                return True
        return False

    def _release_keyboard(self) -> None:
        """Give the keyboard back when leaving: Escape, opening a context, or
        the pointer moving away with the keyboard still here.

        Dropping the layer's keyboard mode is not enough on its own: Hyprland
        reports the window active again without re-sending the keyboard enter,
        so the most recent window is also focused explicitly — the recovery
        that clicking another window and coming back performs, automated.
        """
        if not self.is_sidebar:
            return
        self.set_focus(None)
        sidebar.release_focus(self)
        self._hand_keyboard_back()

    def _hand_keyboard_back(self) -> None:
        backend = getattr(self.get_application(), "backend", None)
        if backend is not None:
            hand_keyboard_back(backend=backend)

    def _holds_keyboard(self) -> bool:
        return self.is_sidebar and bool(self.get_property("is-active"))

    def _maybe_release_keyboard(self) -> None:
        """Hand the keyboard back when the pointer leaves with it still held.

        Clicking the sidebar gives it the keyboard, but clicking back into the
        window the user came from does not take it back: Hyprland still counts
        that window as focused, so the click never triggers a refocus and
        typing keeps landing in the search box. Releasing when the pointer
        leaves means the window under the next click already has the keyboard.
        """
        if not self._holds_keyboard():
            return
        if self._popover_open():
            # This leave is the popover opening, not the user going away;
            # releasing now would dismiss it a frame later. Wait it out, then
            # release if the pointer really is elsewhere.
            if self._popover_watch is None:
                self._popover_watch = GLib.timeout_add(
                    200, self._release_after_popover
                )
            return
        self._release_keyboard()

    def _release_after_popover(self) -> bool:
        if self._pointer_inside or not self._holds_keyboard():
            self._popover_watch = None
            return GLib.SOURCE_REMOVE
        if self._popover_open():
            return GLib.SOURCE_CONTINUE
        self._popover_watch = None
        self._release_keyboard()
        return GLib.SOURCE_REMOVE

    def _popover_open(self, root=None) -> bool:
        """Whether any popover in this window is showing.

        Popovers are ordinary widgets in the tree, so a walk finds dropdowns,
        context menus and the like wherever a page put them.
        """
        child = (root or self).get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Popover) and child.get_mapped():
                return True
            if self._popover_open(child):
                return True
            child = child.get_next_sibling()
        return False

    def _on_press_anywhere(self, gesture, _n_press, x: float, y: float) -> None:
        """Note whether the press landed on a control or on bare sidebar."""
        target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        # Walk up to the nearest thing that takes focus: the press usually
        # lands on a button's label rather than the button.
        while target is not None and not target.get_focusable():
            target = target.get_parent()
        self._clicked_widget = target

    def _on_active_changed(self, *_args) -> None:
        """Put the keyboard somewhere useful when the sidebar is clicked into.

        Only when nothing inside is focused already: clicking directly on a
        button or a row should leave focus where the click put it, and stealing
        it back to the search box would undo the click.

        Deferred to an idle callback because at the moment `is-active` changes
        the click has not finished being delivered — focusing here is undone a
        moment later by GTK settling focus on whatever was pressed.
        """
        if not self.get_property("is-active"):
            return
        if not self.is_sidebar or self.collapsed:
            return
        GLib.idle_add(self._focus_search_if_idle)

    def _focus_search_if_idle(self) -> bool:
        """Focus the search box unless the click landed on something.

        Not "unless something is focused": GTK gives a freshly presented window
        its first focusable child — a header button here — so there is always
        something focused and that test never fired. What matters is whether
        the user aimed at a control, which `_clicked_widget` records.
        """
        # A page pushed over the list owns its own focus.
        if self.nav.get_visible_page() is not self.home_page:
            return GLib.SOURCE_REMOVE
        if self._clicked_widget is None:
            self.entry.grab_focus()
        self._clicked_widget = None
        return GLib.SOURCE_REMOVE

    def _on_escape(self) -> bool:
        if self.nav.get_visible_page() is not self.home_page:
            self.nav.pop()
        elif self.is_sidebar:
            # A docked sidebar has no way to be reopened, so Escape clears the
            # search and hands the keyboard back rather than dismissing it.
            self.entry.set_text("")
            self._release_keyboard()
        else:
            self.close()
        return True

    def _create_from_entry(self) -> None:
        """The list's built-in row: what Enter does, clickable.

        With nothing typed there is nothing to name it by, so the editor opens
        on a blank context and the name is chosen there.
        """
        if self.entry.get_text().strip():
            self._on_entry_activate(self.entry)
        else:
            self._pick_apps(self.store.create("New context"))

    def _new_context(self) -> None:
        """The + opens the overview.

        Starting from an app or an existing context there is the fast path; a
        blank context is still one typed name away in the search bar.
        """
        app = self.get_application()
        if app is not None and hasattr(app, "open_overview"):
            app.open_overview()

    def _pick_apps(self, ctx: Context) -> None:
        """Editor for a context that was just created; committing launches it."""
        self.entry.set_text("")
        self.refresh()
        self._open_editor(ctx, lambda: self._cancel_new(ctx), is_new=True)

    def _open_editor(self, ctx: Context, on_cancel, is_new: bool = False) -> None:
        # A separate maximised window rather than a page in the sidebar: the
        # layout preview needs the monitor's shape, and the app grid needs width.
        self.editor_window = EditorWindow(
            self.get_application(),
            ctx,
            self._on_editor_done,
            on_cancel,
            on_delete=None if is_new else self._delete,
            is_new=is_new,
        )
        self.editor_window.present()
        self.editor = self.editor_window.page

    def _cancel_new(self, ctx: Context) -> None:
        # The context was created up front to give the editor something to edit,
        # so backing out has to remove it again rather than leave an empty one.
        self.store.delete(ctx)
        self.refresh()
        # The editor overlay held the keyboard exclusively; its unmap does not
        # reliably return it either.
        self._hand_keyboard_back()

    def _edit(self, ctx: Context) -> None:
        log.debug("editing context %s", ctx.title)
        self._open_editor(ctx, self._cancel_edit)

    def _cancel_edit(self) -> None:
        self.refresh()
        self._hand_keyboard_back()

    def _on_editor_done(
        self,
        ctx: Context,
        resources: list[Resource],
        title: str,
        ephemeral: bool,
        layout: Layout,
        isolated: bool = False,
    ) -> None:
        was_new = getattr(self.editor, "is_new", False)
        ctx.resources = resources
        ctx.title = title
        ctx.ephemeral = ephemeral
        ctx.layout = layout
        ctx.isolated = isolated
        self.store.save()
        self.refresh()
        if was_new:
            self._open(ctx)
        else:
            self._hand_keyboard_back()

    def _open(self, ctx: Context) -> None:
        log.info("opening context %s", ctx.title)
        self.store.touch(ctx)
        # Feeds the alt-tab between the last two contexts, which is the order
        # they were visited rather than the order they were edited.
        uistate.note_visit(ctx.id)
        self.entry.set_text("")
        self.refresh()
        # Hand focus to the context being opened rather than keeping it here.
        self._release_keyboard()
        self.on_open(ctx)

    def report_launch(self, ctx: Context, result) -> None:
        if result.reused_workspace:
            message = f"Switched to “{ctx.title}”"
        elif not ctx.apps:
            message = f"“{ctx.title}” has no apps yet"
        elif result.ok:
            count = len(result.launched)
            message = f"Opened {count} app{'s' if count != 1 else ''} for “{ctx.title}”"
        elif not result.launched:
            message = f"Couldn't open any apps for “{ctx.title}”"
        else:
            message = f"Opened {len(result.launched)}, {len(result.failed)} failed"
        if result.workspace is not None:
            message += f" · {result.backend} {result.workspace}"
        self.notify("launch", ctx.title, message)

    def _active_context(self):
        active_id = self._active_id
        if active_id is None:
            return None
        return next((c for c in self.store.contexts if c.id == active_id), None)

    def _is_open(self, ctx: Context) -> bool:
        return ctx.id in self._open_ids

    def _close(self, ctx: Context) -> None:
        if self.on_close is not None:
            self.on_close(ctx)

    def report_close(self, ctx: Context, result) -> None:
        if not result.was_open:
            message = f"“{ctx.title}” wasn't open"
        elif result.closed:
            message = f"Closed {result.closed} window{'s' if result.closed != 1 else ''}"
            if not result.workspace_removed:
                message += " · workspace kept"
        else:
            message = f"Nothing to close in “{ctx.title}”"
        self.notify("close", ctx.title, message)
        self.refresh()

    def _delete(self, ctx: Context) -> None:
        log.info("forgetting context %s", ctx.title)
        self.store.delete(ctx)
        self.refresh()
