"""The overview: everything Context can open, on one screen.

One search bar over two columns — the contexts that exist, open ones first,
and the applications installed. A context row opens that context; an
application starts a new context around that app and opens it, which makes
the overview the fast path from "I want to do something" to doing it.

The same overlay shape as the switcher: it covers the output it is summoned
on, takes the keyboard while it is up, and leaves on Escape.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from . import backends, sidebar, theme, widgets
from .apps import App, installed_apps, search_apps
from .launcher import open_state
from .logging_setup import get_logger
from .rows import ContextRow, app_tile, context_for_app

log = get_logger("overview")


class OverviewWindow(Gtk.ApplicationWindow):
    """Contexts on one side, applications on the other, one search over both."""

    def __init__(self, app, store, backend=None) -> None:
        super().__init__(application=app, title="Overview")
        self.add_css_class("ctx-window")
        self.store = store
        self.backend = backend or backends.detect()
        self.apps = installed_apps()
        self._active_id: str | None = None

        theme.install()
        self.set_default_size(1200, 720)
        if not sidebar.apply_overlay(self):
            self.fullscreen()

        toolbar = widgets.ToolbarView()
        toolbar.add_css_class("ctx-surface")
        toolbar.set_overflow(Gtk.Overflow.HIDDEN)
        header = widgets.HeaderBar(title="Overview")
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

        self.entry = Gtk.SearchEntry(placeholder_text="Search contexts and apps")
        self.entry.connect("search-changed", lambda _e: self.refresh())
        self.entry.connect("activate", lambda _e: self._activate_first())
        # A focused search entry consumes Escape as stop-search, so the
        # window shortcut below never fires while typing — which is most of
        # the time. stop-search is the entry's own Escape.
        self.entry.connect("stop-search", lambda _e: self._dismiss())
        content.append(self.entry)

        # The same built-in row the sidebar's list carries: with a name typed
        # it starts that context, blank it opens the editor to be named.
        self.create_row = widgets.ActionRow(title="New context")
        self.create_row.set_activatable(True)
        self.create_row.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        self.create_row.set_subtitle("Name it in the editor")
        self.create_row.connect("activated", lambda _r: self._create_from_entry())
        create_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        create_list.add_css_class("boxed-list")
        create_list.append(self.create_row)
        content.append(create_list)

        columns = Gtk.Box(spacing=18)

        # Contexts. Open before saved, the same split the sidebar draws —
        # the overview is the sidebar's content given room to breathe.
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_hexpand(True)

        self.open_label = Gtk.Label(label="Open", xalign=0.0)
        self.open_label.add_css_class("heading")
        self.open_label.add_css_class("dim-label")
        self.open_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.open_list.add_css_class("boxed-list")

        self.saved_label = Gtk.Label(label="Saved", xalign=0.0)
        self.saved_label.add_css_class("heading")
        self.saved_label.add_css_class("dim-label")
        self.saved_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.saved_list.add_css_class("boxed-list")

        groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        groups.append(self.open_label)
        groups.append(self.open_list)
        groups.append(self.saved_label)
        groups.append(self.saved_list)

        left_scroller = Gtk.ScrolledWindow(vexpand=True)
        left_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left_scroller.set_child(groups)
        left.append(left_scroller)

        # Applications. Each is one click from a context of its own.
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_hexpand(True)

        apps_label = Gtk.Label(label="Apps · open in a new context", xalign=0.0)
        apps_label.add_css_class("heading")
        apps_label.add_css_class("dim-label")
        right.append(apps_label)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_max_children_per_line(30)

        right_scroller = Gtk.ScrolledWindow(vexpand=True)
        right_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        right_scroller.set_child(self.flow)
        right.append(right_scroller)

        columns.append(left)
        columns.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        columns.append(right)
        content.append(columns)

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

    # -- contents ------------------------------------------------------------

    def refresh(self) -> None:
        query = self.entry.get_text().strip()
        matches = self.store.search(query)
        open_ids, active_id = open_state(self.store.contexts, backend=self.backend)
        self._active_id = active_id
        self.create_row.set_subtitle(self._create_subtitle(query))

        opened = [c for c in matches if c.id in open_ids]
        saved = [c for c in matches if c.id not in open_ids]

        self.open_list.remove_all()
        for ctx in opened:
            self.open_list.append(self._context_row(ctx, is_open=True))
        self.saved_list.remove_all()
        for ctx in saved:
            self.saved_list.append(self._context_row(ctx, is_open=False))

        self.open_label.set_visible(bool(opened))
        self.open_list.set_visible(bool(opened))
        self.saved_label.set_visible(bool(saved))
        self.saved_list.set_visible(bool(saved))

        self.flow.remove_all()
        for info in search_apps(self.apps, query):
            self.flow.append(self._app_tile(info))

    def _context_row(self, ctx, is_open: bool) -> ContextRow:
        """The sidebar's row, unchanged.

        A context has the same handles wherever it is listed — open it, edit
        it, close it — so the row is shared rather than reimplemented here
        with half of them missing, which is how the two drifted apart before.
        """
        return ContextRow(
            ctx,
            self._open_context,
            self._edit_context,
            self._close_context,
            is_open=is_open,
            is_active=self._active_id is not None and ctx.id == self._active_id,
            on_forget=self._forget_context,
            on_add_app=self._add_app_to_context,
        )

    def _add_app_to_context(self, ctx) -> None:
        """Hand app-picking to the application, which owns the picker."""
        log.info("overview: adding an app to %s", ctx.title)
        self.close()
        self.on_add_app(ctx)

    def _app_tile(self, info: App) -> Gtk.FlowBoxChild:
        return app_tile(info, self._open_app)

    # -- acting --------------------------------------------------------------

    def _create_subtitle(self, query: str) -> str:
        if not query:
            return "Name it in the editor"
        if any(c.title.lower() == query.lower() for c in self.store.contexts):
            return f"Open “{query}”"
        return f"Start “{query}”"

    def _activate_first(self) -> None:
        """Enter: the first context that matches, or a new one by that name.

        Typing something nothing is called and pressing Enter used to do
        nothing at all here, while the sidebar started it — the same keystroke
        meaning two different things depending on which was open.
        """
        for listbox in (self.open_list, self.saved_list):
            row = listbox.get_row_at_index(0)
            if row is not None and listbox.get_visible():
                self._open_context(row.ctx)
                return
        self._create_from_entry()

    def _create_from_entry(self) -> None:
        title = self.entry.get_text().strip()
        if not title:
            self._edit_context(self.store.create("New context"), is_new=True)
            return
        for ctx in self.store.contexts:
            if ctx.title.lower() == title.lower():
                self._open_context(ctx)
                return
        self._edit_context(self.store.create(title), is_new=True)

    def _open_context(self, ctx) -> None:
        log.info("overview: opening context %s", ctx.title)
        self.close()
        self.on_context(ctx)

    def _edit_context(self, ctx, is_new: bool = False) -> None:
        """Hand editing to the launcher, and get out of its way.

        The editor is an overlay that takes the keyboard exclusively, and so is
        this — two of them stacked leaves the top one typed into and the bottom
        one still covering the screen.
        """
        log.info("overview: editing context %s", ctx.title)
        self.close()
        self.on_edit(ctx, is_new)

    def _close_context(self, ctx) -> None:
        """Close a context without leaving the overview.

        Unlike opening or editing, this is housekeeping: you close a couple of
        contexts and carry on choosing, so the list stays up and re-reads
        itself instead.
        """
        log.info("overview: closing context %s", ctx.title)
        self.on_close(ctx)
        self.refresh()

    def _forget_context(self, ctx) -> None:
        self.store.delete(ctx)
        self.refresh()

    def _open_app(self, info: App) -> None:
        """A context grown around one app, opened on the spot."""
        ctx = context_for_app(self.store, info)
        log.info("overview: new context around %s", info.id)
        self.close()
        self.on_context(ctx)

    def _dismiss(self) -> bool:
        self.close()
        return True

    # Set by the application; opening goes through the launcher so a context
    # is launched rather than merely focused when its windows are gone.
    def on_context(self, ctx) -> None:
        return None

    def on_edit(self, ctx, is_new: bool = False) -> None:
        return None

    def on_close(self, ctx) -> None:
        return None
