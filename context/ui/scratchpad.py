"""The scratchpad you type into, in the sidebar and in the overlay.

One widget for both. The sidebar's is small and the overlay's is large, and that
is the whole difference — the same text, saved the same way, so there is no
"real" copy and no version of it that can be behind the other.

Saving is a timer, not a button. It starts on the first keystroke and writes
once you stop; leaving, closing or switching writes immediately, since a
scratchpad that loses the last thing typed into it is worse than no scratchpad.
Each pad says when it was written, because an autosave with no sign of having
happened asks to be trusted before it has earned it.

`ScratchpadSection` is what views actually place. With a global scratchpad and a
context one both switched on it is either a switch between them or both at once,
depending on the setting — the pads themselves do not know which.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, Gtk

from context.state import scratchpad, settings
from context.state.scratchpad import GLOBAL, NoteStore
from context.system.logging_setup import get_logger

log = get_logger("scratchpad_view")

# How long typing has to stop before the note is written. Short enough that a
# crash costs a word, long enough that a sentence is one write rather than forty.
AUTOSAVE_MS = 600

# How long "Saved" stays up. Long enough to read, short enough that it is gone
# before it becomes furniture.
SAVED_MS = 2000

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
        only: str | None = None,
        height: int | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.store = store
        self.context_id = context_id or None
        self.context_title = context_title
        self.compact = compact
        self._save_source: int | None = None
        self._status_source: int | None = None
        self._loading = False

        # `available` reads both settings, so the sidebar and the overlay cannot
        # disagree about what exists; `preferred` is where you are, which is not
        # the same as where it sits in the row.
        offered = store.available(self.context_id)
        if only is not None:
            # Pinned to one pad: what a section showing both builds, and there
            # is nothing left to choose.
            self.offered = [only]
            self.showing = only
        else:
            self.offered = offered
            self.showing = store.preferred(self.context_id)

        header = Gtk.Box(spacing=6)
        if len(self.offered) > 1:
            from context.ui import widgets

            self.choice = widgets.SegmentedChoice(self._on_choice)
            for key in self.offered:
                self.choice.add(*self._face(key))
            self.choice.set_selected(self.offered.index(self.showing), notify=False)
            header.append(self.choice)
        else:
            self.choice = None
            label = Gtk.Label(xalign=0.0)
            label.add_css_class("heading" if only is not None else "dim-label")
            label.add_css_class("caption")
            label.set_label(self._face(self.showing)[0])
            header.append(label)

        self.status = Gtk.Label(xalign=1.0, hexpand=True)
        self.status.add_css_class("dim-label")
        self.status.add_css_class("caption")
        self.status.set_halign(Gtk.Align.END)
        header.append(self.status)

        if on_expand is not None:
            self.expand_button = Gtk.Button(
                icon_name="view-fullscreen-symbolic", valign=Gtk.Align.CENTER
            )
            self.expand_button.add_css_class("flat")
            self.expand_button.set_tooltip_text("Open the scratchpad with more room")
            self.expand_button.set_halign(Gtk.Align.END)
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

        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda _c: self.emit_focused())
        self.view.add_controller(focus)
        self.on_focused = None

        self.scroller = Gtk.ScrolledWindow(hexpand=True)
        self.scroller.add_css_class("ctx-note-scroll")
        self.scroller.set_child(self.view)
        if compact:
            self.scroller.set_size_request(
                -1, height or settings.current().scratchpad_height
            )
        else:
            self.scroller.set_vexpand(True)
        self.append(self.scroller)

        # Whatever is typed has to be written before the widget goes away, and
        # unmap is the only notice a sidebar section or a closing overlay gives.
        self.connect("unmap", lambda _w: self.flush())

        self._load()

    def _face(self, key: str) -> tuple[str, str]:
        if key == GLOBAL:
            return "Global", "The scratchpad that is always here"
        return (
            _short(self.context_title) if self.context_title else "Context",
            "This context's scratchpad",
        )

    def emit_focused(self) -> None:
        if self.on_focused is not None:
            self.on_focused(self)

    # -- the note ------------------------------------------------------------

    @property
    def body(self) -> str:
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, False)

    def _load(self) -> None:
        self._loading = True
        self.buffer.set_text(self.store.body(self.showing))
        self._loading = False
        self._say("")

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
        self._say("Saving…", clear_after=None)

    def _write(self) -> bool:
        """Write, and say whether anything actually changed on disk."""
        changed = self.body != self.store.body(self.showing)
        self.store.set_body(self.showing, self.body)
        return changed

    def _save(self) -> bool:
        self._save_source = None
        if self._write():
            self._say("Saved")
        else:
            self._say("")
        return False

    def flush(self) -> None:
        """Write now, rather than when the timer would have."""
        if self._save_source is not None:
            GLib.source_remove(self._save_source)
            self._save_source = None
        if self._write():
            self._say("Saved")

    def _say(self, text: str, clear_after: int | None = SAVED_MS) -> None:
        if self._status_source is not None:
            GLib.source_remove(self._status_source)
            self._status_source = None
        self.status.set_label(text)
        if text and clear_after:
            self._status_source = GLib.timeout_add(clear_after, self._clear_status)

    def _clear_status(self) -> bool:
        self._status_source = None
        self.status.set_label("")
        return False

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


class ScratchpadSection(Gtk.Box):
    """Whichever scratchpads belong on screen, however the setting says.

    One pad with a switch, or both at once. Which it is belongs here rather than
    in the pads: a pad shows one note and does not need to know how many others
    are beside it.
    """

    def __init__(
        self,
        store: NoteStore,
        context_id: str | None = None,
        context_title: str = "",
        on_expand=None,
        compact: bool = False,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.store = store
        self.context_id = context_id or None
        self.views: list[ScratchpadView] = []
        self._active: ScratchpadView | None = None

        offered = store.available(self.context_id)
        self.both = settings.current().scratchpad_show_both and len(offered) > 1
        # What a view compares against to decide whether to rebuild. The offered
        # list and the shape are both in it, so switching the setting or turning
        # a scratchpad off rebuilds rather than leaving a stale arrangement.
        self.signature = (self.context_id, tuple(offered), self.both)

        if self.both:
            for key in offered:
                view = ScratchpadView(
                    store,
                    context_id=self.context_id,
                    context_title=context_title,
                    on_expand=on_expand,
                    compact=compact,
                    only=key,
                )
                self._adopt(view)
        elif offered:
            self._adopt(
                ScratchpadView(
                    store,
                    context_id=self.context_id,
                    context_title=context_title,
                    on_expand=on_expand,
                    compact=compact,
                )
            )

    def _adopt(self, view: ScratchpadView) -> None:
        view.on_focused = self._on_focused
        self.views.append(view)
        self.append(view)
        if self._active is None:
            self._active = view

    def _on_focused(self, view: ScratchpadView) -> None:
        self._active = view

    @property
    def active(self) -> ScratchpadView | None:
        """The pad the editor's buttons act on: whichever was last typed in."""
        return self._active

    def matches(self, context_id: str | None) -> bool:
        offered = self.store.available(context_id or None)
        both = settings.current().scratchpad_show_both and len(offered) > 1
        return self.signature == (context_id or None, tuple(offered), both)

    def refresh(self) -> None:
        for view in self.views:
            view.refresh()

    def flush(self) -> None:
        for view in self.views:
            view.flush()


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
