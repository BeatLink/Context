"""Launcher window: a text bar to start a new context, and a list of previous ones."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk

from . import sidebar
from .editor_window import EditorWindow
from .launcher import active_context, context_is_open
from .layout import Layout
from .resources import Resource
from .store import Context, ContextStore


def display_name() -> str:
    real = GLib.get_real_name()
    if real and real != "Unknown":
        return real.split(",")[0].strip() or GLib.get_user_name()
    return GLib.get_user_name()


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

        self.set_default_size(560, 620)
        # Docks the window to a screen edge where the compositor supports it.
        self.is_sidebar = sidebar.apply(self)

        self.nav = Adw.NavigationView()

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        if self.is_sidebar:
            # Nothing to minimise or close when docked.
            header.set_show_start_title_buttons(False)
            header.set_show_end_title_buttons(False)
        toolbar.add_top_bar(header)

        self.toasts = Adw.ToastOverlay()

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(12)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        heading = Gtk.Label(
            label=f"Hey {display_name()}, what would you like to do today?",
            xalign=0.0,
            wrap=True,
        )
        heading.add_css_class("title-1")
        content.append(heading)

        self.entry = Gtk.Entry(
            placeholder_text="Name a new context, or search existing…",
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

        groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        groups.append(self.open_label)
        groups.append(self.open_listbox)
        groups.append(self.list_label)
        groups.append(self.listbox)

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

        toolbar.set_content(content)

        self.home_page = Adw.NavigationPage(child=toolbar, title="Context", tag="home")
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
        entry_click = Gtk.GestureClick()
        entry_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        entry_click.connect("pressed", lambda *_a: self._take_keyboard())
        self.entry.add_controller(entry_click)

        # Releasing when the pointer leaves the sidebar is what lets a click on
        # another window take focus back; tying it to GTK focus-leave does not
        # work, since the entry keeps focus while the layer holds the keyboard.
        pointer = Gtk.EventControllerMotion()
        pointer.connect("leave", lambda *_a: self._release_keyboard())
        self.add_controller(pointer)

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
        """Show open contexts, the way a browser shows open tabs.

        Saved contexts are not listed by default: they appear once you search,
        which is the browser's "new tab" behaviour — the list is what is running,
        and searching is how you reach everything else.
        """
        query = self.entry.get_text().strip()
        searching = bool(query)
        matches = self.store.search(query)

        active = self._active_context()
        opened = [c for c in matches if self._is_open(c)]
        saved = [c for c in matches if c not in opened] if searching else []

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

        self.list_label.set_visible(bool(saved))
        self.list_label.set_label("Saved")
        self.listbox.set_visible(bool(saved))

        if opened or saved:
            self.stack.set_visible_child_name("list")
            return

        self.stack.set_visible_child_name("empty")
        if searching:
            self.empty_state.set_title("No matches")
            self.empty_state.set_description(
                "Press Enter to start a new context with this name."
            )
        elif self.store.contexts:
            self.empty_state.set_title("Nothing open")
            self.empty_state.set_description(
                "Search above to reopen a saved context, or name a new one."
            )
        else:
            self.empty_state.set_title("No contexts yet")
            self.empty_state.set_description("Type a name above to create your first one.")

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

    def _take_keyboard(self) -> None:
        """Raise the layer's keyboard mode so the entry can be typed in."""
        if not self.is_sidebar:
            return
        sidebar.grab_keyboard(self, True)
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
    ) -> None:
        was_new = getattr(self.editor, "is_new", False)
        ctx.resources = resources
        ctx.title = title
        ctx.ephemeral = ephemeral
        ctx.layout = layout
        self.store.save()
        self.refresh()
        if was_new:
            self._open(ctx)

    def _open(self, ctx: Context) -> None:
        self.store.touch(ctx)
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
        if self.on_close is None:
            return None
        try:
            return active_context(self.store.contexts)
        except OSError:
            return None

    def _is_open(self, ctx: Context) -> bool:
        if self.on_close is None:
            return False
        try:
            return context_is_open(ctx)
        except OSError:
            return False

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
        self.store.delete(ctx)
        self.refresh()
