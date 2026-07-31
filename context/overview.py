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
from .layout import preset_for
from .logging_setup import get_logger
from .resources import Resource

log = get_logger("overview")


class OverviewWindow(Gtk.ApplicationWindow):
    """Contexts on one side, applications on the other, one search over both."""

    def __init__(self, app, store, backend=None) -> None:
        super().__init__(application=app, title="Overview")
        self.add_css_class("ctx-window")
        self.store = store
        self.backend = backend or backends.detect()
        self.apps = installed_apps()

        theme.install()
        self.set_default_size(1200, 720)
        if not sidebar.apply_overlay(self):
            self.fullscreen()

        toolbar = widgets.ToolbarView()
        header = widgets.HeaderBar(title="Overview")
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
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
        content.append(self.entry)

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
        open_ids, _active = open_state(self.store.contexts, backend=self.backend)

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

    def _context_row(self, ctx, is_open: bool) -> widgets.ActionRow:
        row = widgets.ActionRow()
        row.set_title(ctx.title)
        if ctx.apps:
            row.set_subtitle(f"{len(ctx.apps)} app{'s' if len(ctx.apps) != 1 else ''}")
        row.set_activatable(True)
        row.ctx = ctx
        icon = Gtk.Image.new_from_icon_name(
            "media-playback-start-symbolic" if is_open else "view-grid-symbolic"
        )
        row.add_prefix(icon)
        row.connect("activated", lambda _r, c=ctx: self._open_context(c))
        return row

    def _app_tile(self, info: App) -> Gtk.FlowBoxChild:
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

        child.set_tooltip_text(f"Open a new “{info.name}” context")
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a, i=info: self._open_app(i))
        box.add_controller(click)

        child.set_child(box)
        return child

    # -- acting --------------------------------------------------------------

    def _activate_first(self) -> None:
        for listbox in (self.open_list, self.saved_list):
            row = listbox.get_row_at_index(0)
            if row is not None and listbox.get_visible():
                self._open_context(row.ctx)
                return

    def _open_context(self, ctx) -> None:
        log.info("overview: opening context %s", ctx.title)
        self.close()
        self.on_context(ctx)

    def _open_app(self, info: App) -> None:
        """A context grown around one app, opened on the spot.

        Named after the app rather than left blank: the overview is the fast
        path, and a naming step would make it slower than the sidebar.
        """
        ctx = self.store.create(info.name, resources=[Resource(app_id=info.id)])
        ctx.layout = preset_for(1)
        self.store.save()
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
