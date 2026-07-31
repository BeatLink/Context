"""The editor as its own full-screen window.

The launcher is a narrow docked sidebar, which is the wrong shape for arranging a
layout — the preview needs the monitor's aspect ratio to be meaningful, and the app
grid needs room for several columns. So the editor opens as a separate window
covering the screen rather than as a page inside the sidebar.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from .editor import EditorPage
from .store import Context


class EditorWindow(Adw.Window):
    def __init__(
        self,
        app: Adw.Application,
        ctx: Context,
        on_done,
        on_cancel,
        is_new: bool = False,
    ) -> None:
        super().__init__(application=app, title=ctx.title or "New context")

        self.set_default_size(1280, 860)
        self.set_modal(False)
        # Genuinely fullscreen rather than maximised: under a tiling compositor a
        # maximised window still shares the workspace and sits inside the bars,
        # which leaves the layout preview competing with whatever else is open.
        self.fullscreen()

        self.nav = Adw.NavigationView()
        self.page = EditorPage(
            ctx,
            lambda *args: self._finish(on_done, *args),
            lambda: self._finish(on_cancel),
            is_new=is_new,
        )
        self.nav.add(self.page)
        self.set_content(self.nav)

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
        if callback is not None:
            callback(*args)
