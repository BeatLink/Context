"""The scratchpad as its own overlay, the shape the editor uses.

The sidebar holds a scratchpad you can type into directly; this is the same one
with room. A layer-shell surface over the whole output, above the bars and
outside the tiling, the way editing a context opens.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from context.state.scratchpad import NoteStore
from context.ui import sidebar, theme, widgets
from context.ui.note_editor import NoteEditorPage


class NoteWindow(Gtk.Window):
    def __init__(
        self,
        app: Gtk.Application,
        store: NoteStore,
        on_done=None,
        context_id: str | None = None,
        context_title: str = "",
        showing: str | None = None,
    ) -> None:
        super().__init__(application=app, title="Scratchpad")
        self.add_css_class("ctx-window")
        self.on_done = on_done

        theme.install()
        self.set_default_size(1000, 720)
        self.set_modal(False)
        if not sidebar.apply_overlay(self):
            self.fullscreen()

        self.nav = widgets.NavigationView()
        self.nav.add_css_class("ctx-surface")
        self.nav.add_css_class("ctx-solid")
        self.nav.set_overflow(Gtk.Overflow.HIDDEN)
        self.page = NoteEditorPage(
            store,
            on_done=self._finish,
            context_id=context_id,
            context_title=context_title,
            showing=showing,
        )
        self.nav.add(self.page)
        self.set_child(self.nav)

        escape = Gtk.ShortcutController()
        escape.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.CallbackAction.new(lambda *_a: self._on_escape()),
            )
        )
        self.add_controller(escape)

    def _on_escape(self) -> bool:
        # Escape saves rather than discarding. A scratchpad that loses what was
        # typed because the wrong key ended it is not a scratchpad.
        self.page._finish()
        return True

    def _finish(self) -> None:
        self.close()
        if self.on_done is not None:
            self.on_done()
