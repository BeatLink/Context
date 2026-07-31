"""Configuring what a selected app should open."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib, Gtk

from . import sidebar, widgets
from .adapters import supports_command, supports_paths, supports_profiles
from .apps import App
from .resources import PROFILE_DEDICATED, PROFILE_MAIN, Resource, normalize_url


class ResourcePage(widgets.NavigationPage):
    """Edit the URLs a resource opens with."""

    def __init__(self, app: App, resource: Resource, on_done) -> None:
        super().__init__(title=app.name)
        self.app = app
        self.resource = resource
        self.on_done = on_done

        toolbar = widgets.ToolbarView()
        header = widgets.HeaderBar()

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
        self.path_row: widgets.ActionRow | None = None
        if supports_paths(resource):
            hint.set_label(
                "Open a folder, a file, or a .code-workspace. "
                "The window opens on whatever is chosen."
            )
            targets = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            targets.add_css_class("boxed-list")

            self.path_row = widgets.ActionRow(title="Opens")
            self.path_row.set_subtitle(resource.path or "nothing chosen")
            self.path_row.set_subtitle_lines(2)

            clear = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER)
            clear.add_css_class("flat")
            clear.set_tooltip_text("Clear")
            clear.connect("clicked", lambda _b: self._set_path(None))
            self.path_row.add_suffix(clear)
            targets.append(self.path_row)

            # A terminal only makes sense opened at a directory; an editor also
            # takes a file or a workspace, so the choices differ per adapter.
            modes = [("Folder…", "folder")]
            if not supports_command(resource):
                modes += [("File…", "file"), ("Workspace…", "workspace")]

            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            buttons.set_halign(Gtk.Align.START)
            for label, mode in modes:
                button = Gtk.Button(label=label)
                button.connect("clicked", lambda _b, m=mode: self._choose_path(m))
                buttons.append(button)

            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            section.append(targets)
            section.append(buttons)
            content.append(section)

        self.command_row: widgets.EntryRow | None = None
        if supports_command(resource):
            command_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            command_list.add_css_class("boxed-list")
            self.command_row = widgets.EntryRow(title="Run a command")
            self.command_row.set_text(resource.command or "")
            command_list.append(self.command_row)
            content.append(command_list)

        # Phrased as the departure from the default rather than as the default,
        # so the switch is off until the user asks for something.
        self.dedicated_profile_switch: Gtk.Switch | None = None
        if supports_profiles(resource):
            options = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            options.add_css_class("boxed-list")

            row = widgets.ActionRow(
                title="Give this context its own profile",
                subtitle=(
                    "Keeps its tabs, cookies and history separate and restores "
                    "them on reopen. Your addons and logins are not carried over."
                ),
            )
            self.dedicated_profile_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
            self.dedicated_profile_switch.set_active(not resource.uses_main_profile)
            row.add_suffix(self.dedicated_profile_switch)
            row.set_activatable_widget(self.dedicated_profile_switch)
            options.append(row)
            content.append(options)

        # Compatibility. Apps differ in how they behave when already running, and
        # there is no reliable way to detect which, so both are exposed.
        compat = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        compat.add_css_class("boxed-list")

        new_window_row = widgets.ActionRow(
            title="Open a new window",
            subtitle="Off if the app should reuse a window it already has",
        )
        self.new_window_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.new_window_switch.set_active(resource.force_new_window)
        new_window_row.add_suffix(self.new_window_switch)
        new_window_row.set_activatable_widget(self.new_window_switch)
        compat.append(new_window_row)

        single_row = widgets.ActionRow(
            title="Single instance only",
            subtitle="The app refuses to run twice, so its existing window is used",
        )
        self.single_instance_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.single_instance_switch.set_active(resource.single_instance)
        single_row.add_suffix(self.single_instance_switch)
        single_row.set_activatable_widget(self.single_instance_switch)
        compat.append(single_row)

        isolate_row = widgets.ActionRow(
            title="Isolate in this context",
            subtitle=(
                "Only applies to isolated contexts. Off for an app that shares "
                "a database with another context, which two copies must not "
                "write at once"
            ),
        )
        self.isolate_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.isolate_switch.set_active(resource.isolate)
        isolate_row.add_suffix(self.isolate_switch)
        isolate_row.set_activatable_widget(self.isolate_switch)
        compat.append(isolate_row)
        content.append(compat)

        # URLs as a list of rows rather than a text box: each is separately
        # removable, and a typo in one does not require re-reading the rest.
        self.url_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.url_list.add_css_class("boxed-list")

        add_url = Gtk.Button(label="Add a URL", halign=Gtk.Align.START)
        add_url.add_css_class("flat")
        add_url.connect("clicked", lambda _b: self._add_url(""))

        url_scroller = Gtk.ScrolledWindow(vexpand=True)
        url_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        url_scroller.set_child(self.url_list)

        self.url_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.url_section.append(url_scroller)
        self.url_section.append(add_url)
        content.append(self.url_section)

        self.count_label = Gtk.Label(xalign=0.0)
        self.count_label.add_css_class("dim-label")
        content.append(self.count_label)

        # After count_label exists: adding a row updates it.
        for url in resource.urls:
            self._add_url(url)

        # Terminals and editors take a path, not URLs.
        self.url_section.set_visible(not supports_paths(resource))
        self.count_label.set_visible(not supports_paths(resource))

        toolbar.set_content(content)
        self.set_child(toolbar)

        self._update_count()

    def url_rows(self) -> list:
        rows = []
        row = self.url_list.get_first_child()
        while row is not None:
            if hasattr(row, "entry"):
                rows.append(row)
            row = row.get_next_sibling()
        return rows

    def current_urls(self) -> list[str]:
        return [
            normalize_url(r.entry.get_text().strip())
            for r in self.url_rows()
            if r.entry.get_text().strip()
        ]

    def _add_url(self, value: str) -> None:
        row = widgets.EntryRow(title="URL")
        row.set_text(value)
        row.connect("changed", lambda _e: self._update_count())

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.connect("clicked", lambda _b: self._remove_url(row))
        row.add_suffix(remove)

        self.url_list.append(row)
        self._update_count()

    def _remove_url(self, row) -> None:
        self.url_list.remove(row)
        self._update_count()

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

        root = self.get_root()
        # The chooser is an ordinary toplevel and the editor is a layer-shell
        # overlay, which the compositor draws above every ordinary window. Left
        # up, the chooser is hidden behind it and cannot be answered.
        suspended = sidebar.suspend_overlay(root) if root is not None else False

        def done(source, result):
            try:
                chosen = (
                    source.select_folder_finish(result)
                    if mode == "folder"
                    else source.open_finish(result)
                )
            except GLib.Error:
                chosen = None  # Cancelled.
            finally:
                if suspended:
                    sidebar.resume_overlay(root)
            if chosen is not None:
                self._set_path(chosen.get_path())

        parent = None if suspended else root
        if mode == "folder":
            dialog.select_folder(parent, None, done)
        else:
            dialog.open(parent, None, done)

    def _set_path(self, path: str | None) -> None:
        self.resource.path = path
        if self.path_row is not None:
            self.path_row.set_subtitle(path or "nothing chosen")

    def _commit(self) -> None:
        self.resource.urls = self.current_urls()
        self.resource.force_new_window = self.new_window_switch.get_active()
        self.resource.single_instance = self.single_instance_switch.get_active()
        self.resource.isolate = self.isolate_switch.get_active()
        if self.command_row is not None:
            self.resource.command = self.command_row.get_text().strip() or None
        if self.dedicated_profile_switch is not None:
            self.resource.profile_mode = (
                PROFILE_DEDICATED
                if self.dedicated_profile_switch.get_active()
                else PROFILE_MAIN
            )
        self.on_done(self.resource)
