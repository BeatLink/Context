"""A note as its own overlay, the shape the editor uses.

The sidebar lists notes; writing one needs room the sidebar does not have, so it
opens the same way editing a context does — a layer-shell surface over the whole
output, above the bars and outside the tiling.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from context.state import scratchpad
from context.state.scratchpad import Note, NoteStore
from context.ui import sidebar, theme, widgets
from context.ui.note_editor import NoteEditorPage


class NoteWindow(Gtk.Window):
    def __init__(
        self,
        app: Gtk.Application,
        store: NoteStore,
        note: Note,
        on_done=None,
        context_id: str = scratchpad.GLOBAL,
        context_title: str = "",
    ) -> None:
        super().__init__(application=app, title=note.title or "Note")
        self.add_css_class("ctx-window")
        self.on_done = on_done

        theme.install()
        self.set_default_size(1100, 780)
        self.set_modal(False)
        if not sidebar.apply_overlay(self):
            self.fullscreen()

        self.nav = widgets.NavigationView()
        self.nav.add_css_class("ctx-surface")
        self.nav.add_css_class("ctx-solid")
        self.nav.set_overflow(Gtk.Overflow.HIDDEN)
        self.page = NoteEditorPage(
            store,
            note,
            on_done=lambda n: self._finish(n),
            on_delete=lambda n: self._finish(n),
            context_id=context_id,
            context_title=context_title,
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
        # typed because the wrong key ended it is not a scratchpad, and there is
        # nothing to discard *to* — every version is kept anyway.
        self.page._finish()
        return True

    def _finish(self, note: Note) -> None:
        self.close()
        if self.on_done is not None:
            self.on_done(note)
