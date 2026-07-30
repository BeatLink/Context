"""App selector page shown after a context is created."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from .apps import App, installed_apps, search_apps
from .store import Context


class AppRow(Adw.ActionRow):
    def __init__(self, app: App, selected: bool, on_toggle) -> None:
        super().__init__()
        self.app = app
        self.set_title(app.name)
        if app.description:
            self.set_subtitle(app.description)
        self.set_activatable(True)

        if app.icon is not None:
            image = Gtk.Image.new_from_gicon(app.icon)
        else:
            image = Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
        image.set_pixel_size(32)
        self.add_prefix(image)

        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        self.check.set_active(selected)
        self.check.connect("toggled", lambda btn: on_toggle(app, btn.get_active()))
        self.add_suffix(self.check)

        self.connect("activated", lambda _r: self.check.set_active(not self.check.get_active()))


class AppPickerPage(Adw.NavigationPage):
    def __init__(self, ctx: Context, on_done, edit_mode: bool = False) -> None:
        super().__init__(title="Edit context" if edit_mode else ctx.title)
        self.ctx = ctx
        self.on_done = on_done
        self.edit_mode = edit_mode
        self.apps = installed_apps()
        self.selected: set[str] = set(ctx.apps)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.done_button = Gtk.Button(label="Save" if edit_mode else "Done")
        self.done_button.add_css_class("suggested-action")
        self.done_button.connect("clicked", lambda _b: self._commit())
        header.pack_end(self.done_button)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(6)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        self.title_entry: Gtk.Entry | None = None
        self.ephemeral_switch: Gtk.Switch | None = None

        if edit_mode:
            details = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            details.add_css_class("boxed-list")

            title_row = Adw.EntryRow(title="Title")
            title_row.set_text(ctx.title)
            title_row.connect("changed", lambda _e: self._update_labels())
            self.title_entry = title_row
            details.append(title_row)

            ephemeral_row = Adw.ActionRow(
                title="Ephemeral",
                subtitle="Discard this context after use",
            )
            self.ephemeral_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
            self.ephemeral_switch.set_active(ctx.ephemeral)
            ephemeral_row.add_suffix(self.ephemeral_switch)
            ephemeral_row.set_activatable_widget(self.ephemeral_switch)
            details.append(ephemeral_row)

            content.append(details)
        else:
            subtitle = Gtk.Label(
                label="Choose the apps to open in this context.",
                xalign=0.0,
                wrap=True,
            )
            subtitle.add_css_class("dim-label")
            content.append(subtitle)

        self.search = Gtk.SearchEntry(placeholder_text="Search installed apps…")
        self.search.connect("search-changed", lambda _e: self.refresh())
        content.append(self.search)

        self.count_label = Gtk.Label(xalign=0.0)
        self.count_label.add_css_class("heading")
        self.count_label.add_css_class("dim-label")
        content.append(self.count_label)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")

        self.empty_state = Adw.StatusPage(
            icon_name="system-search-symbolic",
            title="No matching apps",
            description="Try a different search term.",
        )
        self.empty_state.set_vexpand(True)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.listbox)

        self.stack = Gtk.Stack()
        self.stack.add_named(scroller, "list")
        self.stack.add_named(self.empty_state, "empty")
        content.append(self.stack)

        toolbar.set_content(content)
        self.set_child(toolbar)

        self.refresh()

    def _ordered(self) -> list[str]:
        known = [a.id for a in self.apps if a.id in self.selected]
        missing = [i for i in self.ctx.apps if i in self.selected and i not in set(known)]
        return known + missing

    def current_title(self) -> str:
        if self.title_entry is None:
            return self.ctx.title
        return self.title_entry.get_text().strip()

    def _commit(self) -> None:
        if self.edit_mode:
            title = self.current_title()
            if not title:
                return
            self.on_done(
                self.ctx,
                self._ordered(),
                title,
                bool(self.ephemeral_switch and self.ephemeral_switch.get_active()),
            )
        else:
            self.on_done(self.ctx, self._ordered())

    def _on_toggle(self, app: App, active: bool) -> None:
        if active:
            self.selected.add(app.id)
        else:
            self.selected.discard(app.id)
        self._update_labels()

    def _update_labels(self) -> None:
        count = len(self.selected)
        if self.edit_mode:
            self.done_button.set_label("Save")
            self.done_button.set_sensitive(bool(self.current_title()))
        else:
            self.done_button.set_label("Done" if count else "Skip")
        self.count_label.set_label(
            f"{count} app{'s' if count != 1 else ''} selected" if count else "No apps selected yet"
        )

    def refresh(self) -> None:
        matches = search_apps(self.apps, self.search.get_text())
        self.listbox.remove_all()
        for app in matches:
            self.listbox.append(AppRow(app, app.id in self.selected, self._on_toggle))
        self.stack.set_visible_child_name("list" if matches else "empty")
        self._update_labels()

    def visible_rows(self) -> list[AppRow]:
        rows = []
        row = self.listbox.get_first_child()
        while row is not None:
            if isinstance(row, AppRow):
                rows.append(row)
            row = row.get_next_sibling()
        return rows
