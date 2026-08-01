"""An app grid, asked which application to open somewhere in particular.

The overview's grid answers "what shall I start?" by making a context. This one
answers "what shall I add to *this* context?" — same grid, same tiles, one
question narrower — and is reached from a context's menu.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from context.ui import sidebar, theme, widgets
from context.system.apps import App, installed_apps, search_apps
from context.system.logging_setup import get_logger
from context.ui.rows import app_tile

log = get_logger("app-picker")


class AppGridWindow(Gtk.ApplicationWindow):
    """Pick one installed application. `on_pick` gets it; Escape gets nothing."""

    def __init__(self, app, title: str, on_pick, subtitle: str = "") -> None:
        super().__init__(application=app, title=title)
        self.add_css_class("ctx-window")
        self.on_pick = on_pick
        self.apps = installed_apps()

        theme.install()
        self.set_default_size(900, 640)
        if not sidebar.apply_overlay(self):
            self.fullscreen()

        toolbar = widgets.ToolbarView()
        toolbar.add_css_class("ctx-surface")
        toolbar.add_css_class("ctx-solid")
        toolbar.set_overflow(Gtk.Overflow.HIDDEN)
        header = widgets.HeaderBar(title=title)
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        self.back_button = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_button.add_css_class("flat")
        self.back_button.set_tooltip_text("Back")
        self.back_button.connect("clicked", lambda _b: self._dismiss())
        header.pack_start(self.back_button)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for setter in (
            "set_margin_top",
            "set_margin_bottom",
            "set_margin_start",
            "set_margin_end",
        ):
            getattr(content, setter)(18)

        if subtitle:
            label = Gtk.Label(label=subtitle, xalign=0.0)
            label.add_css_class("dim-label")
            content.append(label)

        self.entry = Gtk.SearchEntry(placeholder_text="Search apps")
        self.entry.connect("search-changed", lambda _e: self.refresh())
        self.entry.connect("activate", lambda _e: self._pick_first())
        self.entry.connect("stop-search", lambda _e: self._dismiss())
        content.append(self.entry)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_max_children_per_line(30)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.flow)
        content.append(scroller)

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

        self.refresh()

    def refresh(self) -> None:
        self.flow.remove_all()
        for info in search_apps(self.apps, self.entry.get_text().strip()):
            self.flow.append(app_tile(info, self._pick, tooltip=f"Open {info.name}"))

    def _pick_first(self) -> None:
        child = self.flow.get_first_child()
        info = getattr(child, "app_info", None)
        if info is not None:
            self._pick(info)

    def _pick(self, info: App) -> None:
        log.info("picked %s", info.id)
        self.close()
        self.on_pick(info)

    def _dismiss(self) -> bool:
        self.close()
        return True
