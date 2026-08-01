"""Settings as a screen of its own.

They outgrew the sidebar: twenty-odd controls stacked in a 380px column meant
scrolling past three groups to reach the fourth, and every description wrapped
to three lines. The page itself is unchanged — it is the same
`SettingsPage` the sidebar used to push — but it is hosted in a full-screen
overlay, the same shape as the editor and the overview.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from context.ui import sidebar, theme, widgets
from context.system.logging_setup import get_logger
from context.ui.settings_page import SettingsPage

log = get_logger("settings_window")


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, app, launcher) -> None:
        super().__init__(application=app, title="Settings")
        self.add_css_class("ctx-window")

        theme.install()
        self.set_default_size(900, 760)
        if not sidebar.apply_overlay(self):
            self.fullscreen()

        self.nav = widgets.NavigationView()
        self.nav.add_css_class("ctx-surface")
        self.nav.add_css_class("ctx-solid")
        self.nav.set_overflow(Gtk.Overflow.HIDDEN)
        self.page = SettingsPage(launcher, on_back=lambda: self.close())
        self.nav.add(self.page)
        self.set_child(self.nav)

        escape = Gtk.ShortcutController()
        escape.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.CallbackAction.new(lambda *_a: self._dismiss()),
            )
        )
        self.add_controller(escape)

    def _dismiss(self) -> bool:
        self.close()
        return True
