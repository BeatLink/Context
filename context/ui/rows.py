"""The rows the views share.

`ContextRow` is the sidebar's, which is the only place contexts are listed now.
The application rows are drawn in three: the sidebar's search results, the
overview's catalogue and the editor's. They differ only in what one row *does*
— open it somewhere, or add it to a layout — so the question each asks is a
row here rather than a grid reinvented per view. The overview and the editor
had separately grown catalogues, one with a category filter and an ordering and
one with neither.
"""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, Gtk

from context.ui import widgets
from context.system.apps import App
from context.state.layout import preset_for
from context.state.resources import Resource
from context.system.launcher import is_no_context
from context.state.store import Context, ContextStore


def relative_time(stamp: float) -> str:
    delta = max(0, int(time.time() - stamp))
    if delta < 60:
        return "just now"
    for unit, seconds in (("d", 86400), ("h", 3600), ("m", 60)):
        if delta >= seconds:
            return f"{delta // seconds}{unit} ago"
    return "just now"


def _row_menu(row: Gtk.Widget, x: float, y: float):
    """A row's menu, built where it was asked for.

    Built per opening rather than kept on the row: rows are thrown away and
    rebuilt on every refresh, and a popover parented to a dead row is a warning
    at best. Unparenting on close keeps the tree clean.

    Shared by both cards so a right-click means the same thing on either — the
    context row grew this first and the app row would otherwise have reinvented
    it a second way.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    popover = Gtk.Popover()
    popover.set_has_arrow(False)
    popover.set_halign(Gtk.Align.START)
    popover.set_child(box)
    popover.set_parent(row)
    at = Gdk.Rectangle()
    at.x, at.y, at.width, at.height = int(x), int(y), 1, 1
    popover.set_pointing_to(at)
    popover.connect("closed", lambda p: p.unparent())

    items: dict[str, Gtk.Button] = {}

    def item(key: str, label: str, action, destructive: bool = False):
        button = Gtk.Button(label=label)
        button.set_has_frame(False)
        button.get_child().set_xalign(0.0)
        if destructive:
            button.add_css_class("destructive-action")
        button.connect("clicked", lambda _b: action())
        box.append(button)
        items[key] = button
        return button

    return popover, item, items


class ContextRow(widgets.ActionRow):
    """A context in a list.

    Open contexts get a close button; saved ones do not, and neither gets a
    delete button — forgetting a context happens in its editor, so it cannot be
    triggered by a stray click next to launch.
    """

    def __init__(
        self,
        ctx: Context,
        on_open,
        on_edit,
        on_close,
        is_open=False,
        is_active=False,
        is_drifted=False,
        on_delete=None,
        on_add_app=None,
        on_save=None,
        on_restore=None,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.is_open = is_open
        self.is_active = is_active
        self.is_drifted = is_drifted
        self.on_open = on_open
        self.on_edit = on_edit
        self.on_close = on_close
        self.on_delete = on_delete
        self.on_add_app = on_add_app
        self.on_save = on_save
        self.on_restore = on_restore
        # Escaped only where markup is actually parsed — see below. A plain
        # label shows what it is given, so an escaped one spelled out the
        # entities: "Review todos &amp; notes" on every row but the current.
        self.set_title(ctx.title)
        self.set_activatable(True)

        self.is_virtual = is_no_context(ctx)
        if self.is_virtual:
            count = len(getattr(ctx, "windows", []))
            subtitle = [
                f"{count} window{'s' if count != 1 else ''} in no context",
                "save them as a context, or close them",
            ]
        else:
            subtitle = [relative_time(ctx.last_used_at)]
            if ctx.apps:
                subtitle.append(
                    f"{len(ctx.apps)} app{'s' if len(ctx.apps) != 1 else ''}"
                )
            if ctx.ephemeral:
                # "unsaved" rather than "ephemeral": it says what to do about
                # it. Ephemeral was a property you chose; this is a state you
                # leave by pressing the button next to the word.
                subtitle.append("unsaved")
        self.set_subtitle(" · ".join(subtitle))

        icon = Gtk.Image.new_from_icon_name(
            "dialog-question-symbolic"
            if self.is_virtual
            else ("media-playback-start-symbolic" if is_open else "view-grid-symbolic")
        )
        self.add_prefix(icon)

        # The context you are actually in is marked the way a browser marks the
        # selected tab, so it is obvious at a glance which one is current.
        if is_active:
            self.add_css_class("accent")
            # Bold means the title is markup now, so its own characters have to
            # be escaped or an "&" in a name is invalid markup and shows raw.
            self.set_title(f"<b>{GLib.markup_escape_text(ctx.title)}</b>")
            self.set_use_markup(True)

        # A context drifts as it is used — windows opened, moved, closed. The
        # button is the offer to keep how it looks now, and appears only while
        # there is something to keep, so its presence is the whole prompt.
        # A floppy disk rather than `document-save-symbolic`, which Adwaita
        # draws as an arrow into a tray — indistinguishable from a download.
        self.save = Gtk.Button(
            icon_name="media-floppy-symbolic", valign=Gtk.Align.CENTER
        )
        self.save.add_css_class("flat")
        self.save.add_css_class("accent")
        self.save.set_tooltip_text(
            "Gather these windows into a context of their own"
            if self.is_virtual
            else "Keep this context"
            if ctx.ephemeral
            else "Save these windows as this context's layout"
        )
        # Always offered while a context is unkept, not only once it has
        # drifted: an unsaved context has nothing to compare against, and the
        # button is the only way it stops being thrown away when it closes.
        self.save.set_visible(
            bool(on_save is not None and (is_drifted or ctx.ephemeral))
        )
        self.save.connect("clicked", lambda _b: on_save and on_save(ctx))

        # Beside the save, and shown under the same condition: they are the two
        # answers to one question, and offering only "keep this" made drifting
        # a one-way door — the way back was to close the context and reopen it.
        self.restore = Gtk.Button(
            icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER
        )
        self.restore.add_css_class("flat")
        self.restore.set_tooltip_text("Put these windows back where they were saved")
        self.restore.set_visible(
            bool(is_drifted and on_restore is not None and not self.is_virtual)
        )
        self.restore.connect("clicked", lambda _b: on_restore and on_restore(ctx))

        self.close = Gtk.Button(
            icon_name="media-playback-stop-symbolic", valign=Gtk.Align.CENTER
        )
        self.close.add_css_class("flat")
        self.close.set_tooltip_text(
            "Close these windows"
            if self.is_virtual
            else "Close and discard — this context has not been saved"
            if ctx.ephemeral
            else "Close this context, keeping it for later"
        )
        self.close.set_visible(bool(is_open and on_close is not None))
        self.close.connect("clicked", lambda _b: on_close and on_close(ctx))

        self.edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        self.edit.add_css_class("flat")
        self.edit.set_tooltip_text("Edit this context")
        # Nothing to edit about a context that is only a name for what has no
        # context: it has no definition until it is saved as one.
        self.edit.set_visible(on_edit is not None)
        self.edit.connect("clicked", lambda _b: on_edit and on_edit(ctx))

        # Only the ones that will show, then joined — see `link_suffixes`.
        for button in (self.save, self.restore, self.close, self.edit):
            if button.get_visible():
                self.add_suffix(button)
        self.link_suffixes()

        self.connect("activated", lambda _r: on_open(ctx))

        # Everything the row can do, on the button the desktop reserves for
        # exactly that question. The two suffix buttons only have room for the
        # common pair, and at rail width there is room for none of them.
        secondary = Gtk.GestureClick()
        secondary.set_button(Gdk.BUTTON_SECONDARY)
        secondary.connect("pressed", self._on_secondary)
        self.add_controller(secondary)

    def _on_secondary(self, _gesture, _n_press, x: float, y: float) -> None:
        self.open_menu(x, y)

    def open_menu(self, x: float = 0.0, y: float = 0.0) -> Gtk.Popover:
        """Everything this context can do, on the button the desktop reserves
        for exactly that question."""
        popover, item, self.menu_items = _row_menu(self, x, y)

        item("open", "Switch to" if self.is_open else "Open", self._menu(self.on_open))
        if self.on_add_app is not None:
            item("add-app", "Open app here…", self._menu(self.on_add_app))
        if self.on_edit is not None:
            item("edit", "Edit…", self._menu(self.on_edit))
        if self.on_save is not None and (self.is_drifted or self.ctx.ephemeral):
            item(
                "save",
                "Save as a context"
                if self.is_virtual
                else "Keep this context"
                if self.ctx.ephemeral
                else "Save these windows",
                self._menu(self.on_save),
            )
        if self.on_restore is not None and self.is_drifted and not self.is_virtual:
            item("restore", "Put the windows back", self._menu(self.on_restore))
        if self.is_open and self.on_close is not None:
            item(
                "close",
                "Close and discard" if self.ctx.ephemeral else "Close",
                self._menu(self.on_close),
                destructive=bool(self.ctx.ephemeral),
            )
        if self.on_delete is not None:
            # Two steps, the way the editor asks: the menu is deliberate, but
            # one click either side of "Close" should not lose the context.
            forget = item("delete", "Delete…", lambda: None, destructive=True)
            confirm = item(
                "confirm", "Really delete", self._menu(self.on_delete), destructive=True
            )
            confirm.set_visible(False)
            keep = item("keep", "Keep", lambda: popover.popdown())
            keep.set_visible(False)

            def ask() -> None:
                forget.set_visible(False)
                confirm.set_visible(True)
                keep.set_visible(True)

            forget.connect("clicked", lambda _b: ask())

        self.menu = popover
        popover.popup()
        return popover

    def _menu(self, callback):
        """Run a row action and put the menu away."""

        def run() -> None:
            menu = getattr(self, "menu", None)
            if menu is not None:
                menu.popdown()
            if callback is not None:
                callback(self.ctx)

        return run


class AppRow(widgets.ActionRow):
    """An installed application, one click from being open.

    Where it lands is asked on the row rather than set in advance. It was a
    setting — every app opened in a new context, or every app joined the current
    one — and the answer is per application rather than per person: a browser
    you are opening to look something up belongs in what you are already doing,
    and the editor you are about to work in does not.
    """

    def __init__(
        self,
        info: App,
        on_new,
        on_current=None,
        into: str = "",
        buttons: bool = True,
    ) -> None:
        super().__init__()
        self.app_info = info
        # The context the second answer means, by name. It used to read "this
        # context", which stopped being true the moment the overview became a
        # place of its own: standing on it, "here" is the overview, and the app
        # is going somewhere else.
        self.into = into
        self.set_title(info.name)
        self.set_subtitle(info.description or "")
        self.set_activatable(True)

        icon = (
            Gtk.Image.new_from_gicon(info.icon)
            if info.icon is not None
            else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
        )
        self.add_prefix(icon)

        # The pair of answers, where there is room to draw them. The overview
        # leaves them off: a full screen of rows carrying two small icons each
        # is a lot of furniture for a question most rows are never asked, and
        # the right-click menu says both in words for every one of them.
        pair = _open_buttons(info, on_new, on_current, into)
        self.here, self.fresh = pair.here, pair.fresh
        if buttons:
            self.add_suffix(pair)

        # Activating the row takes the answer that always works: with no context
        # open there is nothing to add to, so a new one is the only one of the
        # two that is always available.
        self.connect("activated", lambda _r: on_new(info))

        self.on_new, self.on_current = on_new, on_current
        secondary = Gtk.GestureClick()
        secondary.set_button(Gdk.BUTTON_SECONDARY)
        secondary.connect("pressed", lambda _g, _n, x, y: self.open_menu(x, y))
        self.add_controller(secondary)

    def open_menu(self, x: float = 0.0, y: float = 0.0) -> Gtk.Popover:
        """The same two answers the buttons give, in words.

        A right-click means the same thing on both cards, which is the point of
        it being here: the context row has had a menu since it had actions, and
        reaching for one on an application and getting nothing is the kind of
        gap that reads as the row being inert.
        """
        popover, item, self.menu_items = _row_menu(self, x, y)

        def run(action):
            def go() -> None:
                popover.popdown()
                action(self.app_info)

            return go

        item("new", "Open in a new context", run(self.on_new))
        if self.on_current is not None:
            item(
                "here",
                f"Open in “{self.into}”" if self.into else "Open in this context",
                run(self.on_current),
            )

        self.menu = popover
        popover.popup()
        return popover


class AddAppRow(widgets.ActionRow):
    """An application row with one answer: activating it adds this one.

    Two screens ask that. The editor adds a window to the layout being edited
    and wants the count back; the "open app here" picker adds one to a context
    that already exists and has nothing to count. Where an application should
    open is a *second* question, and only the overview asks it.
    """

    def __init__(
        self,
        info: App,
        on_add,
        count: int = 0,
        tooltip: str = "",
        icon_name: str = "list-add-symbolic",
    ) -> None:
        super().__init__()
        self.app_info = info
        self.on_add = on_add
        self.set_title(info.name)
        self.set_subtitle(info.description or "")
        self.set_activatable(True)

        icon = (
            Gtk.Image.new_from_gicon(info.icon)
            if info.icon is not None
            else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
        )
        self.add_prefix(icon)

        # How many windows of this app the layout holds, which is the only
        # thing the editor's copy of a row has to report. Blank at zero rather
        # than "0 in layout": every row would otherwise carry a count of
        # nothing, and the ones that matter would stop standing out.
        self.badge = Gtk.Label()
        self.badge.add_css_class("caption")
        self.badge.add_css_class("dim-label")
        self.add_suffix(self.badge)

        self.add = Gtk.Button(icon_name=icon_name, valign=Gtk.Align.CENTER)
        self.add.set_tooltip_text(tooltip or f"Add {info.name} to the layout")
        self.add.connect("clicked", lambda _b: on_add(info))
        self.add_suffix(self.add)

        self.connect("activated", lambda _r: on_add(info))
        self.refresh(count)

    def refresh(self, count: int) -> None:
        self.badge.set_label(f"{count} in layout" if count else "")
        if count:
            self.add_css_class("ctx-chosen")
        else:
            self.remove_css_class("ctx-chosen")


# What the two ways of opening an application look like, wherever it is drawn.
# One pair of icons and one pair of words, so both views teach the same thing
# rather than each inventing its own.
#
# Run it where you are, against send it somewhere of its own.
#
# A play triangle rather than an arrow for the first: the question is what
# happens to the application, and "run it" is what happens — `go-jump-symbolic`
# is a hooked arrow that reads as navigating somewhere instead. Adwaita has no
# rocket, and of what it does have this is the only glyph that means start.
#
# An arrow leaving an open box for the second. A plus said "one more of these",
# which is true of the context and not of the application; the arrow says where
# the application goes. `document-send-symbolic` keeps its arrow inside a closed
# square and `system-log-out-symbolic` reads as leaving rather than sending —
# compared by rendering all three, not by their names.
#
# The play triangle also marks a running context in `ContextRow`'s prefix. That
# is a status rather than a button, on the other side of the row, and both uses
# mean the same thing by it.
OPEN_HERE_ICON = "media-playback-start-symbolic"
OPEN_NEW_ICON = "send-to-symbolic"


def _open_buttons(info: App, on_new, on_current, into: str = "") -> Gtk.Box:
    """The choice of where an application opens, as the row and the tile share."""
    box = Gtk.Box(spacing=0, halign=Gtk.Align.CENTER)
    box.add_css_class("linked")

    here = Gtk.Button(icon_name=OPEN_HERE_ICON, valign=Gtk.Align.CENTER)
    here.set_tooltip_text(
        f"Open “{info.name}” in “{into}”"
        if into
        else f"Open “{info.name}” in this context"
    )
    here.connect("clicked", lambda _b: on_current and on_current(info))
    # Only where there is a context to open it in. Without one the button would
    # mean the same as its neighbour and say something untrue. Set as well as
    # skipped, so asking the button whether it shows gets the truth — an
    # unparented widget reports itself visible.
    here.set_visible(on_current is not None)
    if on_current is not None:
        box.append(here)

    fresh = Gtk.Button(icon_name=OPEN_NEW_ICON, valign=Gtk.Align.CENTER)
    fresh.set_tooltip_text(f"Open a new “{info.name}” context")
    fresh.connect("clicked", lambda _b: on_new(info))
    box.append(fresh)

    box.here, box.fresh = here, fresh
    return box


def app_tile(info: App, on_pick, tooltip: str | None = None) -> Gtk.FlowBoxChild:
    """One application in a grid: its icon over its name, clickable.

    Only the "open app here" picker draws these now. It asks one question — pick
    an application — so a tile is the right shape for it; anywhere the answer is
    *where* an application should open lists `AppRow` instead, which has room
    for the two buttons and for what the application is.
    """
    child = Gtk.FlowBoxChild()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.add_css_class("ctx-tile")
    box.set_size_request(104, 104)
    for setter in (
        "set_margin_top",
        "set_margin_bottom",
        "set_margin_start",
        "set_margin_end",
    ):
        getattr(box, setter)(4)

    icon = (
        Gtk.Image.new_from_gicon(info.icon)
        if info.icon is not None
        else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
    )
    icon.set_pixel_size(40)
    # Centred as a cluster, the same slack-absorbing shape as the editor's
    # tiles: a box stacks from the top otherwise.
    icon.set_vexpand(True)
    icon.set_valign(Gtk.Align.END)
    box.append(icon)

    name = Gtk.Label(
        label=info.name, wrap=True, lines=2, justify=Gtk.Justification.CENTER
    )
    name.set_ellipsize(3)  # Pango.EllipsizeMode.END
    name.add_css_class("caption")
    name.set_vexpand(True)
    name.set_valign(Gtk.Align.START)
    box.append(name)

    child.set_tooltip_text(tooltip or f"Open a new “{info.name}” context")
    child.app_info = info
    click = Gtk.GestureClick()
    click.connect("released", lambda *_a: on_pick(info))
    box.add_controller(click)

    child.set_child(box)
    return child


def context_for_app(store: ContextStore, info: App) -> Context:
    """A saved context grown around one app, ready to open.

    Named after the app rather than left blank: this is the fast path from "I
    want to do something" to doing it, and a naming step would make it slower
    than typing the name in the first place.
    """
    ctx = store.create(info.name, resources=[Resource(app_id=info.id)])
    ctx.layout = preset_for(1)
    store.save()
    return ctx
