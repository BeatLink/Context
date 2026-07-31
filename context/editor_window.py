"""The editor as its own full-screen window.

The launcher is a narrow docked sidebar, which is the wrong shape for arranging a
layout — the preview needs the monitor's aspect ratio to be meaningful, and the app
grid needs room for several columns. So the editor opens as a separate window
covering the screen rather than as a page inside the sidebar.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from . import sidebar, widgets
from .editor import EditorPage
from .store import Context


class EditorWindow(Gtk.Window):
    def __init__(
        self,
        app: Gtk.Application,
        ctx: Context,
        on_done,
        on_cancel,
        on_delete=None,
        is_new: bool = False,
    ) -> None:
        super().__init__(application=app, title=ctx.title or "New context")
        self.add_css_class("ctx-window")

        self.set_default_size(1280, 860)
        self.set_modal(False)

        # An overlay rather than a window, the way rofi behaves: a layer-shell
        # surface on the overlay layer covers the whole output, sits above the
        # bars, and is never tiled into the workspace. Falls back to a fullscreen
        # window where layer-shell is unavailable.
        if not sidebar.apply_overlay(self):
            self.fullscreen()

        self.nav = widgets.NavigationView()
        self.page = EditorPage(
            ctx,
            lambda *args: self._finish(on_done, *args),
            lambda: self._finish(on_cancel),
            on_delete=(lambda c: self._finish(on_delete, c)) if on_delete else None,
            is_new=is_new,
        )
        self.nav.add(self.page)
        # Dialogs draw into the nearest overlay. Without one the editor could
        # not ask anything: the forget confirmation answered itself with its
        # default response, so the button appeared to do nothing.
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.nav)
        self.set_child(self.overlay)

        # Escape backs out of a pushed page, or closes the editor from the top.
        escape = Gtk.ShortcutController()
        escape.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.CallbackAction.new(lambda *_a: self._on_escape()),
            )
        )
        self.add_controller(escape)

    def _on_escape(self) -> bool:
        if self.nav.get_visible_page() is not self.page:
            self.nav.pop()
        else:
            self.page._commit_cancel()
        return True

    def _finish(self, callback, *args) -> None:
        self.close()
        if callback is  not None:
            callback(*args)
