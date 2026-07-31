"""Configuring what a selected app should open."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from .adapters import supports_paths, supports_profiles
from .apps import App
from .resources import PROFILE_DEDICATED, PROFILE_MAIN, Resource, split_urls


class ResourcePage(Adw.NavigationPage):
    """Edit the URLs a resource opens with."""

    def __init__(self, app: App, resource: Resource, on_done) -> None:
        super().__init__(title=app.name)
        self.app = app
        self.resource = resource
        self.on_done = on_done

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.done_button = Gtk.Button(label="Done")
        self.done_button.add_css_class("suggested-action")
        self.done_button.connect("clicked", lambda _b: self._commit())
        header.pack_end(self.done_button)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(6)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        heading = Gtk.Label(
            label=f"What should {app.name} open?",
            xalign=0.0,
            wrap=True,
        )
        heading.add_css_class("title-4")
        content.append(heading)

        hint = Gtk.Label(
            label="One URL per line. Each opens as a tab in this context's window.",
            xalign=0.0,
            wrap=True,
        )
        hint.add_css_class("dim-label")
        content.append(hint)

        # Path pickers, for apps that open a folder, file or workspace.
        self.path_row: Adw.ActionRow | None = None
        if supports_paths(resource):
            hint.set_label(
                "Open a folder, a file, or a .code-workspace. "
                "The window opens on whatever is chosen."
            )
            targets = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            targets.add_css_class("boxed-list")

            self.path_row = Adw.ActionRow(title="Opens")
            self.path_row.set_subtitle(resource.path or "nothing yet")
            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            for label, mode in (
                ("Folder…", "folder"),
                ("File…", "file"),
                ("Workspace…", "workspace"),
            ):
                button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
                button.add_css_class("flat")
                button.connect("clicked", lambda _b, m=mode: self._choose_path(m))
                buttons.append(button)
            self.path_row.add_suffix(buttons)
            targets.append(self.path_row)
            content.append(targets)

        self.main_profile_switch: Gtk.Switch | None = None
        if supports_profiles(resource):
            options = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            options.add_css_class("boxed-list")

            row = Adw.ActionRow(
                title="Use my main profile",
                subtitle=(
                    "Opens in your existing browser, keeping addons, logins and "
                    "history. Tabs are not kept separate between contexts."
                ),
            )
            self.main_profile_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
            self.main_profile_switch.set_active(resource.uses_main_profile)
            row.add_suffix(self.main_profile_switch)
            row.set_activatable_widget(self.main_profile_switch)
            options.append(row)
            content.append(options)

        frame = Gtk.Frame()
        self.text = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=8,
            bottom_margin=8,
            left_margin=8,
            right_margin=8,
        )
        self.text.get_buffer().set_text("\n".join(resource.urls))
        self.text.get_buffer().connect("changed", lambda _b: self._update_count())
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.text)
        frame.set_child(scroller)
        content.append(frame)

        self.count_label = Gtk.Label(xalign=0.0)
        self.count_label.add_css_class("dim-label")
        content.append(self.count_label)

        toolbar.set_content(content)
        self.set_child(toolbar)

        self._update_count()

    def current_text(self) -> str:
        buffer = self.text.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def current_urls(self) -> list[str]:
        return split_urls(self.current_text())

    def _update_count(self) -> None:
        count = len(self.current_urls())
        self.count_label.set_label(
            f"{count} URL{'s' if count != 1 else ''}" if count else "No URLs yet"
        )

    def _choose_path(self, mode: str) -> None:
        """Pick a folder, a file, or a .code-workspace."""
        dialog = Gtk.FileDialog(title=f"Choose a {mode}")

        if mode == "workspace":
            workspace_filter = Gtk.FileFilter()
            workspace_filter.set_name("VS Code workspaces")
            workspace_filter.add_pattern("*.code-workspace")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(workspace_filter)
            dialog.set_filters(filters)
            dialog.set_default_filter(workspace_filter)

        def done(source, result):
            try:
                chosen = (
                    source.select_folder_finish(result)
                    if mode == "folder"
                    else source.open_finish(result)
                )
            except GLib.Error:
                return  # Cancelled.
            if chosen is not None:
                self.resource.path = chosen.get_path()
                if self.path_row is not None:
                    self.path_row.set_subtitle(self.resource.path or "nothing yet")

        root = self.get_root()
        if mode == "folder":
            dialog.select_folder(root, None, done)
        else:
            dialog.open(root, None, done)

    def _commit(self) -> None:
        self.resource.urls = self.current_urls()
        if self.main_profile_switch is not None:
            self.resource.profile_mode = (
                PROFILE_MAIN if self.main_profile_switch.get_active() else PROFILE_DEDICATED
            )
        self.on_done(self.resource)
