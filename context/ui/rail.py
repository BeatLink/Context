"""The collapsed sidebar: a strip of icons, one per context.

The full launcher is swapped out rather than squeezed, because search and
titles have nowhere to go at rail width. The rail shows every context, never a
search result — there is no search bar at this size to explain why some are
missing — and folds its saved group the same way the expanded list does.

The rail runs along whichever edge the sidebar docks to, so it lays out along
its length: a column on the left or right edge, a row along the top or bottom.
Built as a column regardless, it overflowed a 56px-tall strip after two icons.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, Gtk

from context.ui import sidebar
from context.system.logging_setup import get_logger
from context.state.store import Context

log = get_logger("rail")

# How far the rail's contents sit from the surface's edges. Without it the
# buttons ran into the card's border, which the expanded sidebar never does —
# everything in it is inset from the edge.
RAIL_MARGIN = 4
# The icon fills the rail minus its button's padding and border, and minus the
# margins either side. Derived rather than fixed: a 32px icon in a 32px rail
# cannot fit, so the rail silently came out wider than it was set to.
RAIL_ICON_PADDING = 16
MIN_RAIL_ICON = 12

# Which way the expand button points: into the screen, away from the edge the
# sidebar is docked against.
EXPAND_ICONS = {
    "left": "go-next-symbolic",
    "right": "go-previous-symbolic",
    "top": "go-down-symbolic",
    "bottom": "go-up-symbolic",
}


def rail_icon_size() -> int:
    room = sidebar.rail_width() - RAIL_ICON_PADDING - 2 * RAIL_MARGIN
    return max(MIN_RAIL_ICON, room)


class Rail(Gtk.Box):
    """The rail, wired to the launcher through three callbacks.

    `on_open` gets the context behind a clicked icon; `on_expand` grows the
    sidebar back; `on_toggle_saved` folds or unfolds the saved group, which the
    launcher owns because the expanded list folds with it.
    """

    def __init__(self, on_open, on_expand, on_toggle_saved) -> None:
        edge = sidebar.configured_edge()
        along = (
            Gtk.Orientation.VERTICAL
            if edge in ("left", "right")
            else Gtk.Orientation.HORIZONTAL
        )
        super().__init__(orientation=along, spacing=4)
        self.on_open = on_open
        self.on_toggle_saved = on_toggle_saved
        self._along = along
        # Inset from the card the same way everything in the expanded sidebar
        # is; `rail_icon_size` gives the margins back out of the icon so the
        # rail still renders at the width it was set to.
        for setter in (
            "set_margin_top",
            "set_margin_bottom",
            "set_margin_start",
            "set_margin_end",
        ):
            getattr(self, setter)(RAIL_MARGIN)

        self.expand_button = Gtk.Button(icon_name=EXPAND_ICONS[edge])
        self.expand_button.add_css_class("flat")
        # Or Adwaita's default button width becomes the rail's floor, and a
        # narrow rail comes out wider than it was set to.
        self.expand_button.add_css_class("ctx-rail-toggle")
        self.expand_button.set_tooltip_text("Expand the launcher")
        self.expand_button.connect("clicked", lambda _b: on_expand())
        self.append(self.expand_button)

        self.buttons = Gtk.Box(orientation=along, spacing=6)
        if along is Gtk.Orientation.VERTICAL:
            self.buttons.set_margin_top(8)
            self.buttons.set_margin_bottom(8)
        else:
            self.buttons.set_margin_start(8)
            self.buttons.set_margin_end(8)

        scroller = Gtk.ScrolledWindow()
        if along is Gtk.Orientation.VERTICAL:
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_vexpand(True)
        else:
            scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
            scroller.set_hexpand(True)
        scroller.set_child(self.buttons)
        self.append(scroller)

    def rebuild(
        self,
        opened: list[Context],
        saved: list[Context],
        shown: bool,
        active_id: str | None,
    ) -> None:
        """Open contexts, a divider, then saved ones if the group is showing.

        There is no room for headings, so the split is drawn the way a browser
        separates pinned tabs from the rest when its tab strip is collapsed: a
        rule, and a control to fold the group away.
        """
        child = self.buttons.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.buttons.remove(child)
            child = following

        for ctx in opened:
            self.buttons.append(self._context_button(ctx, True, active_id))

        if not saved:
            return

        if opened:
            divider = Gtk.Separator(
                orientation=(
                    Gtk.Orientation.HORIZONTAL
                    if self._along is Gtk.Orientation.VERTICAL
                    else Gtk.Orientation.VERTICAL
                )
            )
            divider.add_css_class("ctx-rail-divider")
            self.buttons.append(divider)
            # Only when the group could actually fold. With nothing open the
            # saved list is the whole rail, and a control that empties it is a
            # trap.
            self.buttons.append(self._saved_toggle(len(saved), shown))

        if shown:
            for ctx in saved:
                self.buttons.append(self._context_button(ctx, False, active_id))

    def _saved_toggle(self, count: int, shown: bool) -> Gtk.Button:
        button = Gtk.Button(
            halign=Gtk.Align.CENTER,
            icon_name="go-up-symbolic" if shown else "go-down-symbolic",
        )
        button.add_css_class("flat")
        button.add_css_class("ctx-rail-toggle")
        button.set_tooltip_text(
            "Hide saved contexts"
            if shown
            else f"Show {count} saved context{'s' if count != 1 else ''}"
        )
        button.connect("clicked", lambda _b: self.on_toggle_saved(not shown))
        return button

    def _context_button(
        self, ctx: Context, is_open: bool, active_id: str | None
    ) -> Gtk.Button:
        button = Gtk.Button(halign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.add_css_class("ctx-rail-button")
        button.set_child(_icon(ctx))

        if active_id is not None and ctx.id == active_id:
            button.add_css_class("ctx-active")
            state = "here now"
        elif is_open:
            button.add_css_class("ctx-open")
            state = "open"
        else:
            button.add_css_class("ctx-saved")
            state = "saved"

        button.set_tooltip_text(f"{ctx.title} · {state}")
        button.connect("clicked", lambda _b, c=ctx: self.on_open(c))
        return button


def _icon(ctx: Context) -> Gtk.Image:
    """The first app's icon, so a context is recognisable without its name."""
    image = None
    for resource in ctx.resources:
        try:
            info = Gio.DesktopAppInfo.new(resource.app_id)
        except TypeError:
            info = None
        icon = info.get_icon() if info is not None else None
        if icon is not None:
            image = Gtk.Image.new_from_gicon(icon)
            break
    if image is None:
        image = Gtk.Image.new_from_icon_name("view-grid-symbolic")
    # The icon is the only thing identifying a context on the rail, so it gets
    # the room the label would otherwise have taken.
    image.set_pixel_size(rail_icon_size())
    return image
