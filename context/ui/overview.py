"""The overview: every application installed, on one screen.

A search over what is installed, filtered by kind and grouped by how it is
ordered. An application opens in a context of its own, or joins the one you
came from — which makes this the fast path from "I want to do something" to
doing it.

**Contexts are the sidebar's, not this screen's.** They were listed here too,
in a column beside the grid, and the sidebar stands open beside home showing
exactly the same rows — the same contexts, the same handles, twice on one
screen. One of the two had to go, and the sidebar is the one that is there
from every workspace rather than only from this one.

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
from context.ui import theme
from context.system.apps import App
from context.system.launcher import read_live_state
from context.system.logging_setup import get_logger
from context.ui.catalogue import AppCatalogue
from context.ui.rows import AppRow, context_for_app

log = get_logger("overview")


class OverviewWindow(Gtk.ApplicationWindow):
    """Every installed application, searchable, one click from being open."""

    def __init__(self, app, store, backend=None) -> None:
        super().__init__(application=app, title="Overview")
        self.add_css_class("ctx-window")
        # Refused rather than hidden: hiding unmaps the surface, and the next
        # map puts it on whatever workspace is current instead of on home.
        # Cleared by `restart`, which does have to get rid of it — an execv
        # leaves the surface behind otherwise.
        self.permanent = True
        self.connect("close-request", lambda _w: self.permanent)
        self.store = store
        self.backend = backend or backends.detect()
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
        #
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

        # The same catalogue the editor draws, asked a different question:
        # there a row adds a window to the layout, here it opens the app.
        self.catalogue = AppCatalogue(self._app_row, counts=self._app_counts)
        # The catalogue owns the search box; what Enter and Escape mean in it
        # belong to the screen around it.
        self.catalogue.entry.connect("activate", lambda _e: self._activate_first())
        # A focused search entry consumes Escape as stop-search, so the window
        # shortcut never fires while typing — which is most of the time.
        self.catalogue.entry.connect("stop-search", lambda _e: self._escape())
        content.append(self.catalogue)

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
        self.catalogue.focus_search()

    # -- contents ------------------------------------------------------------

    def refresh(self) -> None:
        # The live state is read for `current_id` alone — which context an
        # application would join. The contexts themselves are the sidebar's.
        self._live = read_live_state(self.store.contexts, backend=self.backend)
        self.catalogue.refresh()

    def _app_row(self, info: App) -> AppRow:
        """The sidebar's row, unchanged.

        An application has the same two answers wherever it is listed — a
        context of its own, or the one you came from — so the row is shared
        rather than drawn a second way. It was a grid of tiles with a mode
        above it, which meant the same question was asked differently in the
        two views and a tile had nowhere to put the second answer.
        """
        current = self._current_context()
        return AppRow(
            info,
            self._open_app,
            self._open_app_here if current is not None else None,
            into=current.title if current is not None else "",
            buttons=False,
        )

    # -- acting --------------------------------------------------------------

    def _app_counts(self) -> dict[str, int]:
        """How many contexts each application belongs to."""
        counts: dict[str, int] = {}
        for ctx in self.store.contexts:
            for app_id in set(ctx.apps):
                counts[app_id] = counts.get(app_id, 0) + 1
        return counts

    def _current_context(self):
        """The context an application row would open into.

        `current_id`, not `active_id`: standing on home nothing is active, and
        that is exactly when the question is asked — you came here to find an
        app to add to what you were doing. See `launcher.current_context`.
        """
        current = self._live.current_id
        if current is None:
            return None
        return next((c for c in self.store.contexts if c.id == current), None)

    def _activate_first(self) -> None:
        """Enter: open the first application the search matched.

        The answer that always works, the same one clicking a row takes — a
        context of its own, whether or not anything is open.
        """
        row = self.catalogue.first()
        if row is not None:
            self._open_app(row.app_info)

    def _open_app(self, info: App) -> None:
        """A context grown around this app. What clicking the row does, since
        it is the answer that works whether or not anything is open."""
        ctx = context_for_app(self.store, info)
        log.info("overview: new context around %s", info.id)
        self.on_context(ctx)

    def _open_app_here(self, info: App) -> None:
        """This app added to the context you came from."""
        current = self._current_context()
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
        if self.catalogue.clear():
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

    # Set by the application; opening goes through the launcher so a context
    # is launched rather than merely focused when its windows are gone.
    def on_context(self, ctx) -> None:
        return None

    def on_app_into(self, ctx, info) -> None:
        return None

    def on_leave(self) -> None:
        return None
