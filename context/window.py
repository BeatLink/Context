"""Launcher window: a text bar to start a new context, and a list of previous ones."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import settings, sidebar, theme, uistate
from .editor_window import EditorWindow
from .launcher import open_state
from .layout import Layout
from .logging_setup import get_logger
from .resources import Resource
from .store import Context, ContextStore

log = get_logger("window")

# Large enough to identify a context at a glance, since the rail has no labels.
RAIL_ICON_SIZE = 32


def relative_time(stamp: float) -> str:
    delta = max(0, int(time.time() - stamp))
    if delta < 60:
        return "just now"
    for unit, seconds in (("d", 86400), ("h", 3600), ("m", 60)):
        if delta >= seconds:
            return f"{delta // seconds}{unit} ago"
    return "just now"


class ContextRow(Adw.ActionRow):
    """A context in the list.

    Open contexts get a close button; saved ones do not, and neither gets a
    delete button — forgetting a context happens in its editor, so it cannot be
    triggered by a stray click next to launch.
    """

    def __init__(
        self, ctx: Context, on_open, on_edit, on_close, is_open=False, is_active=False
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.is_open = is_open
        self.is_active = is_active
        self.set_title(GLib.markup_escape_text(ctx.title))
        self.set_activatable(True)

        subtitle = [relative_time(ctx.last_used_at)]
        if ctx.apps:
            subtitle.append(f"{len(ctx.apps)} app{'s' if len(ctx.apps) != 1 else ''}")
        if ctx.ephemeral:
            subtitle.append("ephemeral")
        self.set_subtitle(" · ".join(subtitle))

        icon = Gtk.Image.new_from_icon_name(
            "media-playback-start-symbolic" if is_open else "view-grid-symbolic"
        )
        self.add_prefix(icon)

        # The context you are actually in is marked the way a browser marks the
        # selected tab, so it is obvious at a glance which one is current.
        if is_active:
            self.add_css_class("accent")
            self.set_title(f"<b>{GLib.markup_escape_text(ctx.title)}</b>")
            self.set_use_markup(True)

        self.close = Gtk.Button(icon_name="media-playback-stop-symbolic", valign=Gtk.Align.CENTER)
        self.close.add_css_class("flat")
        self.close.set_tooltip_text("Close this context, keeping it for later")
        self.close.set_visible(is_open)
        self.close.connect("clicked", lambda _b: on_close(ctx))
        self.add_suffix(self.close)

        self.edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        self.edit.add_css_class("flat")
        self.edit.set_tooltip_text("Edit this context")
        self.edit.connect("clicked", lambda _b: on_edit(ctx))
        self.add_suffix(self.edit)

        self.connect("activated", lambda _r: on_open(ctx))


class LauncherWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, store: ContextStore, on_open, on_close=None) -> None:
        super().__init__(application=app, title="Context")
        self.store = store
        self.on_open = on_open
        self.on_close = on_close
        self._open_ids: set[str] = set()
        self._active_id: str | None = None
        self._open_signature: tuple | None = None
        self._auto_expanded = False
        self._auto_expand_source: int | None = None

        self.set_default_size(560, 620)
        # The rail's buttons are styled by the theme, so the stylesheet has to
        # be on the display before one is built.
        theme.install()
        # Docks the window to a screen edge where the compositor supports it.
        self.is_sidebar = sidebar.apply(self)

        self.nav = Adw.NavigationView()

        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
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

        self.toasts = Adw.ToastOverlay()

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

        self.start_button = Gtk.Button(label="Start")
        self.start_button.add_css_class("suggested-action")
        self.start_button.set_sensitive(False)
        self.start_button.connect("clicked", lambda _b: self._on_entry_activate(self.entry))

        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        entry_row.append(self.entry)
        entry_row.append(self.start_button)
        content.append(entry_row)

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

        groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        groups.append(self.open_label)
        groups.append(self.open_listbox)
        groups.append(self.saved_expander)

        self.empty_state = Adw.StatusPage(
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
        self.expand_button.set_tooltip_text("Expand the launcher")
        self.expand_button.connect("clicked", lambda _b: self.toggle_collapsed())

        rail_scroller = Gtk.ScrolledWindow(vexpand=True)
        rail_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rail_scroller.set_child(self.rail)

        rail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        rail_box.append(self.expand_button)
        rail_box.append(rail_scroller)

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

        self.home_page = Adw.NavigationPage(child=self.toolbar, title="Context", tag="home")
        self.nav.add(self.home_page)
        self.toasts.set_child(self.nav)
        self.set_content(self.toasts)

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

        # Keyboard focus is taken only while the entry is actively being used.
        #
        # It cannot be tied to GTK focus alone: once the layer takes keyboard
        # focus the entry keeps it, so no `leave` ever arrives and clicking
        # another window cannot take it back — the panel holds the keyboard until
        # something outside GTK intervenes. Instead the grab is released whenever
        # the pointer leaves the sidebar, which is the gesture that means "I am
        # done here", and the entry drops GTK focus at the same time.
        # The grab has to happen on the click itself. With KeyboardMode.NONE the
        # layer has no keyboard, so GTK never gives the entry focus and a
        # focus-enter handler would never run — the entry would be untypable.
        # Capture phase, so the mode is raised before GTK routes the click.
        # The keyboard is taken when the pointer enters the sidebar and released
        # when it leaves.
        #
        # It cannot be taken on click: with KeyboardMode.NONE the layer has no
        # keyboard, so raising the mode from a click handler happens too late for
        # that same click to reach the entry — the first click was swallowed and
        # only a second one landed. Entering is also the earliest unambiguous
        # signal that the user is coming here to type, and leaving is what lets a
        # click on another window take focus back, which GTK focus-leave never
        # reports while the layer holds the keyboard.
        pointer = Gtk.EventControllerMotion()
        pointer.connect("enter", lambda *_a: self._on_pointer_enter())
        pointer.connect("leave", lambda *_a: self._on_pointer_leave())
        self.add_controller(pointer)

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
            self.collapsed = False
            uistate.save(collapsed=False)
        if self.collapse_button is not None:
            self.collapse_button.set_visible(self.collapses)
        self._apply_collapsed()
        self._restart_poll()
        if needs_restart:
            names = ", ".join(sorted(changed or {}))
            # The toast carries the restart rather than only mentioning it,
            # since the setting is otherwise stuck until Context is found and
            # relaunched by hand.
            toast = Adw.Toast(
                title=f"{names} applies when Context restarts", timeout=8
            )
            toast.set_button_label("Restart")
            toast.connect("button-clicked", lambda _t: self._restart_app())
            self.toasts.add_toast(toast)

    def _restart_app(self) -> None:
        app = self.get_application()
        if app is not None:
            app.restart()

    def _restart_poll(self) -> None:
        if self._poll_source is not None:
            GLib.source_remove(self._poll_source)
        self._poll_source = GLib.timeout_add_seconds(
            settings.current().poll_seconds, self._poll_open_state
        )

    def _on_pointer_enter(self) -> None:
        self._take_keyboard(focus=False)
        if not (self.collapsed and self.collapses):
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
        self._release_keyboard()
        if self._auto_expand_source is not None:
            GLib.source_remove(self._auto_expand_source)
            self._auto_expand_source = None
        if not self._auto_expanded:
            return
        self._auto_expanded = False
        self.collapsed = True
        self._apply_collapsed()
        self.refresh()

    def toggle_collapsed(self) -> None:
        if not self.collapses:
            log.info("collapsing is switched off")
            return
        # A deliberate toggle ends any hover peek, so the state that gets saved
        # is the one the user chose rather than the one hovering produced.
        self._auto_expanded = False
        self.collapsed = not self.collapsed
        uistate.save(collapsed=self.collapsed)
        log.info("sidebar %s", "collapsed" if self.collapsed else "expanded")
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
        image.set_pixel_size(RAIL_ICON_SIZE)
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
                ContextRow(
                    ctx,
                    self._open,
                    self._edit,
                    self._close,
                    is_open=True,
                    is_active=active is not None and ctx.id == active.id,
                )
            )

        self.listbox.remove_all()
        for ctx in saved:
            self.listbox.append(
                ContextRow(ctx, self._open, self._edit, self._close, is_open=False)
            )

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

        if opened or saved:
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
        self.start_button.set_sensitive(bool(text))
        self.start_button.set_label(
            "Open" if any(c.title.lower() == text.lower() for c in self.store.contexts) else "Start"
        )
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

    def _take_keyboard(self, focus: bool = True) -> None:
        """Raise the layer's keyboard mode so the sidebar can be typed in.

        `focus` is False when this fires from the pointer entering: the keyboard
        is made available, but which widget gets it is left to the click, so
        hovering does not steal the caret from somewhere else.
        """
        if not self.is_sidebar:
            return
        sidebar.grab_keyboard(self, True)
        if focus:
            self.entry.grab_focus()

    def _release_keyboard(self) -> None:
        """Hand the keyboard back to whatever the user clicks next."""
        if not self.is_sidebar:
            return
        # Dropping GTK focus as well, so the entry does not silently keep it and
        # re-grab the keyboard the moment the pointer returns.
        self.set_focus(None)
        sidebar.grab_keyboard(self, False)

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

    def _edit(self, ctx: Context) -> None:
        log.debug("editing context %s", ctx.title)
        self._open_editor(ctx, self._cancel_edit)

    def _cancel_edit(self) -> None:
        self.refresh()

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

    def _open(self, ctx: Context) -> None:
        log.info("opening context %s", ctx.title)
        self.store.touch(ctx)
        # Feeds the alt-tab between the last two contexts, which is the order
        # they were visited rather than the order they were edited.
        uistate.note_visit(ctx.id)
        self.entry.set_text("")
        self.refresh()
        # Hand focus to the context being opened rather than keeping it here.
        if self.is_sidebar:
            sidebar.grab_keyboard(self, False)
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
        self.toasts.add_toast(Adw.Toast(title=message, timeout=3))

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
        self.toasts.add_toast(Adw.Toast(title=message, timeout=3))
        self.refresh()

    def _delete(self, ctx: Context) -> None:
        log.info("forgetting context %s", ctx.title)
        self.store.delete(ctx)
        self.refresh()
