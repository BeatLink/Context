"""The pieces the sidebar and the overview both show.

They are two views of the same thing — the contexts you have and the apps you
could start one from — so a context has to look and behave the same in both.
Keeping the rows here is what stops the two drifting into different feature
sets, which is exactly what happened while the overview grew its own.
"""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, Gtk

from . import widgets
from .apps import App
from .layout import preset_for
from .resources import Resource
from .launcher import is_no_context
from .store import Context, ContextStore


def relative_time(stamp: float) -> str:
    delta = max(0, int(time.time() - stamp))
    if delta < 60:
        return "just now"
    for unit, seconds in (("d", 86400), ("h", 3600), ("m", 60)):
        if delta >= seconds:
            return f"{delta // seconds}{unit} ago"
    return "just now"


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
        on_forget=None,
        on_add_app=None,
        on_save=None,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.is_open = is_open
        self.is_active = is_active
        self.is_drifted = is_drifted
        self.on_open = on_open
        self.on_edit = on_edit
        self.on_close = on_close
        self.on_forget = on_forget
        self.on_add_app = on_add_app
        self.on_save = on_save
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
                subtitle.append("ephemeral")
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
            else "Save these windows as this context's layout"
        )
        self.save.set_visible(bool(is_drifted and on_save is not None))
        self.save.connect("clicked", lambda _b: on_save and on_save(ctx))
        self.add_suffix(self.save)

        self.close = Gtk.Button(
            icon_name="media-playback-stop-symbolic", valign=Gtk.Align.CENTER
        )
        self.close.add_css_class("flat")
        self.close.set_tooltip_text(
            "Close these windows"
            if self.is_virtual
            else "Close this context, keeping it for later"
        )
        self.close.set_visible(bool(is_open and on_close is not None))
        self.close.connect("clicked", lambda _b: on_close and on_close(ctx))
        self.add_suffix(self.close)

        self.edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        self.edit.add_css_class("flat")
        self.edit.set_tooltip_text("Edit this context")
        # Nothing to edit about a context that is only a name for what has no
        # context: it has no definition until it is saved as one.
        self.edit.set_visible(on_edit is not None)
        self.edit.connect("clicked", lambda _b: on_edit and on_edit(ctx))
        self.add_suffix(self.edit)

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
        """The row's menu, built where it was asked for.

        Built per opening rather than kept on the row: rows are thrown away and
        rebuilt on every refresh, and a popover parented to a dead row is a
        warning at best. Unparenting on close keeps the tree clean.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        popover = Gtk.Popover()
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        popover.set_child(box)
        popover.set_parent(self)
        at = Gdk.Rectangle()
        at.x, at.y, at.width, at.height = int(x), int(y), 1, 1
        popover.set_pointing_to(at)
        popover.connect("closed", lambda p: p.unparent())

        self.menu_items: dict[str, Gtk.Button] = {}

        def item(key: str, label: str, action, destructive: bool = False):
            button = Gtk.Button(label=label)
            button.set_has_frame(False)
            button.get_child().set_xalign(0.0)
            if destructive:
                button.add_css_class("destructive-action")
            button.connect("clicked", lambda _b: action())
            box.append(button)
            self.menu_items[key] = button
            return button

        item("open", "Switch to" if self.is_open else "Open", self._menu(self.on_open))
        if self.on_add_app is not None:
            item("add-app", "Open app here…", self._menu(self.on_add_app))
        if self.on_edit is not None:
            item("edit", "Edit…", self._menu(self.on_edit))
        if self.on_save is not None and self.is_drifted:
            item(
                "save",
                "Save as a context" if self.is_virtual else "Save these windows",
                self._menu(self.on_save),
            )
        if self.is_open and self.on_close is not None:
            item("close", "Close", self._menu(self.on_close))
        if self.on_forget is not None:
            # Two steps, the way the editor asks: the menu is deliberate, but
            # one click either side of "Close" should not lose the context.
            forget = item("forget", "Forget…", lambda: None, destructive=True)
            confirm = item(
                "confirm", "Really forget", self._menu(self.on_forget), destructive=True
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
    """An installed application, one activation from being a context.

    The overview draws the same thing as a tile; a sidebar has no room for a
    grid, so the list gets rows. Both do the same thing when clicked.
    """

    def __init__(self, info: App, on_open) -> None:
        super().__init__()
        self.app_info = info
        self.set_title(info.name)
        self.set_subtitle(info.description or "New context")
        self.set_activatable(True)

        icon = (
            Gtk.Image.new_from_gicon(info.icon)
            if info.icon is not None
            else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
        )
        self.add_prefix(icon)
        self.set_tooltip_text(f"Open a new “{info.name}” context")
        self.connect("activated", lambda _r: on_open(info))


def app_tile(info: App, on_pick, tooltip: str | None = None) -> Gtk.FlowBoxChild:
    """One application in a grid: its icon over its name, clickable.

    The overview grid and the "open app here" picker are the same grid asked
    two different questions, so they draw the same tile.
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
