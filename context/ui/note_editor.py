"""The scratchpad with room to work in.

The same note the sidebar shows and the same widget writing it — this adds space
and the two things a 380px column has no room for: the buttons that change what
a line is, and the checklist view where a checkbox is clicked rather than typed.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from context.state import scratchpad
from context.state.scratchpad import NoteStore
from context.ui import widgets
from context.ui.scratchpad import ScratchpadSection, checklist

WRITE, CHECKLIST = 0, 1


class NoteEditorPage(widgets.NavigationPage):
    def __init__(
        self,
        store: NoteStore,
        on_done,
        context_id: str | None = None,
        context_title: str = "",
        showing: str | None = None,
    ) -> None:
        super().__init__(title="Scratchpad", tag="scratchpad")
        self.store = store
        self.on_done = on_done

        toolbar = widgets.ToolbarView()
        header = widgets.HeaderBar(title="Scratchpad")
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

        self.section = ScratchpadSection(
            store,
            context_id=context_id,
            context_title=context_title,
            compact=False,
        )
        # Opened from the sidebar showing one of the two, so it opens on that one
        # rather than sending you back to where the chooser happens to start.
        if showing is not None:
            for view in self.section.views:
                if showing in view.offered:
                    index = view.offered.index(showing)
                    if view.choice is not None:
                        view.choice.set_selected(index, notify=False)
                    view.showing = showing
                    view._load()
                    self.section._active = view
                    break

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
            button.connect("clicked", lambda _b, k=kind: self._on_pad("set_line_kind", k))
            self.format_bar.append(button)
        controls.append(self.format_bar)
        content.append(controls)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.add_named(self.section, "write")

        self.checklist_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        checking = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        checking.add_css_class("ctx-note-scroll")
        checking.set_child(self.checklist_box)
        self.stack.add_named(checking, "checklist")
        content.append(self.stack)

        self.status = Gtk.Label(xalign=0.0)
        self.status.add_css_class("dim-label")
        self.status.add_css_class("caption")
        content.append(self.status)
        for view in self.section.views:
            view.buffer.connect("changed", lambda _b: self._sync_status())
        self._sync_status()

        toolbar.set_content(content)
        self.set_child(toolbar)

    def _on_mode(self, index: int) -> None:
        if index == CHECKLIST:
            self._rebuild_checklist()
        self.stack.set_visible_child_name("write" if index == WRITE else "checklist")
        self.format_bar.set_visible(index == WRITE)

    @property
    def pad(self):
        """The pad the controls act on: whichever was last typed in.

        With one scratchpad on screen this is simply it. With both, the buttons
        have to mean something, and "the one you are working in" is the only
        answer that does not need a second control to choose with.
        """
        return self.section.active

    def _on_pad(self, method: str, *args) -> None:
        pad = self.pad
        if pad is not None:
            getattr(pad, method)(*args)

    def _rebuild_checklist(self) -> None:
        child = self.checklist_box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.checklist_box.remove(child)
            child = following
        pad = self.pad
        self.checklist_box.append(
            checklist(pad.body if pad is not None else "", self._toggle)
        )

    def _toggle(self, index: int) -> None:
        pad = self.pad
        if pad is None:
            return
        updated = scratchpad.toggle(pad.body, index)
        if updated != pad.body:
            pad.set_body(updated)

    def _sync_status(self) -> None:
        # Only the checklist count. Whether it is written is each pad's own
        # business and each pad says so itself.
        pad = self.pad
        done, total = scratchpad.progress(pad.body if pad is not None else "")
        self.status.set_label(f"{done} of {total} done" if total else "")

    def _finish(self) -> None:
        self.section.flush()
        self.on_done()
