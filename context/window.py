"""Launcher window: a text bar to start a new context, and a list of previous ones."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk

from .app_picker import AppPickerPage
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
    def __init__(self, ctx: Context, on_open, on_edit, on_delete) -> None:
        super().__init__()
        self.ctx = ctx
        self.set_title(GLib.markup_escape_text(ctx.title))
        self.set_activatable(True)

        subtitle = [relative_time(ctx.last_used_at)]
        if ctx.apps:
            subtitle.append(f"{len(ctx.apps)} app{'s' if len(ctx.apps) != 1 else ''}")
        if ctx.ephemeral:
            subtitle.append("ephemeral")
        self.set_subtitle(" · ".join(subtitle))

        icon = Gtk.Image.new_from_icon_name(
            "user-trash-symbolic" if ctx.ephemeral else "view-grid-symbolic"
        )
        self.add_prefix(icon)

        self.edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        self.edit.add_css_class("flat")
        self.edit.set_tooltip_text("Edit this context")
        self.edit.connect("clicked", lambda _b: on_edit(ctx))
        self.add_suffix(self.edit)

        self.remove = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.CENTER)
        self.remove.add_css_class("flat")
        self.remove.set_tooltip_text("Forget this context")
        self.remove.connect("clicked", lambda _b: on_delete(ctx))
        self.add_suffix(self.remove)

        self.connect("activated", lambda _r: on_open(ctx))


class LauncherWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, store: ContextStore, on_open) -> None:
        super().__init__(application=app, title="Context")
        self.store = store
        self.on_open = on_open

        self.set_default_size(560, 620)

        self.nav = Adw.NavigationView()

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("flat")
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
        content.append(self.list_label)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")

        self.empty_state = Adw.StatusPage(
            icon_name="view-grid-symbolic",
            title="No contexts yet",
            description="Type a name above to create your first one.",
        )
        self.empty_state.set_vexpand(True)

        self.stack = Gtk.Stack()
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.listbox)
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
        query = self.entry.get_text()
        matches = self.store.search(query)

        self.listbox.remove_all()
        for ctx in matches:
            self.listbox.append(ContextRow(ctx, self._open, self._edit, self._delete))

        if not self.store.contexts:
            self.stack.set_visible_child_name("empty")
            self.list_label.set_visible(False)
        elif not matches:
            self.empty_state.set_title("No matches")
            self.empty_state.set_description(
                "Press Enter to create a new context with this name."
            )
            self.stack.set_visible_child_name("empty")
            self.list_label.set_visible(False)
        else:
            self.stack.set_visible_child_name("list")
            self.list_label.set_visible(True)
            self.list_label.set_label("Matching contexts" if query.strip() else "Recent contexts")

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

    def _on_escape(self) -> bool:
        if self.nav.get_visible_page() is not self.home_page:
            self.nav.pop()
        else:
            self.close()
        return True

    def _pick_apps(self, ctx: Context) -> None:
        self.entry.set_text("")
        self.refresh()
        self.picker = AppPickerPage(ctx, self._on_apps_chosen)
        self.nav.push(self.picker)

    def _on_apps_chosen(self, ctx: Context, app_ids: list[str]) -> None:
        ctx.apps = app_ids
        self.store.save()
        self.nav.pop()
        self.refresh()
        self._open(ctx)

    def _edit(self, ctx: Context) -> None:
        self.editor = AppPickerPage(ctx, self._on_edit_saved, edit_mode=True)
        self.nav.push(self.editor)

    def _on_edit_saved(
        self, ctx: Context, app_ids: list[str], title: str, ephemeral: bool
    ) -> None:
        ctx.apps = app_ids
        ctx.title = title
        ctx.ephemeral = ephemeral
        self.store.save()
        self.nav.pop()
        self.refresh()

    def _open(self, ctx: Context) -> None:
        self.store.touch(ctx)
        self.entry.set_text("")
        self.refresh()
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

    def _delete(self, ctx: Context) -> None:
        self.store.delete(ctx)
        self.refresh()
