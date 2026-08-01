"""Settings as a window.

They outgrew the sidebar first — twenty-odd controls stacked in a 380px column
meant scrolling past three groups to reach the fourth — and became a full-screen
overlay, which is a surface made of the one thing settings cannot use. A
layer-shell overlay throws away the click that lands in a popover, so every
choice on this page had to be a row of buttons rather than a dropdown, and the
day settings moved onto one, every combo in the application was dead until
somebody tried one.

So: an ordinary window. It is a task with duration, made entirely of controls,
and it is opened, changed and closed — which is what a window is for. Popovers
work here.

It floats rather than tiling, by a rule the application installs. Tiled, it
would join the layout of whatever context you are standing in — re-tiling that
context's windows and drifting it — for a visit measured in seconds.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from context.ui import theme, widgets
from context.system.logging_setup import get_logger
from context.ui.settings_page import SettingsPage

log = get_logger("settings_window")

# What the window asks for, and what the float rule sizes it to. One pair, so
# the compositor and the toolkit cannot disagree about how big it is.
SIZE = (900, 760)


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, app, launcher) -> None:
        super().__init__(application=app, title="Settings")
        self.add_css_class("ctx-window")

        theme.install()
        self.set_default_size(*SIZE)

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
