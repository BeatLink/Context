"""Writing a note, and moving around its history.

Two views of one body. **Write** is the text itself, markers and all, because
the format is plain text and hiding that would make it harder to edit rather
than easier. **Checklist** is the same body drawn as widgets, where a checkbox
is something you click. Neither is a different document; the text is the note
and the checklist is a rendering of it.

The history is the other half of the page. Selecting an old version shows it and
records it as what the next save was written *from* — so editing an old version
appends a new one rather than rewinding to it. Nothing on the list can be lost
by anything done here, which is what makes wandering through it safe.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk

from context.state import scratchpad
from context.state.scratchpad import Note, NoteStore, Version
from context.ui import widgets
from context.ui.rows import relative_time
from context.system.logging_setup import get_logger

log = get_logger("note_editor")

WRITE, CHECKLIST = 0, 1

# How far one step of nesting moves a rendered line.
INDENT_PX = 18


class NoteEditorPage(widgets.NavigationPage):
    def __init__(
        self,
        store: NoteStore,
        note: Note,
        on_done,
        on_delete=None,
        context_id: str = scratchpad.GLOBAL,
        context_title: str = "",
    ) -> None:
        super().__init__(title=note.title or "Note", tag="note")
        self.store = store
        self.note = note
        self.on_done = on_done
        self.on_delete = on_delete
        self.context_id = context_id
        self.context_title = context_title
        # Which version the text on screen came from, and therefore what the
        # next save records as its base. The tip while writing normally.
        tip = note.current
        self.base = tip.number if tip else 0
        self._loading = False

        toolbar = widgets.ToolbarView()
        header = widgets.HeaderBar(title="Note")
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        self.back_button = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_button.add_css_class("flat")
        self.back_button.set_tooltip_text("Save and close")
        self.back_button.connect("clicked", lambda _b: self._finish())
        header.pack_start(self.back_button)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (
            "set_margin_top",
            "set_margin_bottom",
            "set_margin_start",
            "set_margin_end",
        ):
            getattr(content, setter)(18)

        self.title_entry = Gtk.Entry(placeholder_text="Title")
        self.title_entry.set_text(note.title)
        self.title_entry.add_css_class("title-3")
        content.append(self.title_entry)

        columns = Gtk.Box(spacing=18)
        columns.set_vexpand(True)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_hexpand(True)

        controls = Gtk.Box(spacing=8)
        self.mode = widgets.SegmentedChoice(self._on_mode)
        self.mode.add(Gtk.Label(label="Write"), tooltip="The text, markers and all")
        self.mode.add(
            Gtk.Label(label="Checklist"), tooltip="The same note, drawn to click"
        )
        controls.append(self.mode)

        self.format_bar = Gtk.Box(spacing=6)
        self.format_bar.set_halign(Gtk.Align.END)
        self.format_bar.set_hexpand(True)
        for label, tooltip, kind in (
            ("Bullet", "Make this line a bullet", scratchpad.BULLET),
            ("Checkbox", "Make this line a checkbox", scratchpad.UNCHECKED),
            ("Plain", "Make this line plain text", scratchpad.TEXT),
        ):
            button = Gtk.Button(label=label)
            button.add_css_class("flat")
            button.set_tooltip_text(tooltip)
            button.connect("clicked", lambda _b, k=kind: self._set_line_kind(k))
            self.format_bar.append(button)
        controls.append(self.format_bar)
        left.append(controls)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)

        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.view.add_css_class("ctx-note-body")
        self.view.set_monospace(False)
        self.buffer = self.view.get_buffer()
        self.buffer.set_text(note.body)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.view.add_controller(keys)
        writing = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        writing.set_child(self.view)
        writing.add_css_class("ctx-note-scroll")
        self.stack.add_named(writing, "write")

        self.checklist = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        checking = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        checking.set_child(self.checklist)
        checking.add_css_class("ctx-note-scroll")
        self.stack.add_named(checking, "checklist")
        left.append(self.stack)

        self.status = Gtk.Label(xalign=0.0)
        self.status.add_css_class("dim-label")
        self.status.add_css_class("caption")
        left.append(self.status)
        columns.append(left)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_size_request(280, -1)

        history_label = Gtk.Label(label="History", xalign=0.0)
        history_label.add_css_class("heading")
        history_label.add_css_class("dim-label")
        right.append(history_label)

        self.history = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.history.add_css_class("boxed-list")
        history_scroll = Gtk.ScrolledWindow(vexpand=True)
        history_scroll.set_child(self.history)
        right.append(history_scroll)

        self.restore_button = Gtk.Button(label="Restore this version")
        self.restore_button.set_tooltip_text(
            "Write this version's text as a new version. Nothing after it is lost."
        )
        self.restore_button.connect("clicked", lambda _b: self._restore())
        self.restore_button.set_visible(False)
        right.append(self.restore_button)

        if self.context_id:
            self.where = widgets.SegmentedChoice(self._on_where)
            self.where.add(Gtk.Label(label="Global"), tooltip="Listed everywhere")
            self.where.add(
                Gtk.Label(label=context_title or "This context"),
                tooltip="Listed only in this context",
            )
            self.where.set_selected(0 if note.is_global else 1, notify=False)
            where_label = Gtk.Label(label="Belongs to", xalign=0.0)
            where_label.add_css_class("heading")
            where_label.add_css_class("dim-label")
            right.append(where_label)
            right.append(self.where)
        else:
            self.where = None

        if on_delete is not None:
            self.delete_button = Gtk.Button(label="Delete note…")
            self.delete_button.add_css_class("destructive-action")
            self.delete_button.connect("clicked", lambda _b: self._ask_delete())
            right.append(self.delete_button)

            self.confirm_button = Gtk.Button(label="Really delete")
            self.confirm_button.add_css_class("destructive-action")
            self.confirm_button.connect("clicked", lambda _b: self._delete())
            self.confirm_button.set_visible(False)
            right.append(self.confirm_button)

            self.keep_button = Gtk.Button(label="Keep")
            self.keep_button.connect("clicked", lambda _b: self._ask_delete(False))
            self.keep_button.set_visible(False)
            right.append(self.keep_button)

        columns.append(right)
        content.append(columns)
        toolbar.set_content(content)
        self.set_child(toolbar)

        self._rebuild_history()
        self._sync_status()

    # -- the body ------------------------------------------------------------

    @property
    def body(self) -> str:
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, False)

    def _set_body(self, text: str) -> None:
        self._loading = True
        self.buffer.set_text(text)
        self._loading = False
        self._rebuild_checklist()

    def _on_mode(self, index: int) -> None:
        if index == CHECKLIST:
            self._rebuild_checklist()
        self.stack.set_visible_child_name("write" if index == WRITE else "checklist")
        self.format_bar.set_visible(index == WRITE)

    def _current_line(self) -> int:
        return self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_line()

    def _set_line_kind(self, kind: str) -> None:
        line = self._current_line()
        updated = scratchpad.set_kind(self.body, line, kind)
        if updated == self.body:
            return
        self._set_body(updated)
        # Put the cursor back on the line that was just changed, at its end, so
        # the button does not send the caret to the top of the note.
        where = self.buffer.get_iter_at_line(line)
        if isinstance(where, tuple):
            where = where[1]
        where.forward_to_line_end()
        self.buffer.place_cursor(where)
        self.view.grab_focus()

    def _on_key(self, _controller, keyval, _keycode, state) -> bool:
        if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return False
        if state & Gdk.ModifierType.CONTROL_MASK:
            self._toggle_current_line()
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

    def _toggle_current_line(self) -> None:
        updated = scratchpad.toggle(self.body, self._current_line())
        if updated != self.body:
            line = self._current_line()
            self._set_body(updated)
            where = self.buffer.get_iter_at_line(line)
            if isinstance(where, tuple):
                where = where[1]
            where.forward_to_line_end()
            self.buffer.place_cursor(where)

    def _rebuild_checklist(self) -> None:
        child = self.checklist.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.checklist.remove(child)
            child = following

        lines = scratchpad.parse(self.body)
        if not lines or not any(line.text for line in lines):
            empty = Gtk.Label(label="Nothing written yet.", xalign=0.0)
            empty.add_css_class("dim-label")
            self.checklist.append(empty)
            return

        for index, line in enumerate(lines):
            if line.is_box:
                widget = Gtk.CheckButton(label=line.text, active=line.checked)
                widget.connect("toggled", lambda _b, i=index: self._toggle(i))
            elif line.kind == scratchpad.BULLET:
                widget = Gtk.Label(label=f"•  {line.text}", xalign=0.0, wrap=True)
            else:
                widget = Gtk.Label(label=line.text, xalign=0.0, wrap=True)
                if not line.text:
                    widget.set_label(" ")
            widget.set_margin_start(line.indent * INDENT_PX)
            self.checklist.append(widget)

    def _toggle(self, index: int) -> None:
        if self._loading:
            return
        updated = scratchpad.toggle(self.body, index)
        if updated == self.body:
            return
        self._loading = True
        self.buffer.set_text(updated)
        self._loading = False

    # -- history -------------------------------------------------------------

    def _rebuild_history(self) -> None:
        self.history.remove_all()
        tip = self.note.current
        for version in reversed(self.note.versions):
            self.history.append(self._version_row(version, is_tip=version is tip))
        self.restore_button.set_visible(
            tip is not None and self.base != tip.number
        )

    def _version_row(self, version: Version, is_tip: bool) -> widgets.ActionRow:
        row = widgets.ActionRow()
        row.set_activatable(True)
        label = f"Version {version.number}"
        if is_tip:
            label += " · current"
        row.set_title(label)
        detail = [relative_time(version.created_at)]
        if version.base:
            detail.append(f"from {version.base}")
        done, total = scratchpad.progress(version.body)
        if total:
            detail.append(f"{done}/{total} done")
        row.set_subtitle(" · ".join(detail))
        if version.number == self.base:
            row.add_css_class("accent")
        row.connect("activated", lambda _r, n=version.number: self._show_version(n))
        return row

    def _show_version(self, number: int) -> None:
        version = self.note.version(number)
        if version is None:
            return
        # Whatever is on screen is kept before moving away from it, so browsing
        # the history cannot discard an edit in progress.
        self._commit()
        self.base = number
        self._set_body(version.body)
        self._rebuild_history()
        self._sync_status()

    def _restore(self) -> None:
        version = self.note.restore(self.base)
        if version is not None:
            self.store.save()
            self.base = version.number
            self._set_body(version.body)
            self._rebuild_history()
            self._sync_status()

    def _sync_status(self) -> None:
        tip = self.note.current
        if tip is None:
            self.status.set_label("Not written yet")
            return
        if self.base == tip.number:
            done, total = scratchpad.progress(self.body)
            counted = f" · {done} of {total} done" if total else ""
            self.status.set_label(
                f"Version {tip.number}, the current one{counted}"
            )
            return
        self.status.set_label(
            f"Showing version {self.base} of {tip.number}. Editing it adds a new "
            f"version — {tip.number - self.base} newer "
            f"version{'s' if tip.number - self.base != 1 else ''} stay as they are."
        )

    # -- leaving -------------------------------------------------------------

    def _commit(self) -> None:
        """Append what is on screen, if it says anything new."""
        title = self.title_entry.get_text().strip()
        if title != self.note.title:
            self.store.rename(self.note, title)
        before = self.note.current
        version = self.store.revise(self.note, self.body, base=self.base)
        if version is not before:
            self.base = version.number
            log.debug("note %s at version %d", self.note.id, version.number)

    def _on_where(self, index: int) -> None:
        if self.where is None:
            return
        self.store.move(
            self.note, scratchpad.GLOBAL if index == 0 else self.context_id
        )

    def _ask_delete(self, asking: bool = True) -> None:
        self.delete_button.set_visible(not asking)
        self.confirm_button.set_visible(asking)
        self.keep_button.set_visible(asking)

    def _delete(self) -> None:
        self.store.delete(self.note)
        if self.on_delete is not None:
            self.on_delete(self.note)

    def _finish(self) -> None:
        self._commit()
        self.on_done(self.note)
