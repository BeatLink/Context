"""The application catalogue, asked which one to open somewhere in particular.

The overview answers "what shall I start?" by making a context. This one
answers "what shall I add to *this* context?" — one question narrower — and is
reached from a context's menu.

The same `AppCatalogue` both times, so an application is found the same way
wherever it is being looked for. This was a flow of tiles with a search box and
nothing else: no filter by kind, no ordering, and a name you half-remembered
meant scrolling.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from context.ui import sidebar, theme, widgets
from context.system.apps import App
from context.system.logging_setup import get_logger
from context.ui.catalogue import AppCatalogue
from context.ui.rows import AddAppRow

log = get_logger("app-picker")


class AppGridWindow(Gtk.ApplicationWindow):
    """Pick one installed application. `on_pick` gets it; Escape gets nothing."""

    def __init__(self, app, title: str, on_pick, subtitle: str = "") -> None:
        super().__init__(application=app, title=title)
        self.add_css_class("ctx-window")
        self.on_pick = on_pick
        self.where = title

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

        self.catalogue = AppCatalogue(self._row, placeholder="Search apps")
        # The catalogue owns the search box; what Enter and Escape mean in it
        # belong to the screen around it.
        self.catalogue.entry.connect("activate", lambda _e: self._pick_first())
        self.catalogue.entry.connect("stop-search", lambda _e: self._dismiss())
        content.append(self.catalogue)

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

    def _row(self, info: App) -> AddAppRow:
        """One answer, since the context is already chosen — the overview is
        the only screen that also asks *where*."""
        return AddAppRow(
            info,
            self._pick,
            tooltip=f"Open {info.name} here",
            icon_name="media-playback-start-symbolic",
        )

    def refresh(self) -> None:
        self.catalogue.refresh()

    def _pick_first(self) -> None:
        row = self.catalogue.first()
        if row is not None:
            self._pick(row.app_info)

    def _pick(self, info: App) -> None:
        log.info("picked %s", info.id)
        self.close()
        self.on_pick(info)

    def _dismiss(self) -> bool:
        self.close()
        return True
