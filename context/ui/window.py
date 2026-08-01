"""Launcher window: a text bar to start a new context, and a list of previous ones."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from context.state import scratchpad, settings, uistate
from context.system import notify
from context.ui import rail, sidebar, theme, widgets
from context.system.apps import App, installed_apps, search_apps
from context.ui.editor_window import EditorWindow
from context.system.launcher import LiveState, hand_keyboard_back, is_no_context
from context.system.launcher import loose_context, read_live_state
from context.state.layout import Layout
from context.system.logging_setup import get_logger
from context.state.resources import Resource
from context.ui.rows import AppRow, ContextRow, NoteRow, context_for_app, relative_time
from context.state.scratchpad import Note, NoteStore
from context.state.store import Context, ContextStore

log = get_logger("window")

# The rail moved to its own module; these names stay importable from here for
# everything that learned them at this address.
from context.ui.rail import MIN_RAIL_ICON, RAIL_ICON_PADDING, RAIL_MARGIN, rail_icon_size

# How many application results the sidebar's list shows. The full set is
# hundreds of rows rebuilt on every keystroke, and the point of the sidebar's
# search is the first few hits; the heading says when there are more.
APP_RESULTS = 8

# How many notes the sidebar lists. The overview has room for all of them; a
# narrow column showing every note would bury the contexts it is mainly for.
NOTE_RESULTS = 6

# How often the cursor is asked for while the sidebar waits to retract. It has
# left the surface by then, so there are no motion events to go on.
ZONE_POLL_MS = 200


class LauncherWindow(Gtk.ApplicationWindow):
    def __init__(
        self,
        app: Gtk.Application,
        store: ContextStore,
        on_open,
        on_close=None,
        monitor: str | None = None,
        notes: NoteStore | None = None,
    ) -> None:
        super().__init__(application=app, title="Context")
        # Which screen this launcher docks to. None means the setting decides,
        # which is what a single launcher uses.
        self.monitor = monitor
        self.store = store
        # Shared with every other launcher, the way the context store is: a note
        # written on one screen has to appear on the others.
        self.notes = notes if notes is not None else NoteStore()
        self.on_open = on_open
        self.on_close = on_close
        self._open_ids: set[str] = set()
        self._active_id: str | None = None
        self._open_signature: tuple | None = None
        self._live = LiveState()
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

        # Everything Context can open, on one screen. In the body rather than
        # the header: as a + in the corner it read as "new context", which is
        # the row below's job. It shares a row with the search box — the two
        # are both ways of finding something to open, and stacked they cost a
        # row each of a sidebar that has few to spend. Alone, either takes the
        # full width; together the button folds to its icon.
        self.overview_button = Gtk.Button()
        overview_face = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        overview_face.append(Gtk.Image.new_from_icon_name("view-grid-symbolic"))
        self.overview_label = Gtk.Label(label="Overview")
        overview_face.append(self.overview_label)
        self.overview_button.set_child(overview_face)
        self.overview_button.set_tooltip_text(
            "Everything you can open, on one screen"
        )
        self.overview_button.connect("clicked", lambda _b: self._new_context())

        # No Start button: Enter is the trigger, and the row below is the
        # clickable path — a button that mirrored the row said the same thing
        # twice in half the sidebar's width.
        self.top_row = Gtk.Box()
        self.top_row.add_css_class("linked")
        self.top_row.append(self.entry)
        self.top_row.append(self.overview_button)
        content.append(self.top_row)

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

        # Notes. The global ones and the current context's are listed together
        # and told apart by a tag on the row, rather than split into two groups:
        # at sidebar width a second heading costs more than it explains.
        self.notes_label = Gtk.Label(xalign=0.0)
        self.notes_label.add_css_class("heading")
        self.notes_label.add_css_class("dim-label")

        self.notes_listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.notes_listbox.add_css_class("boxed-list")

        self.new_note_row = widgets.ActionRow(title="New note")
        self.new_note_row.set_activatable(True)
        self.new_note_row.add_prefix(
            Gtk.Image.new_from_icon_name("document-new-symbolic")
        )
        self.new_note_row.set_subtitle("A blank note, in the scratchpad")
        self.new_note_row.connect("activated", lambda _r: self._new_note())

        groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        groups.append(self.open_label)
        groups.append(self.open_listbox)
        groups.append(self.saved_expander)
        groups.append(self.apps_label)
        groups.append(self.apps_listbox)
        groups.append(self.notes_label)
        groups.append(self.notes_listbox)

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

        # Collapsed, the sidebar is the rail — see context/rail.py. The old
        # names stay as aliases so callers keep working: `rail` is the strip of
        # context buttons, `rail_box` the whole widget.
        self.rail_box = rail.Rail(
            on_open=self._open,
            on_expand=self.toggle_collapsed,
            on_toggle_saved=self._toggle_saved,
        )
        self.rail = self.rail_box.buttons
        self.expand_button = self.rail_box.expand_button

        self.mode_stack = Gtk.Stack()
        # A homogeneous stack requests the largest child's size, so the full
        # launcher would hold the window at 380px however narrow the rail is.
        self.mode_stack.set_hhomogeneous(False)
        self.mode_stack.set_vhomogeneous(False)
        self.mode_stack.add_named(content, "full")
        self.mode_stack.add_named(self.rail_box, "rail")
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
        self._apply_sections()
        self.refresh()

        # Which contexts are open changes outside this window — a context is
        # launched, its last window closes, you switch workspaces by keyboard.
        # Nothing notifies the launcher, so the open list is re-checked on a
        # timer; without it the list only updated when the user acted here.
        self._poll_source: int | None = None
        self._restart_poll()

    def _read_open_state(self) -> bool:
        """Ask the backend what is running. Returns whether anything changed.

        One pass for all of it — open, focused, drifted, and what belongs to no
        context — since each answer is a compositor query and this runs on a
        timer.
        """
        if self.on_close is None:
            self._live = LiveState()
            self._open_ids, self._active_id = set(), None
            return False
        try:
            live = read_live_state(self.store.contexts, backend=self._backend())
        except OSError:
            return False

        if live.signature == self._open_signature:
            return False
        self._open_signature = live.signature
        self._live = live
        self._open_ids, self._active_id = live.open_ids, live.active_id
        return True

    def _backend(self):
        return getattr(self.get_application(), "backend", None)

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
        if app is not None and hasattr(app, "note_open_contexts"):
            app.note_open_contexts(len(self._open_ids))
        return True

    def open_settings(self) -> None:
        """Settings are a screen of their own, not a page in this column."""
        app = self.get_application()
        if app is not None and hasattr(app, "open_settings"):
            self._release_keyboard()
            app.open_settings()

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
        self._apply_sections()
        self.refresh()
        self._restart_poll()
        if needs_restart:
            names = ", ".join(sorted(changed or {}))
            # The notification carries the restart rather than only mentioning
            # it, since the setting is otherwise stuck until Context is found
            # and relaunched by hand.
            # Essential: with notifications switched off this would vanish,
            # leaving no feedback and no path to the restart the change needs.
            self.notify(
                "restart",
                "Restart to apply",
                f"{names} applies when Context restarts",
                button="Restart",
                on_click=self._restart_app,
                essential=True,
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
        # Pinning: a sidebar held open by the pointer is not "expanded" in the
        # sense the toggle means, so pressing the button while peeking has to
        # keep it rather than close it.
        wanted = False if (self._pins and self._auto_expanded) else not self.collapsed
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

    def _apply_sections(self) -> None:
        """Show only the parts of the sidebar that are switched on."""
        live = settings.current()
        self.entry.set_visible(live.show_search)
        self.create_list.set_visible(live.show_new_context)
        self.overview_button.set_visible(live.show_overview_button)
        self.top_row.set_visible(live.show_search or live.show_overview_button)
        # Joined to the search box the button folds to its icon; on its own it
        # says what it opens and takes the row.
        self.overview_label.set_visible(not live.show_search)
        self.overview_button.set_hexpand(not live.show_search)

    def _sync_collapse_button(self) -> None:
        """What the header's button means, which depends on how it collapses.

        Expanding on hover makes "collapse" the wrong word: the sidebar is open
        because the pointer is there and will close again on its own, so what
        the button offers is to keep it — and pressing it while peeking used to
        do the opposite of what it looked like, closing the sidebar under the
        pointer.
        """
        if self.collapse_button is None:
            return
        collapse_icon = {
            "left": "go-previous-symbolic",
            "right": "go-next-symbolic",
            "top": "go-up-symbolic",
            "bottom": "go-down-symbolic",
        }[sidebar.configured_edge()]
        if not self._pins:
            self.collapse_button.set_icon_name(collapse_icon)
            self.collapse_button.set_tooltip_text("Collapse to a rail")
            return
        pinned = not getattr(self, "collapsed", False) and not self._auto_expanded
        self.collapse_button.set_icon_name(
            "view-pin-symbolic" if not pinned else collapse_icon
        )
        self.collapse_button.set_tooltip_text(
            "Unpin — let it collapse when the pointer leaves"
            if pinned
            else "Pin the launcher open"
        )

    @property
    def _pins(self) -> bool:
        """Whether the button pins rather than collapses.

        Only when the sidebar opens itself: hiding always reveals on hover, so
        that counts too whatever the switch says.
        """
        live = settings.current()
        return self.collapses and (live.auto_expand or self.hides_when_collapsed)

    def _apply_collapsed(self) -> None:
        """Swap the content and give the reserved space back."""
        self._sync_collapse_button()
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
        live = settings.current()
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
            # The rail ignores the search, not the settings: with saved
            # contexts switched off the list hides them, and a rail that kept
            # showing them disagreed with what it collapses back into.
            rail_saved = (
                [c for c in all_contexts if not self._is_open(c)]
                if live.show_saved
                else []
            )
            self.rail_box.rebuild(
                rail_open,
                rail_saved,
                # Searching is not a thing at rail width, so it plays no part.
                shown=self._saved_group_shown(rail_open, rail_saved, searching=False),
                active_id=self._active_id,
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
        # Last in the open group: everything running that belongs to no
        # context, as a context of its own until it is given one. Not filtered
        # by the search, since it has no name to match.
        loose = loose_context(self._live.loose) if not searching else None
        if loose is not None:
            self.open_listbox.append(self._context_row(loose, is_open=True))

        self.listbox.remove_all()
        for ctx in saved:
            self.listbox.append(self._context_row(ctx, is_open=False))

        app_matches = self._app_matches(query) if live.show_apps else []
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

        # Notes for wherever you are standing: the global ones, plus this
        # context's if both are switched on. `visible` is what reads the
        # settings, so the sidebar and the overview cannot disagree about it.
        notes = (
            self.notes.search(query, active.id if active else None)
            if live.scratchpad and live.show_notes
            else []
        )
        self.notes_listbox.remove_all()
        for note in notes[:NOTE_RESULTS]:
            self.notes_listbox.append(
                NoteRow(note, self._open_note, self._delete_note)
            )
        notes_shown = live.scratchpad and live.show_notes
        if notes_shown and not searching:
            # Not while searching: the row would start a note named after a
            # query that was meant to find one.
            self.notes_listbox.append(self.new_note_row)
        self.notes_label.set_visible(notes_shown)
        self.notes_listbox.set_visible(notes_shown)
        shown_notes = min(len(notes), NOTE_RESULTS)
        self.notes_label.set_label(
            f"Notes · {shown_notes} of {len(notes)}"
            if len(notes) > shown_notes
            else f"Notes · {len(notes)}"
        )

        self.open_label.set_visible(bool(opened or loose))
        self.open_listbox.set_visible(bool(opened or loose))
        self.open_label.set_label("Open")

        self.saved_expander.set_visible(bool(saved) and live.show_saved)
        self.list_label.set_visible(bool(saved) and live.show_saved)
        self.list_label.set_label(f"Saved · {len(saved)}")

        should_expand = self._saved_group_shown(opened, saved, searching)
        if self.saved_expander.get_expanded() != should_expand:
            self._suppress_toggle = True
            self.saved_expander.set_expanded(should_expand)
            self._suppress_toggle = False

        # Notes count only when there are some. The section is on by default, so
        # letting it alone stand for "there is something to show" meant the
        # empty state — the only place that says how to make a first context —
        # could never appear again.
        if opened or loose or app_matches or (saved and live.show_saved) or notes:
            self.stack.set_visible_child_name("list")
            return

        self.stack.set_visible_child_name("empty")
        if searching:
            self.empty_state.set_title("No matches")
            self.empty_state.set_description(
                "Press Enter to start a new context with this name."
            )
        elif saved and not live.show_saved:
            # There are contexts; the settings are hiding them. Saying "no
            # contexts yet" here was a lie with an unhelpful instruction.
            count = len(saved)
            self.empty_state.set_title("Nothing open")
            self.empty_state.set_description(
                f"{count} saved context{'s are' if count != 1 else ' is'} hidden "
                "by the sidebar settings."
            )
        else:
            self.empty_state.set_title("No contexts yet")
            # The instruction has to name a control that is actually on
            # screen: "type a name above" with the search box switched off
            # pointed at nothing.
            if live.show_search:
                description = "Type a name above to create your first one."
            elif live.show_new_context:
                description = "“New context” above starts your first one."
            elif live.show_overview_button:
                description = "The Overview button starts your first one."
            else:
                description = "Open the overview to start your first one."
            self.empty_state.set_description(description)

    def _context_row(self, ctx: Context, is_open: bool, is_active: bool = False):
        # The no-context has no definition to edit, forget or add an app to
        # until it has been saved as one; what it does have is windows, so it
        # can be closed and it always offers to be kept.
        virtual = is_no_context(ctx)
        return ContextRow(
            ctx,
            self._open,
            None if virtual else self._edit,
            self._close,
            is_open=is_open,
            is_active=is_active,
            is_drifted=virtual or ctx.id in self._live.drifted_ids,
            on_forget=None if virtual else self._delete,
            on_add_app=None if virtual else self._add_app_to_context,
            on_save=self._save,
        )

    def _save(self, ctx: Context) -> None:
        """Keep the windows as they are — for a context, or as a new one."""
        app = self.get_application()
        if app is not None and hasattr(app, "save_context"):
            app.save_context(ctx)

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
        """Start an app from the search results.

        Where it lands is a setting: a context of its own, or the context you
        are standing in — with a new context as the fallback when there is
        nothing to add to.
        """
        app = self.get_application()
        if (
            settings.current().search_apps_target == "current"
            and app is not None
            and getattr(app, "add_app_to_active", lambda _i: False)(info)
        ):
            log.info("added %s to the current context", info.id)
            self.entry.set_text("")
            self.refresh()
            self._release_keyboard()
            return
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
        elif any(c.title.casefold() == text.casefold() for c in self.store.contexts):
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
            if ctx.title.casefold() == title.casefold():
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

    def _new_note(self) -> None:
        """Start a note where the settings say a new one belongs.

        In the context you are standing in when per-context notes are on and
        there is one, global otherwise — so the common case needs no choice, and
        the editor can still move it either way.
        """
        live = settings.current()
        active = self._active_context()
        owner = (
            active.id
            if (live.scratchpad_per_context and active is not None)
            else scratchpad.GLOBAL
        )
        self._open_note(self.notes.create(context_id=owner))

    def _open_note(self, note: Note) -> None:
        from context.ui.note_window import NoteWindow

        active = self._active_context()
        log.debug("opening note %s", note.id)
        self.note_window = NoteWindow(
            self.get_application(),
            self.notes,
            note,
            on_done=self._on_note_done,
            context_id=active.id if active is not None else scratchpad.GLOBAL,
            context_title=active.title if active is not None else "",
        )
        self.note_window.present()

    def _on_note_done(self, note: Note) -> None:
        # An untitled note that was never written into is housekeeping, not a
        # note: opening the editor and closing it again should leave nothing
        # behind. Anything with a version or a title is kept.
        if not note.title and not note.body:
            self.notes.delete(note)
        self.refresh()
        self._hand_keyboard_back()

    def _delete_note(self, note: Note) -> None:
        self.notes.delete(note)
        self.refresh()

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
        if is_no_context(ctx):
            # Nothing to launch: its windows are already open, so this is a
            # jump to them rather than an opening of anything.
            self._release_keyboard()
            self.on_open(ctx)
            return
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
