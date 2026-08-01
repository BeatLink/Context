"""Switching between contexts and between windows.

Two pickers over one list widget. The context switcher jumps to a context by
name; the window switcher jumps to a window, within the current context or
across all of them.

Both are overlays rather than windows, the way the editor is: a picker that gets
tiled into the layout it is being used to navigate would be absurd.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, Gtk

from context.system import backends
from context.ui import sidebar, theme, widgets
from context.system.launcher import open_state
from context.system.logging_setup import get_logger

log = get_logger("switcher")

CONTEXTS = "contexts"
WINDOWS = "windows"


def _icon_for(app_id: str) -> Gtk.Image:
    """The application's own icon, falling back to a generic one."""
    for candidate in (app_id, f"{app_id}.desktop", app_id.lower()):
        try:
            info = Gio.DesktopAppInfo.new(candidate)
        except TypeError:
            info = None
        icon = info.get_icon() if info is not None else None
        if icon is not None:
            image = Gtk.Image.new_from_gicon(icon)
            image.set_pixel_size(24)
            return image
    image = Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
    image.set_pixel_size(24)
    return image


class SwitcherWindow(Gtk.ApplicationWindow):
    """A filterable list of contexts or windows, over the whole screen."""

    def __init__(self, app, store, mode: str = CONTEXTS, scope_all: bool = False) -> None:
        super().__init__(application=app, title="Switch")
        self.add_css_class("ctx-window")
        self.store = store
        self.mode = mode
        self.scope_all = scope_all
        self.backend = backends.detect()
        self.entries: list[tuple[str, str, object]] = []

        theme.install()
        self.set_default_size(720, 520)
        if not sidebar.apply_overlay(self):
            self.fullscreen()

        toolbar = widgets.ToolbarView()
        toolbar.add_css_class("ctx-surface")
        toolbar.add_css_class("ctx-solid")
        toolbar.set_overflow(Gtk.Overflow.HIDDEN)
        self.header = widgets.HeaderBar()
        header = self.header
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)

        # Every other full-screen overlay has one; this was the exception, so
        # the only way out of the move picker was knowing about Escape.
        self.back_button = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_button.add_css_class("flat")
        self.back_button.set_tooltip_text("Back")
        self.back_button.connect("clicked", lambda _b: self._dismiss())
        header.pack_start(self.back_button)

        self.scope_button: Gtk.Button | None = None
        if mode == WINDOWS:
            self.scope_button = Gtk.Button(label="All contexts" if scope_all else "This context")
            self.scope_button.add_css_class("flat")
            self.scope_button.set_tooltip_text("Change which windows are listed")
            self.scope_button.connect("clicked", lambda _b: self.toggle_scope())
            header.pack_end(self.scope_button)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(content, setter)(18)

        self.entry = widgets.SearchBar(
            (
                "Switch to a context" if mode == CONTEXTS else "Switch to a window"
            )
        )
        self.entry.connect("search-changed", lambda _e: self.refresh())
        self.entry.connect("activate", lambda _e: self._activate_first())
        content.append(self.entry)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.listbox)
        content.append(scroller)

        self.empty = Gtk.Label(label="Nothing to switch to")
        self.empty.add_css_class("dim-label")
        content.append(self.empty)

        toolbar.set_content(content)
        self.set_child(toolbar)

        escape = Gtk.ShortcutController()
        escape.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.CallbackAction.new(lambda *_a: self._dismiss()),
            )
        )
        self.add_controller(escape)

        # Down from the entry moves into the list, as it does in the launcher.
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.entry.add_controller(keys)

        self.refresh()

    # -- contents ------------------------------------------------------------

    def set_heading(self, text: str) -> None:
        """Name what the picker is for, on the window and in its header.

        `set_title` alone names the window, which on a layer-shell overlay
        nothing draws — so a picker opened to move a window said nothing about
        what picking would do.
        """
        self.set_title(text)
        self.header.set_title(text)

    def toggle_scope(self) -> None:
        self.scope_all = not self.scope_all
        if self.scope_button is not None:
            self.scope_button.set_label(
                "All contexts" if self.scope_all else "This context"
            )
        self.refresh()

    def _collect(self) -> list[tuple[str, str, object]]:
        """(title, subtitle, target) for everything switchable."""
        if self.mode == CONTEXTS:
            open_ids, active_id = open_state(self.store.contexts, backend=self.backend)
            collected = []
            for ctx in self.store.contexts:
                if ctx.id == active_id:
                    state = "here now"
                elif ctx.id in open_ids:
                    state = "open"
                else:
                    state = "saved"
                collected.append((ctx.title, state, ctx))
            # Open contexts first: switching is mostly to something running.
            return sorted(collected, key=lambda row: row[1] == "saved")

        handle = None if self.scope_all else self.backend.current_handle()
        by_handle = {
            ctx.handle_for(self.backend.name): ctx.title for ctx in self.store.contexts
        }
        return [
            (
                window.title or window.app_id,
                by_handle.get(window.handle, window.handle or "no context"),
                window,
            )
            for window in self.backend.windows(handle)
        ]

    def refresh(self) -> None:
        query = self.entry.get_text().strip().casefold()
        self.entries = [
            row
            for row in self._collect()
            if not query or query in f"{row[0]}\n{row[1]}".casefold()
        ]

        self.listbox.remove_all()
        for title, subtitle, target in self.entries:
            row = widgets.ActionRow()
            row.set_title(title)
            row.set_subtitle(subtitle)
            row.set_activatable(True)
            row.target = target
            if self.mode == WINDOWS:
                row.add_prefix(_icon_for(getattr(target, "app_id", "")))
            row.connect("activated", self._on_row_activated)
            self.listbox.append(row)

        self.listbox.set_visible(bool(self.entries))
        self.empty.set_visible(not self.entries)
        first = self.listbox.get_row_at_index(0)
        if first is not None:
            self.listbox.select_row(first)

    # -- acting --------------------------------------------------------------

    def _on_key_pressed(self, _controller, keyval, _code, _state) -> bool:
        from gi.repository import Gdk

        if keyval == Gdk.KEY_Down:
            first = self.listbox.get_row_at_index(0)
            if first is not None:
                first.grab_focus()
            return True
        return False

    def _activate_first(self) -> None:
        row = self.listbox.get_selected_row() or self.listbox.get_row_at_index(0)
        if row is not None:
            self._on_row_activated(row)

    def _on_row_activated(self, row) -> None:
        target = getattr(row, "target", None)
        if target is None:
            return
        self.close()
        if self.mode == CONTEXTS:
            log.info("switching to context %s", target.title)
            self.on_context(target)
        else:
            log.info("focusing window %s", target.title or target.app_id)
            self.backend.focus_window(target.id)

    def _dismiss(self) -> bool:
        self.close()
        return True

    # Set by the caller; switching to a context goes through the launcher so it
    # is opened rather than merely focused when its windows are gone.
    def on_context(self, ctx) -> None:
        return None
