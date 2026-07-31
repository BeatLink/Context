"""Giving loose windows a home.

The goal state is that every window belongs to a context. Windows opened
outside one — from a notification, a file manager, a terminal you already had —
are the leftovers, and this offers each of them a context to join.

One list, one dropdown per window, one button. Deliberately not a wizard: the
whole point is that adopting a window should be cheaper than recreating it
inside a context.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from . import sidebar, theme, widgets
from .launcher import move_window_to_context
from .logging_setup import get_logger

log = get_logger("adopt")

# Chosen for a window that should stay where it is.
LEAVE = "Leave it"


class AdoptWindow(Gtk.ApplicationWindow):
    """A list of unmanaged windows, each with a context to send it to."""

    def __init__(self, app, store, windows, backend) -> None:
        super().__init__(application=app, title="Adopt windows")
        self.add_css_class("ctx-window")
        self.store = store
        self.windows = windows
        self.backend = backend
        self.choices: dict[str, Gtk.DropDown] = {}

        theme.install()
        self.set_default_size(720, 560)
        if not sidebar.apply_overlay(self):
            self.fullscreen()

        toolbar = widgets.ToolbarView()
        header = widgets.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)

        adopt = Gtk.Button(label="Adopt")
        adopt.add_css_class("suggested-action")
        adopt.connect("clicked", lambda _b: self._adopt())
        header.pack_end(adopt)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (
            "set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"
        ):
            getattr(content, setter)(18)

        count = len(windows)
        heading = Gtk.Label(
            label=(
                f"{count} window{'s' if count != 1 else ''} "
                "belong to no context"
            ),
            xalign=0.0,
            wrap=True,
        )
        heading.add_css_class("title-4")
        content.append(heading)

        # Only contexts that are open can take a window: a named workspace does
        # not exist until something is on it, so a closed one has nowhere to
        # put it.
        self.targets = [c for c in store.contexts if c.handles_for(backend.name)]
        labels = [LEAVE, *(c.title for c in self.targets)]

        if not self.targets:
            hint = Gtk.Label(
                label="Open a context first — a window can only move somewhere "
                "that already exists.",
                xalign=0.0,
                wrap=True,
            )
            hint.add_css_class("dim-label")
            content.append(hint)
            adopt.set_sensitive(False)

        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        for window in windows:
            row = widgets.ActionRow(
                title=window.title or window.app_id,
                subtitle=window.app_id or "unknown application",
            )
            row.set_subtitle_lines(1)
            drop = Gtk.DropDown(
                model=Gtk.StringList.new(labels), valign=Gtk.Align.CENTER
            )
            drop.set_sensitive(bool(self.targets))
            row.add_suffix(drop)
            self.choices[window.id] = drop
            listbox.append(row)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(listbox)
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

    def _adopt(self) -> int:
        """Move every window whose dropdown names a context. Returns how many."""
        moved = 0
        for window in self.windows:
            drop = self.choices.get(window.id)
            if drop is None:
                continue
            selected = drop.get_selected()
            if selected <= 0:  # index 0 is "leave it"
                continue
            ctx = self.targets[selected - 1]
            if move_window_to_context(window.id, ctx, backend=self.backend):
                moved += 1
                log.info("adopted %s into %s", window.app_id, ctx.title)
        self.close()
        return moved

    def _dismiss(self) -> bool:
        self.close()
        return True
