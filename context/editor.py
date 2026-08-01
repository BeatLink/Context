"""The full-screen context editor: a layout preview above, an app grid below."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk


from .apps import App, installed_apps, search_apps
from . import isolation, monitors, settings, theme, widgets
from .logging_setup import get_logger
from .layout import PRESET_LABELS, PRESETS, Layout, Slot, preset_for, snap
from .resource_page import ResourcePage
from .resources import Resource
from .store import Context

log = get_logger("editor")

HANDLE = 14  # px hit area for the resize grips

# The app grid and the layout preview draw themselves, so libadwaita has no
# styling for them; colours come from the theme instead. See context/theme.py.


def _same_slot(one: Slot, other: Slot, tolerance: float = 0.01) -> bool:
    return all(
        abs(getattr(one, field) - getattr(other, field)) <= tolerance
        for field in ("x", "y", "width", "height")
    )


def _preset_glyph(slots: list[Slot]) -> Gtk.DrawingArea:
    """A thumbnail of an arrangement, for the button that applies it."""
    area = Gtk.DrawingArea()
    area.set_content_width(30)
    area.set_content_height(20)

    def draw(_area, cr, width: int, height: int) -> None:
        palette = theme.current()
        cr.set_source_rgba(*palette.rgba("preview_background"))
        cr.rectangle(0, 0, width, height)
        cr.fill()
        for slot in slots:
            x, y = slot.x * width, slot.y * height
            w, h = slot.width * width, slot.height * height
            cr.set_source_rgba(*palette.rgba("slot_fill"))
            cr.rectangle(x + 1, y + 1, max(1.0, w - 2), max(1.0, h - 2))
            cr.fill()
            cr.set_source_rgba(*palette.rgba("slot_border"))
            cr.set_line_width(1)
            cr.rectangle(x + 1, y + 1, max(1.0, w - 2), max(1.0, h - 2))
            cr.stroke()

    area.set_draw_func(draw)
    return area


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
        # Which screen this preview is, and how to hand a window to the next.
        # Set by the editor; a lone preview has neither.
        self.screen = 0
        self.indices: list[int] = []
        self.on_move_to_screen = None
        self.on_drag_toward = None
        self._last_x = 0.0
        # Which way the window under the pointer is about to leave, and
        # whether this screen is the one it would land on.
        self._leaving = 0
        self._drop_target = False
        # The shape the preview is drawn at. Read once rather than per frame,
        # since it costs a compositor query and does not change mid-edit.
        self.aspect = monitors.preview_aspect()

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
        """The monitor rectangle inside the widget, at the monitor's own shape.

        Not a fixed 16:9. A layout is fractions of whatever it opens on, so a
        preview drawn at the wrong aspect lies about where the windows land —
        badly on a 16:10 panel, absurdly on an ultrawide or a rotated one.
        """
        width = self.get_width()
        height = self.get_height()
        margin = 12.0
        avail_w = max(1.0, width - margin * 2)
        avail_h = max(1.0, height - margin * 2)
        ratio = self.aspect
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

    def _leaving_toward(self, pointer_x: float) -> int:
        """Which way a window is being dragged off this screen, if any.

        Measured against the *drawn* screen rectangle, not the widget. The
        preview is letterboxed to the monitor's shape and centred, so there is
        dead space beside it — using the widget edge meant dragging well past
        the picture of the screen before anything happened.
        """
        if self.on_move_to_screen is None:
            return 0
        ox, _oy, sw, _sh = self._screen()
        if pointer_x < ox:
            return -1
        if pointer_x > ox + sw:
            return 1
        return 0

    def _on_drag_update(self, _g, dx: float, dy: float) -> None:
        if self._drag is None:
            return
        mode, index, _sx, _sy, start = self._drag
        # Where the pointer is now, so `_end_drag` can tell whether it left.
        self._last_x = _sx + dx

        if mode == "move":
            leaving = self._leaving_toward(self._last_x)
            if leaving != self._leaving:
                self._leaving = leaving
                # Tell the editor, so the screen being dragged toward can light
                # up. Without it the gesture gives no sign of what it will do.
                if self.on_drag_toward is not None:
                    self.on_drag_toward(self.screen, leaving)
                self.queue_draw()
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
        if self._drag is None:
            return
        mode, index, _sx, _sy, _start = self._drag
        leaving = self._leaving
        self._drag = None
        self._leaving = 0
        if self.on_drag_toward is not None:
            self.on_drag_toward(self.screen, 0)
        self.queue_draw()

        # Dragged off one side: hand the window to the next screen. The
        # previews sit in monitor order, so pushing a window right is the same
        # gesture as pushing it right across the desk.
        if mode == "move" and leaving and self.on_move_to_screen is not None:
            self.on_move_to_screen(self.screen, index, leaving)
            return

        self.on_changed(self.layout)

    def set_drop_target(self, active: bool) -> None:
        """Light this screen up as where a dragged window would land."""
        if active != self._drop_target:
            self._drop_target = active
            self.queue_draw()

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

        palette = theme.current()

        # Monitor backdrop
        cr.set_source_rgba(*palette.rgba("preview_background"))
        cr.rectangle(ox, oy, sw, sh)
        cr.fill()

        if self._drop_target:
            # The screen a dragged window would land on.
            ox, oy, sw, sh = self._screen()
            cr.set_source_rgba(*palette.rgba("drop_target"))
            cr.rectangle(ox, oy, sw, sh)
            cr.fill()
            cr.set_source_rgba(*palette.rgba("leaving_border"))
            cr.set_line_width(3)
            cr.set_dash([8, 6])
            cr.rectangle(ox, oy, sw, sh)
            cr.stroke()
            cr.set_dash([])

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

            cr.set_source_rgba(
                *palette.rgba("slot_fill_active" if focused else "slot_fill")
            )
            cr.rectangle(x + 2, y + 2, max(1.0, w - 4), max(1.0, h - 4))
            cr.fill()

            cr.set_source_rgba(*palette.rgba("slot_border"))
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

    def __init__(self, app: App, count: int, on_add) -> None:
        super().__init__()
        self.app = app

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_size_request(104, 118)
        # Without this a FlowBox stretches its children to fill the height, so a
        # single row of apps grew to the full height of the grid.
        box.set_valign(Gtk.Align.START)
        box.set_vexpand(False)
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
        # The card is taller than its content so that two-line names fit, and a
        # box stacks from the top — which left the icon riding high over dead
        # space. The icon and the badge soak up the slack from either end, so
        # the cluster sits centred whatever the name's line count.
        image.set_vexpand(True)
        image.set_valign(Gtk.Align.END)
        box.append(image)

        name = Gtk.Label(label=app.name, wrap=True, lines=2, justify=Gtk.Justification.CENTER)
        name.set_ellipsize(3)  # Pango.EllipsizeMode.END
        name.add_css_class("caption")
        box.append(name)

        # No add button: the whole card is the target. Windows are configured
        # from the layout above, where the edit control sits on the window it
        # applies to, so the card only has one job.
        self.set_tooltip_text(f"Add {app.name} to the layout")
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a: on_add(app))
        box.add_controller(click)

        self.badge = Gtk.Label()
        self.badge.add_css_class("caption")
        self.badge.add_css_class("dim-label")
        self.badge.set_vexpand(True)
        self.badge.set_valign(Gtk.Align.START)
        box.append(self.badge)

        self.set_child(box)
        self.refresh(count, None)

    def refresh(self, count: int, resource: Resource | None) -> None:
        self.badge.set_label(f"{count} in layout" if count else "")
        if count:
            self.box.add_css_class("ctx-chosen")
        else:
            self.box.remove_css_class("ctx-chosen")


class EditorPage(widgets.NavigationPage):
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
        theme.install()
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

        toolbar = widgets.ToolbarView()
        header = widgets.HeaderBar()
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

        self.title_row = widgets.EntryRow(title="What are you doing?")
        self.title_row.set_text(ctx.title)
        self.title_row.connect("changed", lambda _e: self._update_state())
        details.append(self.title_row)

        ephemeral_row = widgets.ActionRow(
            title="Ephemeral",
            subtitle="Discard this context after use",
        )
        self.ephemeral_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.ephemeral_switch.set_active(ctx.ephemeral)
        ephemeral_row.add_suffix(self.ephemeral_switch)
        ephemeral_row.set_activatable_widget(self.ephemeral_switch)
        details.append(ephemeral_row)

        isolated_row = widgets.ActionRow(
            title="Isolated",
            subtitle=(
                "Apps here cannot see copies of themselves running elsewhere, so "
                "they open their own window instead of reusing one. Turn off for "
                "any app that shares a database with another context."
            ),
        )
        self.isolated_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.isolated_switch.set_active(ctx.isolated)
        self.isolated_switch.set_sensitive(isolation.available())
        if not isolation.available():
            isolated_row.set_subtitle("Needs dbus-run-session, which is not installed")
        isolated_row.add_suffix(self.isolated_switch)
        isolated_row.set_activatable_widget(self.isolated_switch)
        details.append(isolated_row)

        # Forgetting a context lives here rather than beside its launch button,
        # so it takes opening the editor and a confirmation to lose one. The
        # confirmation is the same row changing its buttons rather than a
        # dialog: there is nowhere sensible for a popup on a full-screen
        # overlay, and the answer buttons appearing where the click just
        # landed cannot be missed.
        if on_delete is not None and not is_new:
            delete_row = widgets.ActionRow(
                title="Forget this context",
                subtitle="Removes the definition. Windows it opened are left alone.",
            )
            self.delete_button = Gtk.Button(label="Forget", valign=Gtk.Align.CENTER)
            self.delete_button.add_css_class("destructive-action")
            self.delete_button.connect("clicked", lambda _b: self._ask_to_forget(True))
            delete_row.add_suffix(self.delete_button)

            self.keep_button = Gtk.Button(label="Keep", valign=Gtk.Align.CENTER)
            self.keep_button.set_visible(False)
            self.keep_button.connect("clicked", lambda _b: self._ask_to_forget(False))
            delete_row.add_suffix(self.keep_button)

            self.forget_button = Gtk.Button(
                label="Really forget", valign=Gtk.Align.CENTER
            )
            self.forget_button.add_css_class("destructive-action")
            self.forget_button.set_visible(False)
            self.forget_button.connect(
                "clicked", lambda _b: self.on_delete(self.ctx)
            )
            delete_row.add_suffix(self.forget_button)
            details.append(delete_row)

        content.append(details)

        # --- layout preview, top half -----------------------------------------
        layout_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading = Gtk.Label(label="Window layout", xalign=0.0, hexpand=True)
        heading.add_css_class("heading")
        layout_header.append(heading)

        # Filled in by `_sync_preset_choices`, since which arrangements make
        # sense depends on how many windows there are to arrange.
        self.preset_chooser = widgets.SegmentedChoice(self._on_preset_selected)
        self.preset_options: list[tuple[str, list[Slot]]] = []
        self._preset_for_count = -1
        layout_header.append(self.preset_chooser)
        content.append(layout_header)

        hint = Gtk.Label(
            label="Drag a window to move it, or its bottom-right corner to resize.",
            xalign=0.0,
            wrap=True,
        )
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        content.append(hint)

        # A context holds a layout per screen count, and this edits one of
        # them. The mode defaults to what is attached now, but any of them can
        # be edited from here — arranging the docked layout while undocked is
        # the whole point of keeping them separate.
        self.attached = max(1, len(monitors.all_monitors()))
        self.screen_count = min(self.attached, settings.current().max_screens)
        self.arrangement = ctx.arrangement_for(self.screen_count)
        self.previews: list[LayoutPreview] = []

        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mode_label = Gtk.Label(label="Layout for", xalign=0.0)
        mode_label.add_css_class("dim-label")
        mode_row.append(mode_label)

        self.mode_chooser = widgets.SegmentedChoice(self._on_mode_changed)
        for n in range(1, settings.current().max_screens + 1):
            self.mode_chooser.add(
                f"{n} screen{'s' if n != 1 else ''}"
                + (" · attached now" if n == self.attached else "")
            )
        self.mode_chooser.set_selected(self.screen_count - 1, notify=False)
        mode_row.append(self.mode_chooser)
        content.append(mode_row)

        self.screens_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.screens_box.set_vexpand(True)
        content.append(self.screens_box)
        self._build_previews()

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
            valign=Gtk.Align.START,
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

    def _build_previews(self) -> None:
        """One preview per screen in the mode being edited.

        The arrangement is grown to match first. A context that has only ever
        run on one screen carries a one-screen arrangement, and showing two
        previews over it meant the second could be dragged to but never
        assigned — `healed` would shrink it back on the next refresh.
        """
        self.arrangement.grow_to(self.screen_count)
        child = self.screens_box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.screens_box.remove(child)
            child = following

        self.previews = []
        attached = monitors.ordered()
        shapes = [m.aspect for m in attached]
        screens_box = self.screens_box
        for screen in range(self.screen_count):
            preview = LayoutPreview(
                lambda layout, s=screen: self._on_layout_changed(layout, s),
                lambda index, s=screen: self._on_remove(index, s),
                lambda index, s=screen: self._on_edit_slot(index, s),
            )
            if screen < len(shapes):
                preview.aspect = shapes[screen]
            preview.screen = screen
            preview.on_move_to_screen = self._move_to_screen
            preview.on_drag_toward = self._on_drag_toward
            self.previews.append(preview)

            frame = Gtk.Frame()
            frame.set_vexpand(True)
            frame.set_hexpand(True)
            frame.set_child(preview)

            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            # Named by number, with the monitor it currently means beside it.
            # The number is what the context stores; the name is only there so
            # it is obvious which physical screen that is today.
            caption = f"Screen {screen + 1}"
            if screen < len(attached):
                caption += f" · {attached[screen].name}"
            elif screen >= self.attached:
                caption += " · not attached"
            label = Gtk.Label(label=caption, xalign=0.0)
            label.add_css_class("dim-label")
            label.add_css_class("caption")
            column.append(label)
            column.append(frame)
            screens_box.append(column)

        # Kept for the code that still speaks in one layout; screen 0's.
        self.preview = self.previews[0]

    def _on_mode_changed(self, selected: int) -> None:
        """Switch to editing another screen mode, keeping what was edited."""
        self.ctx.set_arrangement(self.screen_count, self.arrangement)
        self.screen_count = selected + 1
        self.arrangement = self.ctx.arrangement_for(self.screen_count)
        log.debug("editing the %d-screen layout", self.screen_count)
        self._build_previews()
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
        self._sync_previews()

    def _sync_previews(self) -> None:
        """Give each screen its own slots and the windows assigned to it."""
        entries = self._preview_entries()
        healed, _ = self.arrangement.healed(len(self.entries))
        self.arrangement = healed
        for screen, preview in enumerate(self.previews):
            indices = healed.indices_on(screen)
            preview.indices = indices
            preview.set_layout(
                healed.layout_for(screen), [entries[i] for i in indices]
            )
        # Screen 0 stays the single-screen layout, so a context edited while
        # docked still opens sensibly undocked.
        self.layout = healed.layout_for(0)
        self._sync_preset_choices()

    def _sync_preset_choices(self) -> None:
        """Offer the arrangements that fit the windows on screen 1, and no others.

        A preset held a fixed number of slots and was padded or trimmed to the
        window count, so "Three columns" over two windows meant two thirds of a
        screen and a gap, and "Grid" meant the same thing as side by side. What
        cannot be arranged is not offered.
        """
        count = len(self.arrangement.indices_on(0))
        if count != self._preset_for_count:
            self._preset_for_count = count
            self.preset_options = [
                (PRESET_LABELS[name], list(slots))
                for name, slots in PRESETS.items()
                if len(slots) == count
            ]
            if not self.preset_options and count:
                # More windows than any named arrangement covers. The generated
                # grid is the only sensible starting point, so it is the only
                # thing offered rather than a row that would all be trimmed.
                self.preset_options = [
                    (f"Grid of {count}", list(preset_for(count).slots))
                ]
            self.preset_chooser.clear()
            for label, slots in self.preset_options:
                self.preset_chooser.add(_preset_glyph(slots), label)
        # Which one is in force — and none, once a slot has been dragged off it.
        self.preset_chooser.set_selected(self._matching_preset(), notify=False)

    def _matching_preset(self) -> int:
        """Which of the offered arrangements the first screen currently is."""
        slots = self.arrangement.layout_for(0).slots
        for index, (_label, option) in enumerate(self.preset_options):
            if len(option) == len(slots) and all(
                _same_slot(a, b) for a, b in zip(option, slots)
            ):
                return index
        return -1

    def _on_layout_changed(self, layout: Layout, screen: int = 0) -> None:
        screens = list(self.arrangement.screens)
        while len(screens) <= screen:
            screens.append(Layout())
        screens[screen] = layout
        self.arrangement.screens = screens
        if screen == 0:
            self.layout = layout

    def _on_drag_toward(self, screen: int, direction: int) -> None:
        """Light up the screen a dragged window would land on."""
        target = screen + direction if direction else None
        for index, preview in enumerate(self.previews):
            preview.set_drop_target(target is not None and index == target)

    def _move_to_screen(self, screen: int, local_index: int, direction: int) -> None:
        """Send a window to the neighbouring screen.

        Dragged past the edge of its preview rather than picked from a menu:
        the previews sit side by side in the same order as the monitors, so
        pushing a window right is the same gesture as pushing it right on the
        desk.
        """
        target = screen + direction
        if not 0 <= target < self.screen_count:
            return
        indices = self.arrangement.indices_on(screen)
        if not 0 <= local_index < len(indices):
            return
        resource_index = indices[local_index]
        self.arrangement.assign(resource_index, target)
        log.debug("moved window %d to screen %d", resource_index, target)
        for preview in self.previews:
            preview.set_drop_target(False)
        self._update_state()

    def _on_preset_selected(self, selected: int) -> None:
        if not 0 <= selected < len(self.preset_options):
            return
        label, slots = self.preset_options[selected]
        log.debug("arrangement %s chosen", label)
        # Only arrangements that hold exactly this many windows are offered, so
        # there is nothing to pad or trim. The choice applies to the screen it
        # was made on, which is the first — the others keep what they had.
        self._on_layout_changed(Layout(slots=list(slots)), 0)
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
                AppTile(app, self._count_of(app.id), self._on_add)
            )

    def _refresh_tile(self, app_id: str) -> None:
        latest = next((r for r in reversed(self.entries) if r.app_id == app_id), None)
        for tile in self.visible_tiles():
            if tile.app.id == app_id:
                tile.refresh(self._count_of(app_id), latest)

    def _on_add(self, app: App) -> None:
        log.debug("adding %s to the layout", app.id)
        self.entries.append(Resource(app_id=app.id))
        # New windows land on the screen with the fewest, so adding to a
        # two-screen context fills both rather than piling onto the first.
        counts = [
            len(self.arrangement.indices_on(s)) for s in range(self.screen_count)
        ]
        self.arrangement.assign(len(self.entries) - 1, counts.index(min(counts)))
        self._refresh_tile(app.id)
        self._update_state()

    def _on_remove(self, index: int, screen: int = 0) -> None:
        """Remove a window. `index` is its position on `screen`, not overall."""
        indices = self.arrangement.indices_on(screen)
        if not 0 <= index < len(indices):
            return
        resource_index = indices[index]
        log.debug("removing window %d from screen %d", resource_index, screen)
        app_id = self.entries[resource_index].app_id
        del self.entries[resource_index]
        # Assignments are by position, so everything after the hole shifts up.
        self.arrangement.assignments = {
            (i if i < resource_index else i - 1): s
            for i, s in self.arrangement.assignments.items()
            if i != resource_index
        }
        self._refresh_tile(app_id)
        self._update_state()

    def _on_edit_slot(self, index: int, screen: int = 0) -> None:
        """Configure the window at `index` on `screen` from the preview.

        `index` is the slot's position on its screen, not overall — the same
        mapping `_on_remove` does, and the same reason: each preview shows only
        its own screen's windows.
        """
        indices = self.arrangement.indices_on(screen)
        if not 0 <= index < len(indices):
            return
        resource = self.entries[indices[index]]
        app = next((a for a in self.apps if a.id == resource.app_id), None)
        if app is None:
            return
        self._push_resource_page(app, resource)

    def _push_resource_page(self, app: App, resource: Resource) -> None:
        nav = self.get_parent()
        if not isinstance(nav, widgets.NavigationView):
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
        if isinstance(nav, widgets.NavigationView):
            nav.pop()
        self._refresh_tile(resource.app_id)
        self._update_state()

    # -- commit --------------------------------------------------------------

    def _ask_to_forget(self, asking: bool) -> None:
        self.delete_button.set_visible(not asking)
        self.keep_button.set_visible(asking)
        self.forget_button.set_visible(asking)

    def _commit_cancel(self) -> None:
        self.on_cancel()

    def _commit(self) -> None:
        title = self.current_title()
        if not title:
            return
        # The arrangement being edited, before anything else reads it. Without
        # this every screen-mode edit was thrown away on Done: only the flat
        # `layout` was handed back, so arranging two screens and saving left
        # the context exactly as it was.
        self.ctx.set_arrangement(self.screen_count, self.arrangement)
        log.info(
            "saving context %s: %d windows, %d slots across %d screen(s)",
            title, len(self.entries), len(self.layout.slots), self.screen_count,
        )
        self.on_done(
            self.ctx,
            self._ordered_resources(),
            title,
            self.ephemeral_switch.get_active(),
            self.layout,
            self.isolated_switch.get_active(),
        )
