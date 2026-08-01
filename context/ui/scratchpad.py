"""The scratchpad you type into, in the sidebar and in the overlay.

One widget for both. The sidebar's is small and the overlay's is large, and that
is the whole difference — the same text, saved the same way, so there is no
"real" copy and no version of it that can be behind the other.

Saving is a timer, not a button. It starts on the first keystroke and writes
once you stop; leaving, closing or switching writes immediately, since a
scratchpad that loses the last thing typed into it is worse than no scratchpad.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, Gtk

from context.state import scratchpad
from context.state.scratchpad import GLOBAL, NoteStore
from context.system.logging_setup import get_logger

log = get_logger("scratchpad_view")

# How long typing has to stop before the note is written. Short enough that a
# crash costs a word, long enough that a sentence is one write rather than forty.
AUTOSAVE_MS = 600

# How far one step of nesting moves a rendered line.
INDENT_PX = 18


class ScratchpadView(Gtk.Box):
    def __init__(
        self,
        store: NoteStore,
        context_id: str | None = None,
        context_title: str = "",
        on_expand=None,
        compact: bool = False,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.store = store
        self.context_id = context_id or None
        self.context_title = context_title
        self.compact = compact
        self._save_source: int | None = None
        self._loading = False

        # Which scratchpad is on screen. `available` reads both settings, so the
        # sidebar and the overlay cannot disagree about what exists, and its
        # first entry is where you are — the context's own, when there is one.
        self.offered = store.available(self.context_id)
        self.showing = self.offered[0] if self.offered else GLOBAL

        header = Gtk.Box(spacing=6)
        if len(self.offered) > 1:
            # Only when there is a choice to make. With one scratchpad the
            # switch is a control that does nothing, and in a sidebar that is a
            # row taken from the list for no reason.
            from context.ui import widgets

            self.choice = widgets.SegmentedChoice(self._on_choice)
            self.choice.add(
                _short(context_title) if context_title else "Context",
                tooltip="This context's scratchpad",
            )
            self.choice.add("Global", tooltip="The scratchpad that is always here")
            header.append(self.choice)
        else:
            self.choice = None
            label = Gtk.Label(xalign=0.0, hexpand=True)
            label.add_css_class("dim-label")
            label.add_css_class("caption")
            label.set_label("Global" if self.showing == GLOBAL else "This context")
            header.append(label)

        if on_expand is not None:
            self.expand_button = Gtk.Button(
                icon_name="view-fullscreen-symbolic", valign=Gtk.Align.CENTER
            )
            self.expand_button.add_css_class("flat")
            self.expand_button.set_tooltip_text("Open the scratchpad with more room")
            self.expand_button.set_halign(Gtk.Align.END)
            self.expand_button.set_hexpand(not bool(self.choice))
            self.expand_button.connect("clicked", lambda _b: self._expand(on_expand))
            header.append(self.expand_button)
        else:
            self.expand_button = None
        self.append(header)

        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.view.add_css_class("ctx-note-body")
        self.buffer = self.view.get_buffer()
        self.buffer.connect("changed", lambda _b: self._on_changed())

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.view.add_controller(keys)

        self.scroller = Gtk.ScrolledWindow(hexpand=True)
        self.scroller.add_css_class("ctx-note-scroll")
        self.scroller.set_child(self.view)
        if compact:
            self.scroller.set_size_request(-1, 132)
        else:
            self.scroller.set_vexpand(True)
        self.append(self.scroller)

        # Whatever is typed has to be written before the widget goes away, and
        # unmap is the only notice a sidebar section or a closing overlay gives.
        self.connect("unmap", lambda _w: self.flush())

        self._load()

    # -- the note ------------------------------------------------------------

    @property
    def body(self) -> str:
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, False)

    def _load(self) -> None:
        self._loading = True
        self.buffer.set_text(self.store.body(self.showing))
        self._loading = False

    def _on_choice(self, index: int) -> None:
        if index >= len(self.offered):
            return
        # The one on screen is written before moving off it, or switching would
        # be the one way to lose what was typed.
        self.flush()
        self.showing = self.offered[index]
        self._load()

    def refresh(self) -> None:
        """Re-read the note, for a view that is being reused.

        Only when nothing is being typed: the sidebar refreshes on a poll timer,
        and reloading the buffer under the cursor would eat the word in progress.
        """
        if self._save_source is not None or self.view.has_focus():
            return
        if self.body != self.store.body(self.showing):
            self._load()

    # -- saving --------------------------------------------------------------

    def _on_changed(self) -> None:
        if self._loading:
            return
        if self._save_source is not None:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(AUTOSAVE_MS, self._save)

    def _save(self) -> bool:
        self._save_source = None
        self.store.set_body(self.showing, self.body)
        return False

    def flush(self) -> None:
        """Write now, rather than when the timer would have."""
        if self._save_source is not None:
            GLib.source_remove(self._save_source)
            self._save_source = None
        self.store.set_body(self.showing, self.body)

    def _expand(self, on_expand) -> None:
        self.flush()
        on_expand(self.showing)

    # -- typing --------------------------------------------------------------

    def _current_line(self) -> int:
        return self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_line()

    def _place_at_end_of(self, line: int) -> None:
        where = self.buffer.get_iter_at_line(line)
        if isinstance(where, tuple):
            where = where[1]
        where.forward_to_line_end()
        self.buffer.place_cursor(where)

    def _on_key(self, _controller, keyval, _keycode, state) -> bool:
        if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return False
        if state & Gdk.ModifierType.CONTROL_MASK:
            self.toggle_current_line()
            return True

        lines = self.body.split("\n")
        index = self._current_line()
        if not 0 <= index < len(lines):
            return False
        marker = scratchpad.continuation(lines[index])
        if not marker:
            # An empty list item ends the list: clear the marker rather than
            # carrying it down another line forever.
            if lines[index].strip() in ("-", "*", "- [ ]", "- [x]"):
                start = self.buffer.get_iter_at_line(index)
                if isinstance(start, tuple):
                    start = start[1]
                end = start.copy()
                end.forward_to_line_end()
                self.buffer.delete(start, end)
            return False
        self.buffer.insert_at_cursor(f"\n{marker}")
        return True

    def toggle_current_line(self) -> None:
        line = self._current_line()
        updated = scratchpad.toggle(self.body, line)
        if updated == self.body:
            return
        self.set_body(updated)
        self._place_at_end_of(line)

    def set_line_kind(self, kind: str) -> None:
        line = self._current_line()
        updated = scratchpad.set_kind(self.body, line, kind)
        if updated == self.body:
            return
        self.set_body(updated)
        self._place_at_end_of(line)
        self.view.grab_focus()

    def set_body(self, text: str) -> None:
        """Replace the text, as an edit rather than a load, so it is saved."""
        self.buffer.set_text(text)


def _short(title: str, limit: int = 14) -> str:
    return title if len(title) <= limit else title[: limit - 1] + "…"


def checklist(body: str, on_toggle) -> Gtk.Box:
    """The same note drawn as widgets, where a checkbox is something you click."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    lines = scratchpad.parse(body)
    if not lines or not any(line.text for line in lines):
        empty = Gtk.Label(label="Nothing written yet.", xalign=0.0)
        empty.add_css_class("dim-label")
        box.append(empty)
        return box

    for index, line in enumerate(lines):
        if line.is_box:
            widget = Gtk.CheckButton(label=line.text, active=line.checked)
            widget.connect("toggled", lambda _b, i=index: on_toggle(i))
        elif line.kind == scratchpad.BULLET:
            widget = Gtk.Label(label=f"•  {line.text}", xalign=0.0, wrap=True)
        else:
            widget = Gtk.Label(label=line.text or " ", xalign=0.0, wrap=True)
        widget.set_margin_start(line.indent * INDENT_PX)
        box.append(widget)
    return box
