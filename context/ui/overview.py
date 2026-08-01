"""The overview: everything Context can open, on one screen.

One search bar over two columns — the contexts that exist, open ones first,
and the applications installed. A context row opens that context; an
application starts a new context around that app and opens it, which makes
the overview the fast path from "I want to do something" to doing it.

**Home is a place, not an overlay.** The window lives on a workspace of its
own that Context owns, and going to the overview is a workspace switch rather
than a surface raised over whatever you were looking at. It is never closed:
`close-request` is refused, so the compositor's own close cannot take away the
one screen that is always there to come back to.

Being an ordinary toplevel is most of what this buys. An overlay holds the
keyboard exclusively, so the editor and the pickers could not be opened over
it — the overview had to close and hand the context on, and everything it did
that way came back to a screen that was no longer there. A window on a
workspace has the editor stack over it and is still underneath afterwards.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from context.system import backends
from context.ui import theme, widgets
from context.state import settings, uistate
from context.state.scratchpad import NoteStore
from context.ui.scratchpad import ScratchpadSection
from context.system.apps import MAIN_CATEGORIES, SORTS, App, arrange_apps, categories_of
from context.system.apps import in_category, installed_apps, search_apps
from context.system.launcher import is_no_context, loose_context, read_live_state
from context.system.logging_setup import get_logger
from context.ui.rows import AppRow, ContextRow, context_for_app

log = get_logger("overview")

# Enough for the longest of the three, so the controls line up under one another.
LABEL_WIDTH = 74


def _labelled(text: str, control: Gtk.Widget) -> Gtk.Box:
    """One of the grid's controls, behind the word for what it decides."""
    row = Gtk.Box(spacing=8)
    label = Gtk.Label(label=text, xalign=0.0)
    label.add_css_class("dim-label")
    label.set_size_request(LABEL_WIDTH, -1)
    row.append(label)
    row.append(control)
    return row


class OverviewWindow(Gtk.ApplicationWindow):
    """Contexts on one side, applications on the other, one search over both."""

    def __init__(self, app, store, backend=None, notes=None) -> None:
        super().__init__(application=app, title="Overview")
        self.add_css_class("ctx-window")
        # Refused rather than hidden: hiding unmaps the surface, and the next
        # map puts it on whatever workspace is current instead of on home.
        # Cleared by `restart`, which does have to get rid of it — an execv
        # leaves the surface behind otherwise.
        self.permanent = True
        self.connect("close-request", lambda _w: self.permanent)
        self.store = store
        self.notes = notes if notes is not None else NoteStore()
        # Set by the application, the way `on_edit` is: the overview does not
        # own the note editor any more than it owns the context editor, since
        # two overlays stacked leaves one covering the other.
        self.on_note = None
        self.backend = backend or backends.detect()
        self.apps = installed_apps()
        self._active_id: str | None = None
        # Which kind of application the grid is showing; "" is all of them.
        # Not a setting: it is narrowing done in the moment, and an overview
        # that opened filtered would look like half your applications had gone.
        self.category = ""
        # The order is a setting, so the overview opens the same way every time
        # rather than however it was left. Where an application opens is not:
        # each tile asks, the way the sidebar's rows do.
        live = settings.current()
        self.sort = live.overview_sort if live.overview_sort in SORTS else next(iter(SORTS))
        self.flows: list[Gtk.ListBox] = []
        self._live = read_live_state([], backend=self.backend)

        theme.install()
        # A tiled window fills the workspace it is on, so this only decides the
        # shape it takes where there is no window manager to tile it.
        self.set_default_size(1200, 720)
        # The toolkit half of having no titlebar. It is not the half that
        # matters on Hyprland — hyprbars decorates regardless and is turned off
        # by a window rule — but a compositor honouring xdg-decoration would
        # otherwise put a bar with a close button on the one window that must
        # not close.
        self.set_decorated(False)

        # No header. Every other full-screen view is a thing you opened and
        # will close, so it carries a title saying which one and a back button
        # to leave by; home is neither. The title named the screen you are
        # always able to get to, and the back button offered to leave the one
        # place that cannot be left empty — Escape and the sidebar's own list
        # are the ways out, and both were already there.
        # The card and the inset it holds are two boxes, not one. `.ctx-surface`
        # is what paints — the window itself is transparent — and a margin is
        # outside the thing it is set on, so a single box carrying both the
        # class and the 18px left that band unpainted: a transparent border
        # inside the window's own rounded edge.
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for setter in (
            "set_margin_top",
            "set_margin_bottom",
            "set_margin_start",
            "set_margin_end",
        ):
            getattr(content, setter)(18)

        self.entry = widgets.SearchBar("Search contexts and apps")
        self.entry.connect("search-changed", lambda _e: self.refresh())
        self.entry.connect("activate", lambda _e: self._activate_first())
        # A focused search entry consumes Escape as stop-search, so the
        # window shortcut below never fires while typing — which is most of
        # the time. stop-search is the entry's own Escape.
        self.entry.connect("stop-search", lambda _e: self._escape())
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

        # The scratchpad, under the contexts it sits beside in the sidebar —
        # the same widget, given the room that is the whole difference between
        # the two views.
        self.notes_label = Gtk.Label(label="Scratchpad", xalign=0.0)
        self.notes_label.add_css_class("heading")
        self.notes_label.add_css_class("dim-label")
        self.scratchpad_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.scratchpad_view = None

        groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        groups.append(self.open_label)
        groups.append(self.open_list)
        groups.append(self.saved_label)
        groups.append(self.saved_list)
        groups.append(self.notes_label)
        groups.append(self.scratchpad_box)

        left_scroller = Gtk.ScrolledWindow(vexpand=True)
        left_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left_scroller.set_child(groups)
        left.append(left_scroller)

        # Applications. Each is one click from a context of its own.
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_hexpand(True)

        self.apps_label = Gtk.Label(label="Apps", xalign=0.0)
        self.apps_label.add_css_class("heading")
        self.apps_label.add_css_class("dim-label")
        right.append(self.apps_label)


        # What is installed, by kind. Buttons rather than a dropdown, like
        # everything else on an overlay, and only the categories something is
        # actually filed under — an empty "Science" helps nobody.
        self.categories = ["", *categories_of(self.apps)]
        self.category_chooser = widgets.SegmentedChoice(self._on_category)
        for key in self.categories:
            self.category_chooser.add(MAIN_CATEGORIES.get(key, "All"))
        # The categories outrun the column on a full desktop, so this one
        # scrolls where the other two rows do not.
        category_scroller = Gtk.ScrolledWindow(
            propagate_natural_width=True, hexpand=True
        )
        category_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        category_scroller.set_child(self.category_chooser)
        right.append(_labelled("Category", category_scroller))

        # And in what order. Buttons again rather than a dropdown, for the same
        # reason: a popover on an overlay never keeps what is clicked in it.
        self.sort_keys = list(SORTS)
        self.sort_chooser = widgets.SegmentedChoice(self._on_sort)
        for key in self.sort_keys:
            self.sort_chooser.add(SORTS[key])
        self.sort_chooser.set_selected(self.sort_keys.index(self.sort), notify=False)
        right.append(_labelled("Sort", self.sort_chooser))


        # One section per group, each with its own grid: a heading cannot sit
        # inside a list, and the groups are the point of the ordering.
        self.sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        right_scroller = Gtk.ScrolledWindow(vexpand=True)
        right_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        right_scroller.set_child(self.sections)
        right.append(right_scroller)

        columns.append(left)
        columns.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        columns.append(right)
        content.append(columns)

        # Fills the window, so the rounding and the inset ring `.ctx-surface`
        # draws are the window's own edge. Overflow hidden clips the lists to
        # those corners.
        self.surface = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.surface.add_css_class("ctx-surface")
        self.surface.add_css_class("ctx-solid")
        self.surface.set_overflow(Gtk.Overflow.HIDDEN)
        content.set_vexpand(True)
        self.surface.append(content)
        self.set_child(self.surface)

        escape = Gtk.ShortcutController()
        escape.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.CallbackAction.new(lambda *_a: self._escape()),
            )
        )
        self.add_controller(escape)

        # Typing is what the overview is for, so the search box holds the
        # keyboard from the moment it appears rather than after a click. On map
        # rather than in the constructor: grabbing focus on a widget that has
        # not been realised does nothing at all.
        self.connect("map", lambda _w: self.focus_search())

        self.refresh()

    def focus_search(self) -> None:
        self.entry.grab_focus()

    # -- contents ------------------------------------------------------------

    def refresh(self) -> None:
        query = self.entry.get_text().strip()
        matches = self.store.search(query)
        live = read_live_state(self.store.contexts, backend=self.backend)
        open_ids, self._active_id = live.open_ids, live.active_id
        self._live = live
        self.create_row.set_subtitle(self._create_subtitle(query))

        opened = [c for c in matches if c.id in open_ids]
        saved = [c for c in matches if c.id not in open_ids]

        self.open_list.remove_all()
        for ctx in opened:
            self.open_list.append(self._context_row(ctx, is_open=True))
        # Everything running that belongs to no context, listed as one until it
        # is given a name. It has none to search by, so it goes when you type.
        loose = loose_context(live.loose) if not query else None
        if loose is not None:
            self.open_list.append(self._context_row(loose, is_open=True))
        self.saved_list.remove_all()
        for ctx in saved:
            self.saved_list.append(self._context_row(ctx, is_open=False))

        live_settings = settings.current()
        shown = live_settings.scratchpad and live_settings.overview_scratchpad
        self._sync_scratchpad(self._active_id, shown)
        self.notes_label.set_visible(shown)
        self.scratchpad_box.set_visible(shown)

        self.open_label.set_visible(bool(opened or loose))
        self.open_list.set_visible(bool(opened or loose))
        self.saved_label.set_visible(bool(saved))
        self.saved_list.set_visible(bool(saved))

        within = in_category(self.apps, self.category)
        matches = search_apps(within, query)
        self._fill_sections(
            arrange_apps(
                matches,
                self.sort,
                times=uistate.app_times(),
                counts=self._app_counts(),
            )
        )
        kind = MAIN_CATEGORIES.get(self.category, "")
        self.apps_label.set_label(f"{kind or 'Apps'} · {len(matches)}")

    def _fill_sections(self, sections: list[tuple[str, list[App]]]) -> None:
        child = self.sections.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.sections.remove(child)
            child = following

        self.flows: list[Gtk.ListBox] = []
        for heading, apps in sections:
            if heading:
                label = Gtk.Label(label=heading, xalign=0.0)
                label.add_css_class("heading")
                label.add_css_class("dim-label")
                self.sections.append(label)
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")
            listbox.set_valign(Gtk.Align.START)
            for info in apps:
                listbox.append(self._app_row(info))
            self.sections.append(listbox)
            self.flows.append(listbox)

    def _context_row(self, ctx, is_open: bool) -> ContextRow:
        """The sidebar's row, unchanged.

        A context has the same handles wherever it is listed — open it, edit
        it, close it — so the row is shared rather than reimplemented here
        with half of them missing, which is how the two drifted apart before.
        """
        virtual = is_no_context(ctx)
        return ContextRow(
            ctx,
            self._open_context,
            None if virtual else self._edit_context,
            self._close_context,
            is_open=is_open,
            is_active=self._active_id is not None and ctx.id == self._active_id,
            is_drifted=virtual or ctx.id in self._live.drifted_ids,
            on_forget=None if virtual else self._forget_context,
            on_add_app=None if virtual else self._add_app_to_context,
            on_save=self._save_context,
            on_restore=self._restore_context,
        )

    def _restore_context(self, ctx) -> None:
        # Housekeeping done while you carry on looking, the same exception
        # closing and saving make: the list refreshes rather than dismissing.
        self.on_restore(ctx)
        self.refresh()

    def _save_context(self, ctx) -> None:
        log.info("overview: saving the windows of %s", ctx.title)
        self.on_save(ctx)
        self.refresh()

    def _add_app_to_context(self, ctx) -> None:
        """Hand app-picking to the application, which owns the picker."""
        log.info("overview: adding an app to %s", ctx.title)
        self.on_add_app(ctx)

    def _app_row(self, info: App) -> AppRow:
        """The sidebar's row, unchanged.

        An application has the same two answers wherever it is listed — a
        context of its own, or the one you are in — so the row is shared rather
        than drawn a second way. It was a grid of tiles with a mode above it,
        which meant the same question was asked differently in the two views and
        a tile had nowhere to put the second answer.
        """
        current = self._active_context()
        return AppRow(
            info,
            self._open_app,
            self._open_app_here if current is not None else None,
            into=current.title if current is not None else "",
        )

    # -- acting --------------------------------------------------------------

    def _app_counts(self) -> dict[str, int]:
        """How many contexts each application belongs to."""
        counts: dict[str, int] = {}
        for ctx in self.store.contexts:
            for app_id in set(ctx.apps):
                counts[app_id] = counts.get(app_id, 0) + 1
        return counts

    def _active_context(self):
        """The context an application row would open into.

        `current_id`, not `active_id`: standing on home nothing is active, and
        that is exactly when the question is asked — you came here to find an
        app to add to what you were doing. See `launcher.current_context`.
        """
        current = self._live.current_id
        if current is None:
            return None
        return next((c for c in self.store.contexts if c.id == current), None)

    def _on_sort(self, selected: int) -> None:
        if 0 <= selected < len(self.sort_keys):
            self.sort = self.sort_keys[selected]
            log.debug("sorting apps by %s", self.sort)
            self.refresh()

    def _on_category(self, selected: int) -> None:
        if 0 <= selected < len(self.categories):
            self.category = self.categories[selected]
            log.debug("category %s", self.category or "all")
            self.refresh()

    def _create_subtitle(self, query: str) -> str:
        if not query:
            return "Name it in the editor"
        if any(c.title.casefold() == query.casefold() for c in self.store.contexts):
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
            if ctx.title.casefold() == title.casefold():
                self._open_context(ctx)
                return
        self._edit_context(self.store.create(title), is_new=True)

    def _open_context(self, ctx) -> None:
        """Opening a context is a workspace switch, which takes you off home.

        Nothing is dismissed: the overview is still on its own workspace when
        you come back, with the search you typed and the grid where you left it.
        """
        log.info("overview: opening context %s", ctx.title)
        self.on_context(ctx)

    def _edit_context(self, ctx, is_new: bool = False) -> None:
        """Hand editing to the launcher, which owns the editor.

        The editor is a layer-shell overlay and covers this, which is the point
        of the overview no longer being one: it opens on top and the overview is
        still underneath when it closes.
        """
        log.info("overview: editing context %s", ctx.title)
        self.on_edit(ctx, is_new)

    def _close_context(self, ctx) -> None:
        """Close a context without leaving the overview."""
        log.info("overview: closing context %s", ctx.title)
        self.on_close(ctx)
        self.refresh()

    def _forget_context(self, ctx) -> None:
        self.store.delete(ctx)
        self.refresh()

    def _open_app(self, info: App) -> None:
        """A context grown around this app. What clicking the tile does, since
        it is the answer that works whether or not anything is open."""
        ctx = context_for_app(self.store, info)
        log.info("overview: new context around %s", info.id)
        self.on_context(ctx)

    def _open_app_here(self, info: App) -> None:
        """This app added to the context you came from."""
        current = self._active_context()
        if current is None:
            self._open_app(info)
            return
        log.info("overview: %s into %s", info.id, current.title)
        self.on_app_into(current, info)

    def _escape(self) -> bool:
        """Clear the search, or leave if there is nothing to clear.

        Escape used to close the overview, which is no longer a thing that can
        happen — so it does the two things left that mean "not this": undo the
        filtering, then go back where you came from.
        """
        if self.entry.get_text():
            self.entry.set_text("")
            return True
        return self._leave()

    def _leave(self) -> bool:
        """Back to the context you came from, if it is still open.

        Nothing happens when there is nowhere to go. Home is where a desktop
        with nothing running is, so leaving it for an empty workspace would be
        leaving the only screen that has anything on it.
        """
        self.on_leave()
        return True

    def _sync_scratchpad(self, context_id, shown: bool) -> None:
        if not shown:
            return
        if self.scratchpad_view is not None and self.scratchpad_view.matches(
            context_id
        ):
            self.scratchpad_view.refresh()
            return
        if self.scratchpad_view is not None:
            self.scratchpad_view.flush()
            self.scratchpad_box.remove(self.scratchpad_view)
        active = next((c for c in self.store.contexts if c.id == context_id), None)
        self.scratchpad_view = ScratchpadSection(
            self.notes,
            context_id=context_id,
            context_title=active.title if active is not None else "",
            on_expand=self._open_note,
            compact=True,
        )
        self.scratchpad_box.append(self.scratchpad_view)

    def _open_note(self, showing=None) -> None:
        # Handed to the launcher rather than opened here, the same way editing a
        # context is: the launcher owns the editors, not the views that ask.
        if self.on_note is not None:
            self.on_note(showing)

    # Set by the application; opening goes through the launcher so a context
    # is launched rather than merely focused when its windows are gone.
    def on_context(self, ctx) -> None:
        return None

    def on_edit(self, ctx, is_new: bool = False) -> None:
        return None

    def on_close(self, ctx) -> None:
        return None

    def on_save(self, ctx) -> None:
        return None

    def on_restore(self, ctx) -> None:
        return None

    def on_app_into(self, ctx, info) -> None:
        return None

    def on_leave(self) -> None:
        return None
