"""The full-screen context editor: a layout preview above, an app grid below."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk

from .adapters import configurable, describe
from .apps import App, installed_apps, search_apps
from .logging_setup import get_logger
from .layout import PRESET_LABELS, PRESETS, Layout, Slot, preset_for, snap
from .resource_page import ResourcePage
from .resources import Resource
from .store import Context

log = get_logger("editor")

HANDLE = 14  # px hit area for the resize grips

# The app grid draws its own tiles rather than using list rows, so it does not
# pick up the dark styling libadwaita applies elsewhere: the cards came out
# white on a dark page and the add buttons were nearly invisible against them.
GRID_CSS = b"""
.ctx-tile {
    background-color: alpha(currentColor, 0.06);
    border: 1px solid alpha(currentColor, 0.12);
    border-radius: 10px;
}
.ctx-tile:hover {
    background-color: alpha(currentColor, 0.12);
}
.ctx-tile.ctx-chosen {
    background-color: alpha(@accent_bg_color, 0.22);
    border-color: @accent_bg_color;
}
.ctx-tile button {
    min-width: 28px;
    min-height: 28px;
    padding: 2px;
    background-color: alpha(currentColor, 0.14);
    border-radius: 999px;
}
.ctx-tile button:hover {
    background-color: @accent_bg_color;
    color: @accent_fg_color;
}
"""


class LayoutPreview(Gtk.DrawingArea):
    """A scale model of the monitor, one rectangle per window.

    Slots are dragged to move and edge-dragged to resize, both snapped to a 5%
    grid so windows line up. Colours come from the theme so it matches the shell.
    """

    def __init__(self, on_changed, on_remove, on_edit) -> None:
        super().__init__()
        self.layout = Layout()
        self.entries: list[tuple[object, str]] = []  # (Gio.Icon | None, name)
        self.on_changed = on_changed
        self.on_remove = on_remove
        self.on_edit = on_edit
        self._textures: dict[int, object] = {}
        self.active: int | None = None
        self._drag: tuple[str, int, float, float, Slot] | None = None

        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self._draw)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", lambda *_a: self._end_drag())
        self.add_controller(drag)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

    def set_layout(self, layout: Layout, entries: list) -> None:
        self.layout = layout
        self.entries = entries
        self._textures.clear()
        self.set_tooltip_text(None)
        self.queue_draw()

    def _screen(self) -> tuple[float, float, float, float]:
        """The monitor rectangle inside the widget, keeping 16:9."""
        width = self.get_width()
        height = self.get_height()
        margin = 12.0
        avail_w = max(1.0, width - margin * 2)
        avail_h = max(1.0, height - margin * 2)
        ratio = 16 / 9
        if avail_w / avail_h > ratio:
            h = avail_h
            w = h * ratio
        else:
            w = avail_w
            h = w / ratio
        return (width - w) / 2, (height - h) / 2, w, h

    def _slot_rect(self, slot: Slot) -> tuple[float, float, float, float]:
        ox, oy, sw, sh = self._screen()
        return ox + slot.x * sw, oy + slot.y * sh, slot.width * sw, slot.height * sh

    def _hit(self, px: float, py: float) -> tuple[str, int] | None:
        """What is under the pointer: a slot edge, a slot body, or nothing."""
        for index in reversed(range(len(self.layout.slots))):
            x, y, w, h = self._slot_rect(self.layout.slots[index])
            if not (x - HANDLE <= px <= x + w + HANDLE and y - HANDLE <= py <= y + h + HANDLE):
                continue
            # Close hotspot, top right of the slot
            if x + w - 22 <= px <= x + w - 4 and y + 4 <= py <= y + 22:
                return "close", index
            # Edit hotspot, immediately left of it: settings belong on the window
            # they apply to rather than in the grid below.
            if x + w - 44 <= px <= x + w - 26 and y + 4 <= py <= y + 22:
                return "edit", index
            right = abs(px - (x + w)) <= HANDLE
            bottom = abs(py - (y + h)) <= HANDLE
            if right and bottom:
                return "resize-both", index
            if right:
                return "resize-x", index
            if bottom:
                return "resize-y", index
            if x <= px <= x + w and y <= py <= y + h:
                return "move", index
        return None

    def _on_motion(self, _c, x: float, y: float) -> None:
        hit = self._hit(x, y)
        cursors = {
            "resize-both": "se-resize",
            "resize-x": "e-resize",
            "resize-y": "s-resize",
            "move": "grab",
            "close": "pointer",
            "edit": "pointer",
        }
        self.set_cursor(Gdk.Cursor.new_from_name(cursors.get(hit[0], "default"), None) if hit else None)
        active = hit[1] if hit else None
        if active != self.active:
            self.active = active
            # The slot shows an icon, so the name is surfaced on hover instead.
            if active is not None and active < len(self.entries):
                self.set_tooltip_text(self.entries[active][1])
            else:
                self.set_tooltip_text(None)
            self.queue_draw()

    def _on_drag_begin(self, _g, x: float, y: float) -> None:
        hit = self._hit(x, y)
        if hit is None:
            self._drag = None
            return
        mode, index = hit
        if mode == "close":
            self._drag = None
            self.on_remove(index)
            return
        if mode == "edit":
            self._drag = None
            self.on_edit(index)
            return
        self._drag = (mode, index, x, y, self.layout.slots[index])

    def _on_drag_update(self, _g, dx: float, dy: float) -> None:
        if self._drag is None:
            return
        mode, index, _sx, _sy, start = self._drag
        _ox, _oy, sw, sh = self._screen()
        fx, fy = dx / sw, dy / sh

        if mode == "move":
            slot = Slot(
                x=snap(min(max(0.0, start.x + fx), 1.0 - start.width)),
                y=snap(min(max(0.0, start.y + fy), 1.0 - start.height)),
                width=start.width,
                height=start.height,
            )
        else:
            width = start.width + fx if mode in ("resize-x", "resize-both") else start.width
            height = start.height + fy if mode in ("resize-y", "resize-both") else start.height
            slot = Slot(
                x=start.x,
                y=start.y,
                width=snap(min(max(0.05, width), 1.0 - start.x)) or 0.05,
                height=snap(min(max(0.05, height), 1.0 - start.y)) or 0.05,
            )

        slots = list(self.layout.slots)
        slots[index] = slot
        self.layout = Layout(slots=slots)
        self.queue_draw()

    def _end_drag(self) -> None:
        if self._drag is not None:
            self._drag = None
            self.on_changed(self.layout)

    def _draw_icon(self, cr, index: int, x: float, y: float, w: float, h: float) -> None:
        """The app's icon, centred in its slot. Names live in the tooltip."""
        entry = self.entries[index] if index < len(self.entries) else None
        icon, name = entry if entry else (None, f"Window {index + 1}")

        size = int(min(64, max(24, min(w, h) * 0.4)))
        paintable = self._textures.get(index)
        if paintable is None:
            paintable = _icon_texture(icon, size)
            if paintable is not None:
                self._textures[index] = paintable

        if paintable is not None:
            cr.save()
            cr.translate(x + (w - size) / 2, y + (h - size) / 2)
            snapshot = Gtk.Snapshot()
            paintable.snapshot(snapshot, size, size)
            node = snapshot.to_node()
            if node is not None:
                node.draw(cr)
            cr.restore()
            return

        # No icon available: fall back to the name so the slot is still legible.
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.select_font_face("Sans")
        cr.set_font_size(12)
        extents = cr.text_extents(name)
        if extents.width < w - 12:
            cr.move_to(x + (w - extents.width) / 2, y + h / 2 + 4)
            cr.show_text(name)

    def _draw(self, _area, cr, _w, _h) -> None:
        ox, oy, sw, sh = self._screen()

        # Monitor backdrop
        cr.set_source_rgba(0, 0, 0, 0.28)
        cr.rectangle(ox, oy, sw, sh)
        cr.fill()

        if not self.layout.slots:
            cr.set_source_rgba(1, 1, 1, 0.35)
            cr.select_font_face("Sans")
            cr.set_font_size(13)
            message = "Pick apps below to arrange them"
            extents = cr.text_extents(message)
            cr.move_to(ox + (sw - extents.width) / 2, oy + sh / 2)
            cr.show_text(message)
            return

        for index, slot in enumerate(self.layout.slots):
            x, y, w, h = self._slot_rect(slot)
            focused = index == self.active

            cr.set_source_rgba(0.35, 0.75, 0.75, 0.5 if focused else 0.32)
            cr.rectangle(x + 2, y + 2, max(1.0, w - 4), max(1.0, h - 4))
            cr.fill()

            cr.set_source_rgba(0.35, 0.75, 0.75, 1.0 if focused else 0.7)
            cr.set_line_width(2 if focused else 1)
            cr.rectangle(x + 2, y + 2, max(1.0, w - 4), max(1.0, h - 4))
            cr.stroke()

            self._draw_icon(cr, index, x, y, w, h)

            # Resize grip, bottom right
            cr.set_source_rgba(1, 1, 1, 0.5)
            cr.rectangle(x + w - 10, y + h - 10, 6, 6)
            cr.fill()

            # Edit affordance, left of the close button
            if w > 90 and h > 44:
                ex, ey = x + w - 35, y + 13
                cr.set_source_rgba(0, 0, 0, 0.35)
                cr.arc(ex, ey, 8, 0, 6.2832)
                cr.fill()
                cr.set_source_rgba(1, 1, 1, 0.85)
                cr.set_line_width(1.4)
                # A small pencil: a diagonal stroke with a tip
                cr.move_to(ex - 3, ey + 3)
                cr.line_to(ex + 3, ey - 3)
                cr.stroke()
                cr.move_to(ex - 4, ey + 4)
                cr.line_to(ex - 2.5, ey + 2)
                cr.line_to(ex - 1, ey + 3.5)
                cr.close_path()
                cr.fill()

            # Close affordance, top right
            if w > 60 and h > 44:
                cx, cy = x + w - 13, y + 13
                cr.set_source_rgba(0, 0, 0, 0.35)
                cr.arc(cx, cy, 8, 0, 6.2832)
                cr.fill()
                cr.set_source_rgba(1, 1, 1, 0.85)
                cr.set_line_width(1.6)
                cr.move_to(cx - 3.5, cy - 3.5); cr.line_to(cx + 3.5, cy + 3.5)
                cr.move_to(cx + 3.5, cy - 3.5); cr.line_to(cx - 3.5, cy + 3.5)
                cr.stroke()


_grid_css_loaded = False


def _install_grid_css() -> None:
    """Load the app-grid stylesheet once per display."""
    global _grid_css_loaded
    if _grid_css_loaded:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(GRID_CSS)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _grid_css_loaded = True


def _icon_texture(icon, size: int):
    """Render a Gio.Icon to a texture, or None if it cannot be looked up."""
    if icon is None:
        return None
    display = Gdk.Display.get_default()
    if display is None:
        return None
    theme = Gtk.IconTheme.get_for_display(display)
    paintable = theme.lookup_by_gicon(
        icon, size, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.FORCE_REGULAR
    )
    return paintable


class AppTile(Gtk.FlowBoxChild):
    """One app in the grid, with an explicit button to add it to the layout.

    Following PowerToys Workspaces: the grid is a catalogue you add *from*, rather
    than a set of checkboxes. Adding puts a window in the preview above; the count
    badge shows how many copies of the app the context holds.
    """

    def __init__(self, app: App, count: int, on_add, on_configure) -> None:
        super().__init__()
        self.app = app

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_size_request(104, 118)
        box.add_css_class("ctx-tile")
        self.box = box
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        image = (
            Gtk.Image.new_from_gicon(app.icon)
            if app.icon is not None
            else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
        )
        image.set_pixel_size(40)
        image.set_margin_top(8)
        box.append(image)

        name = Gtk.Label(label=app.name, wrap=True, lines=2, justify=Gtk.Justification.CENTER)
        name.set_ellipsize(3)  # Pango.EllipsizeMode.END
        name.add_css_class("caption")
        box.append(name)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, halign=Gtk.Align.CENTER)
        buttons.set_margin_bottom(6)

        self.add_button = Gtk.Button(icon_name="list-add-symbolic")
        self.add_button.add_css_class("flat")
        self.add_button.set_tooltip_text(f"Add {app.name} to the layout")
        self.add_button.connect("clicked", lambda _b: on_add(app))
        buttons.append(self.add_button)

        self.configure_button = Gtk.Button(icon_name="document-edit-symbolic")
        self.configure_button.add_css_class("flat")
        self.configure_button.set_tooltip_text(f"Choose what {app.name} opens")
        self.configure_button.connect("clicked", lambda _b: on_configure(app))
        buttons.append(self.configure_button)

        box.append(buttons)

        self.badge = Gtk.Label()
        self.badge.add_css_class("caption")
        self.badge.add_css_class("dim-label")
        box.append(self.badge)

        self.set_child(box)
        self.refresh(count, None)

    def refresh(self, count: int, resource: Resource | None) -> None:
        self.badge.set_label(f"{count} in layout" if count else "")
        self.configure_button.set_visible(
            resource is not None and configurable(resource)
        )
        if count:
            self.box.add_css_class("ctx-chosen")
        else:
            self.box.remove_css_class("ctx-chosen")


class EditorPage(Adw.NavigationPage):
    """Full-screen page for creating or editing a context."""

    def __init__(
        self, ctx: Context, on_done, on_cancel, on_delete=None, is_new: bool = False
    ) -> None:
        super().__init__(title=ctx.title or "New context")
        self.ctx = ctx
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.on_delete = on_delete
        self.is_new = is_new
        _install_grid_css()
        self.apps = installed_apps()
        # An ordered list rather than a set: a context may hold two terminals, and
        # position in this list is what maps a resource to a layout slot.
        self.entries: list[Resource] = [
            Resource(**{k: v for k, v in r.__dict__.items()}) for r in ctx.resources
        ]
        self.layout = (
            Layout(slots=list(ctx.layout.slots))
            if ctx.layout.slots
            else preset_for(len(self.entries))
        )

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        # No minimise or close: the editor is a fullscreen overlay, and Cancel
        # and Save are the only two ways out of it.
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.on_cancel())
        header.pack_start(cancel)

        self.done_button = Gtk.Button(label="Start" if is_new else "Save")
        self.done_button.add_css_class("suggested-action")
        self.done_button.connect("clicked", lambda _b: self._commit())
        header.pack_end(self.done_button)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(18)
        content.set_margin_end(18)

        # --- title and options -------------------------------------------------
        details = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        details.add_css_class("boxed-list")

        self.title_row = Adw.EntryRow(title="What are you doing?")
        self.title_row.set_text(ctx.title)
        self.title_row.connect("changed", lambda _e: self._update_state())
        details.append(self.title_row)

        ephemeral_row = Adw.ActionRow(
            title="Ephemeral",
            subtitle="Discard this context after use",
        )
        self.ephemeral_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.ephemeral_switch.set_active(ctx.ephemeral)
        ephemeral_row.add_suffix(self.ephemeral_switch)
        ephemeral_row.set_activatable_widget(self.ephemeral_switch)
        details.append(ephemeral_row)

        # Forgetting a context lives here rather than beside its launch button,
        # so it takes opening the editor and a confirmation to lose one.
        if on_delete is not None and not is_new:
            delete_row = Adw.ActionRow(
                title="Forget this context",
                subtitle="Removes the definition. Windows it opened are left alone.",
            )
            delete_button = Gtk.Button(label="Forget", valign=Gtk.Align.CENTER)
            delete_button.add_css_class("destructive-action")
            delete_button.connect("clicked", lambda _b: self._confirm_delete())
            delete_row.add_suffix(delete_button)
            details.append(delete_row)

        content.append(details)

        # --- layout preview, top half -----------------------------------------
        layout_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading = Gtk.Label(label="Window layout", xalign=0.0, hexpand=True)
        heading.add_css_class("heading")
        layout_header.append(heading)

        self.preset_dropdown = Gtk.DropDown.new_from_strings(
            [PRESET_LABELS[k] for k in PRESETS]
        )
        self.preset_dropdown.set_tooltip_text("Start from an arrangement")
        self.preset_dropdown.connect("notify::selected", self._on_preset_selected)
        layout_header.append(self.preset_dropdown)
        content.append(layout_header)

        hint = Gtk.Label(
            label="Drag a window to move it, or its bottom-right corner to resize.",
            xalign=0.0,
            wrap=True,
        )
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        content.append(hint)

        self.preview = LayoutPreview(
            self._on_layout_changed, self._on_remove, self._on_edit_slot
        )
        preview_frame = Gtk.Frame()
        preview_frame.set_vexpand(True)
        preview_frame.set_child(self.preview)
        content.append(preview_frame)

        # --- app grid, bottom half --------------------------------------------
        apps_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.count_label = Gtk.Label(xalign=0.0, hexpand=True)
        self.count_label.add_css_class("heading")
        apps_header.append(self.count_label)

        self.search = Gtk.SearchEntry(placeholder_text="Search apps…")
        self.search.set_size_request(240, -1)
        self.search.connect("search-changed", lambda _e: self.refresh_apps())
        apps_header.append(self.search)
        content.append(apps_header)

        self.flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            min_children_per_line=4,
            max_children_per_line=10,
            row_spacing=4,
            column_spacing=4,
        )
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_size_request(-1, 240)
        scroller.set_child(self.flow)
        content.append(scroller)

        toolbar.set_content(content)
        self.set_child(toolbar)

        self.refresh_apps()
        self._update_state()

    # -- state ---------------------------------------------------------------

    def current_title(self) -> str:
        return self.title_row.get_text().strip()

    def _ordered_resources(self) -> list[Resource]:
        return list(self.entries)

    def _preview_entries(self) -> list[tuple[object, str]]:
        """(icon, name) per window, for the preview to draw and label."""
        by_id = {a.id: a for a in self.apps}
        out = []
        for resource in self.entries:
            app = by_id.get(resource.app_id)
            name = app.name if app else resource.app_id.removesuffix(".desktop")
            out.append((app.icon if app else None, name))
        return out

    def _count_of(self, app_id: str) -> int:
        return sum(1 for r in self.entries if r.app_id == app_id)

    def _update_state(self) -> None:
        count = len(self.entries)
        self.count_label.set_label(
            f"Apps · {count} selected" if count else "Apps · none selected yet"
        )
        self.done_button.set_sensitive(bool(self.current_title()))
        self.preview.set_layout(self.layout, self._preview_entries())

    def _on_layout_changed(self, layout: Layout) -> None:
        self.layout = layout

    def _on_preset_selected(self, dropdown, _param) -> None:
        name = list(PRESETS)[dropdown.get_selected()]
        slots = list(PRESETS[name])
        # Keep one slot per selected app, padding or trimming the preset.
        count = max(1, len(self.entries))
        while len(slots) < count:
            slots.append(Slot())
        self.layout = Layout(slots=slots[:count])
        self._update_state()

    # -- apps ----------------------------------------------------------------

    def visible_tiles(self) -> list[AppTile]:
        tiles = []
        child = self.flow.get_first_child()
        while child is not None:
            if isinstance(child, AppTile):
                tiles.append(child)
            child = child.get_next_sibling()
        return tiles

    def refresh_apps(self) -> None:
        matches = search_apps(self.apps, self.search.get_text())
        self.flow.remove_all()
        for app in matches[:200]:
            self.flow.append(
                AppTile(app, self._count_of(app.id), self._on_add, self._on_configure)
            )

    def _refresh_tile(self, app_id: str) -> None:
        latest = next((r for r in reversed(self.entries) if r.app_id == app_id), None)
        for tile in self.visible_tiles():
            if tile.app.id == app_id:
                tile.refresh(self._count_of(app_id), latest)

    def _on_add(self, app: App) -> None:
        log.debug("adding %s to the layout", app.id)
        self.entries.append(Resource(app_id=app.id))
        # Keep exactly one slot per window in the layout.
        self.layout = self.layout.resized(len(self.entries))
        self._refresh_tile(app.id)
        self._update_state()

    def _on_remove(self, index: int) -> None:
        log.debug("removing window %d from the layout", index)
        if not (0 <= index < len(self.entries)):
            return
        app_id = self.entries[index].app_id
        del self.entries[index]
        self.layout = self.layout.resized(len(self.entries))
        self._refresh_tile(app_id)
        self._update_state()

    def _on_edit_slot(self, index: int) -> None:
        """Configure the window at `index` from the preview itself."""
        if not (0 <= index < len(self.entries)):
            return
        resource = self.entries[index]
        app = next((a for a in self.apps if a.id == resource.app_id), None)
        if app is None:
            return
        self._push_resource_page(app, resource)

    def _push_resource_page(self, app: App, resource: Resource) -> None:
        nav = self.get_parent()
        if not isinstance(nav, Adw.NavigationView):
            return
        self.resource_page = ResourcePage(app, resource, self._on_resource_done)
        nav.push(self.resource_page)

    def _on_configure(self, app: App) -> None:
        resource = next((r for r in reversed(self.entries) if r.app_id == app.id), None)
        if resource is None:
            resource = Resource(app_id=app.id)
            self.entries.append(resource)
            self.layout = self.layout.resized(len(self.entries))
        self._push_resource_page(app, resource)

    def _on_resource_done(self, resource: Resource) -> None:
        nav = self.get_parent()
        if isinstance(nav, Adw.NavigationView):
            nav.pop()
        self._refresh_tile(resource.app_id)
        self._update_state()

    # -- commit --------------------------------------------------------------

    def _confirm_delete(self) -> None:
        dialog = Adw.MessageDialog(
            heading=f"Forget “{self.ctx.title}”?",
            body="The context definition is removed. Any windows it opened stay open.",
            modal=True,
        )
        root = self.get_root()
        if root is not None:
            dialog.set_transient_for(root)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Forget")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect(
            "response",
            lambda _d, response: self.on_delete(self.ctx) if response == "delete" else None,
        )
        dialog.present()

    def _commit_cancel(self) -> None:
        self.on_cancel()

    def _commit(self) -> None:
        title = self.current_title()
        if not title:
            return
        log.info(
            "saving context %s: %d windows, %d slots",
            title, len(self.entries), len(self.layout.slots),
        )
        self.on_done(
            self.ctx,
            self._ordered_resources(),
            title,
            self.ephemeral_switch.get_active(),
            self.layout,
        )
