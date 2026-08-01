"""The settings page.

Everything in `settings.Settings`, grouped the way it is encountered rather than
the order it happens to be stored in. Changes apply as they are made — there is
no save button — and the ones the running window can honour immediately do so.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from . import monitors, settings, theme, widgets
from .logging_setup import get_logger

log = get_logger("settings_page")

EDGE_LABELS = ("Left", "Right", "Top", "Bottom")
LEVEL_LABELS = ("Debug", "Info", "Warning", "Error", "Critical")
BACKEND_LABELS = ("Detect automatically", "Hyprland", "None")
COLLAPSE_LABELS = ("A rail of icons", "Hidden entirely", "Never collapse")
SAVE_LABELS = (
    "Never",
    "Whenever it changes",
    "When switching away",
    "When closing it",
)


def _stacked(title: str, subtitle: str, control: Gtk.Widget) -> widgets.Row:
    """A row with its control beside the text, text taking the slack.

    The control used to sit *under* the description, because these rows lived
    in a 380px sidebar where a side-by-side layout wrapped every sentence to
    three lines. Settings are a full screen now, so the control goes back to
    the right — the arrangement every settings page uses — and the name stays
    from the stacked era because every row in this file calls it.

    The title is set on the row as well as drawn, so it still reaches
    accessibility tooling and anything that looks rows up by name.
    """
    row = widgets.Row(title=title)
    row.add_css_class("ctx-setting")

    box = Gtk.Box(spacing=18)
    box.set_margin_top(12)
    box.set_margin_bottom(12)
    box.set_margin_start(14)
    box.set_margin_end(14)

    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    text.set_hexpand(True)
    text.set_valign(Gtk.Align.CENTER)

    heading = Gtk.Label(label=title, xalign=0.0, wrap=True)
    heading.add_css_class("ctx-setting-title")
    text.append(heading)

    if subtitle:
        description = Gtk.Label(label=subtitle, xalign=0.0, wrap=True)
        description.add_css_class("ctx-setting-subtitle")
        text.append(description)
    box.append(text)

    # The control keeps its natural size at the end of the row rather than
    # stretching to fill it; the text is what absorbs the width.
    control.set_hexpand(False)
    control.set_halign(Gtk.Align.END)
    control.set_valign(Gtk.Align.CENTER)
    if not isinstance(control, Gtk.Switch):
        control.set_size_request(230, -1)
    box.append(control)

    row.set_child(box)
    # The description is redrawn when a setting changes what it says.
    row.set_subtitle = (
        description.set_label if subtitle else lambda _text: None
    )
    return row


def _row_spin(title, subtitle, value, low, high, step, on_change) -> widgets.Row:
    spin = Gtk.SpinButton(
        adjustment=Gtk.Adjustment(
            value=value, lower=low, upper=high, step_increment=step, page_increment=step
        ),
        numeric=True,
        hexpand=True,
    )
    spin.add_css_class("ctx-spin")
    spin.connect("value-changed", lambda s: on_change(int(s.get_value())))
    return _stacked(title, subtitle, spin)


def _row_combo(title, subtitle, labels, values, current_value, on_change):
    drop = Gtk.DropDown(model=Gtk.StringList.new(list(labels)), hexpand=True)
    drop.set_selected(values.index(current_value) if current_value in values else 0)
    drop.connect("notify::selected", lambda d, _p: on_change(values[d.get_selected()]))
    row = _stacked(title, subtitle, drop)
    row.dropdown = drop
    return row


def _monitor_description(found, name: str) -> str:
    """A monitor's make and model, so a connector name means something."""
    for monitor in found:
        if monitor.name == name:
            size = f"{monitor.width}\u00d7{monitor.height}"
            return f"{name} — {size}"
    return name


def _row_switch(title, subtitle, active, on_change) -> widgets.Row:
    switch = Gtk.Switch(active=active, halign=Gtk.Align.START)
    switch.connect("notify::active", lambda s, _p: on_change(s.get_active()))
    return _stacked(title, subtitle, switch)


class SettingsPage(widgets.NavigationPage):
    """A preferences page hosted in the launcher's navigation stack."""

    def __init__(self, window, on_back=None) -> None:
        super().__init__(title="Settings", tag="settings")
        self.window = window
        # Where "back" goes. As a page inside the sidebar that was popping the
        # navigation stack; as a full-screen view of its own it is closing the
        # window, and the page should not have to know which it is in.
        self.on_back = on_back or (lambda: window.nav.pop())

        toolbar = widgets.ToolbarView()
        header = widgets.HeaderBar(title="Settings")
        # Nothing to minimise, maximise or close: this is a page inside the
        # docked launcher, and its own back button is the way out. Every other
        # header in Context suppresses them for the same reason.
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        self.back_button = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_button.add_css_class("flat")
        self.back_button.set_tooltip_text("Back")
        self.back_button.connect("clicked", lambda _b: self.on_back())
        header.pack_start(self.back_button)
        toolbar.add_top_bar(header)

        page = widgets.Page(max_width=760)
        page.add(self._appearance())
        page.add(self._sidebar_contents())
        page.add(self._screens())
        page.add(self._behaviour())
        page.add(self._saving())
        page.add(self._advanced())
        page.add(self._files())

        toolbar.set_content(page)
        self.set_child(toolbar)

    # -- groups --------------------------------------------------------------

    def _appearance(self) -> widgets.Group:
        live = settings.current()
        group = widgets.Group(
            title="Appearance",
            description="How the launcher looks and where it sits.",
        )
        # Named rather than chosen from a list of connected outputs: a monitor
        # that is unplugged today is still the right choice for tomorrow, and a
        # dropdown of what happens to be attached cannot express that.
        found = monitors.names()
        group.add(
            _row_combo(
                "Monitor",
                "Which screen the launcher docks to. Applies on restart."
                + (f" Connected: {', '.join(found)}." if found else ""),
                ("Wherever the compositor puts it", "All displays", *found),
                ("", settings.ALL_MONITORS, *found),
                live.monitor,
                lambda v: self._apply(monitor=v, remonitor=True),
            )
        )
        group.add(
            _row_combo(
                "Edge",
                "Which side of the screen it docks to. Applies on restart.",
                EDGE_LABELS,
                settings.EDGES,
                live.sidebar_edge,
                lambda v: self._apply(sidebar_edge=v, restart=True),
            )
        )
        # The launcher's own width, which applies whether or not it collapses.
        # Collapsing only decides what it shrinks *to*, so that width belongs
        # with the collapse mode and this one does not.
        group.add(
            _row_spin(
                "Width",
                "Pixels the launcher reserves.",
                live.sidebar_width,
                settings.MIN_SIDEBAR_WIDTH,
                settings.MAX_SIDEBAR_WIDTH,
                10,
                lambda v: self._apply(sidebar_width=v),
            )
        )
        return group

    def _screens(self) -> widgets.Group:
        """Which physical monitor is screen 1, screen 2, and so on.

        The whole of Context's screen identity lives here. A context only ever
        says "screen 2", so moving a cable is one change on this page rather
        than an edit to every context that mentioned the old one.
        """
        live = settings.current()
        group = widgets.Group(
            title="Screens",
            description="Which monitor a context means by screen 1, screen 2, "
            "and so on.",
        )
        group.add(
            _row_spin(
                "Screen modes",
                "How many screen counts a context can hold a separate layout "
                "for, whatever is plugged in now.",
                live.max_screens,
                settings.MIN_SCREENS,
                settings.MAX_SCREENS,
                1,
                lambda v: self._apply(max_screens=v, resync=True),
            )
        )

        found = monitors.all_monitors()
        names = [m.name for m in found]
        if not names:
            return group

        # One row per connected monitor: which screen number it answers to.
        # Ordering by picking a monitor for each slot rather than dragging a
        # list, since the list is two or three items and a drag target for
        # that is more machinery than it is worth.
        current = [m.name for m in monitors.ordered()]
        self.screen_rows = []
        for position in range(len(names)):
            row = _row_combo(
                f"Screen {position + 1}",
                _monitor_description(found, current[position])
                if position < len(current)
                else "",
                names,
                tuple(names),
                current[position] if position < len(current) else names[0],
                lambda v, p=position: self._set_screen(p, v),
            )
            self.screen_rows.append(row)
            group.add(row)
        return group

    def _set_screen(self, position: int, name: str) -> None:
        """Put `name` at this screen number, swapping whatever was there.

        A swap rather than an insert: every screen number has to name exactly
        one monitor, and letting two rows pick the same one would leave a
        screen with nothing on it.
        """
        order = [m.name for m in monitors.ordered()]
        if position >= len(order) or order[position] == name:
            return
        if name in order:
            other = order.index(name)
            order[position], order[other] = order[other], order[position]
        else:
            order[position] = name
        settings.update(screen_order=order)
        self.window.settings_changed(needs_restart=True, changed={"screen_order": order})
        self._sync_screen_rows()

    def _sync_screen_rows(self) -> None:
        rows = getattr(self, "screen_rows", [])
        if not rows:
            return
        found = monitors.all_monitors()
        current = [m.name for m in monitors.ordered()]
        for position, row in enumerate(rows):
            if position < len(current):
                row.set_subtitle(_monitor_description(found, current[position]))

    def _behaviour(self) -> widgets.Group:
        """What the collapse button does, and what collapsing looks like.

        The launcher's own width is not here — it applies in every mode, so it
        belongs under Appearance. What is here is the width it shrinks *to*,
        which only means anything while collapsing does. A row is hidden when
        the current mode makes it meaningless, so the group only ever shows
        settings that currently do something.
        """
        live = settings.current()
        group = widgets.Group(
            title="Collapsing",
            description="What the collapse button does.",
        )
        group.add(
            _row_combo(
                "Collapse mode",
                "A rail keeps one icon per context. Hidden gives back all the "
                "space and leaves a sliver to hover over. Never collapse removes "
                "the button.",
                COLLAPSE_LABELS,
                settings.COLLAPSE_MODES,
                live.collapse_mode,
                lambda v: self._apply(collapse_mode=v, resync=True),
            )
        )

        self.rail_width_row = _row_spin(
            "Collapsed width",
            "Pixels reserved by the rail.",
            live.rail_width,
            settings.MIN_RAIL_WIDTH,
            settings.MAX_RAIL_WIDTH,
            4,
            lambda v: self._apply(rail_width=v),
        )
        group.add(self.rail_width_row)

        self.hover_row = _row_switch(
            "Expand on hover",
            "Open the full launcher while the pointer is over it, and collapse "
            "it again on leaving. Always on when hidden, since a sliver is not "
            "much to click.",
            live.auto_expand,
            lambda v: self._apply(auto_expand=v, resync=True),
        )
        group.add(self.hover_row)

        self.hover_delay_row = _row_spin(
            "Hover delay",
            "Milliseconds to wait before expanding, so passing over does not "
            "open it.",
            live.auto_expand_delay_ms,
            0,
            2000,
            20,
            lambda v: self._apply(auto_expand_delay_ms=v),
        )
        group.add(self.hover_delay_row)

        self.collapse_delay_row = _row_spin(
            "Collapse delay",
            "Milliseconds the sidebar stays open after the pointer leaves it, "
            "so passing out and back does not close it.",
            live.collapse_delay_ms,
            0,
            5000,
            50,
            lambda v: self._apply(collapse_delay_ms=v),
        )
        group.add(self.collapse_delay_row)

        self._sync_rows()
        return group

    def _sidebar_contents(self) -> widgets.Group:
        live = settings.current()
        group = widgets.Group(
            title="What the sidebar shows",
            description="Every part of it is useful and none is essential; at "
            "sidebar width each one costs the others room.",
        )
        for title, subtitle, field in (
            (
                "Search",
                "The box that filters the list, and searches applications.",
                "show_search",
            ),
            (
                "New context row",
                "The row that starts a context — from what is typed in the "
                "search box, or from the editor when nothing is.",
                "show_new_context",
            ),
            (
                "Overview button",
                "Everything you can open, on one screen. Shares the search "
                "box's row when both are shown.",
                "show_overview_button",
            ),
            (
                "Saved contexts",
                "The group beneath the open ones, in the list and on the rail. "
                "Open contexts are always listed.",
                "show_saved",
            ),
            (
                "Apps",
                "Matching applications under the search results, each one a new "
                "context. Only ever appears while searching, so it needs the "
                "search box.",
                "show_apps",
            ),
        ):
            row = _row_switch(
                title,
                subtitle,
                getattr(live, field),
                lambda value, name=field: self._apply(**{name: value}, resync=True),
            )
            group.add(row)
            if field == "show_apps":
                self.apps_switch_row = row

        self.apps_target_row = _row_combo(
            "Apps open in",
            "Where an app started from the search results lands. The current "
            "context takes it in; without one open, a new context is made "
            "either way.",
            ("A new context", "The current context"),
            settings.APP_TARGETS,
            live.search_apps_target,
            lambda v: self._apply(search_apps_target=v),
        )
        group.add(self.apps_target_row)
        self._sync_sidebar_rows()
        return group

    def _sync_sidebar_rows(self) -> None:
        # App results cannot appear without the search box that summons them;
        # a live switch here looked like a broken feature rather than a
        # dependency.
        live = settings.current()
        self.apps_switch_row.set_sensitive(live.show_search)
        self.apps_target_row.set_sensitive(live.show_search and live.show_apps)

    def _sync_rows(self) -> None:
        """Hide the settings that the current mode makes meaningless."""
        if hasattr(self, "apps_switch_row"):
            self._sync_sidebar_rows()
        live = settings.current()
        collapses = live.collapse_mode != "none"

        # Only a rail reserves a collapsed width. Hidden reserves a fixed
        # sliver, and never-collapse reserves nothing different.
        self.rail_width_row.set_visible(live.collapse_mode == "rail")
        self.hover_row.set_visible(collapses)
        # Hiding always reveals on hover whatever the switch says, so the delay
        # still applies there even with the switch off.
        self.hover_delay_row.set_visible(
            collapses and (live.auto_expand or live.collapse_mode == "hidden")
        )
        # Only hover-expansion retracts on its own, so only it has a delay.
        self.collapse_delay_row.set_visible(self.hover_delay_row.get_visible())

    def _saving(self) -> widgets.Group:
        live = settings.current()
        group = widgets.Group(
            title="Saving",
            description="A context drifts as you use it — windows get opened, "
            "moved and closed. This is when to offer to keep the changes.",
        )
        group.add(
            _row_combo(
                "Ask to save",
                "Whenever it changes asks the most, since a context changes "
                "often. The other two ask as you leave.",
                SAVE_LABELS,
                settings.SAVE_PROMPTS,
                live.save_prompt,
                lambda v: self._apply(save_prompt=v),
            )
        )
        group.add(
            _row_switch(
                "Notifications",
                "Report launches, closes and drift to the desktop's "
                "notification daemon. The save prompt is one of them, so with "
                "this off a drifting context is only saved by hand.",
                live.notifications,
                lambda v: self._apply(notifications=v),
            )
        )
        return group

    def _advanced(self) -> widgets.Group:
        live = settings.current()
        group = widgets.Group(title="Advanced")
        group.add(
            _row_combo(
                "Window manager",
                "Which backend drives workspaces. Applies on restart.",
                BACKEND_LABELS,
                settings.BACKENDS,
                live.backend,
                lambda v: self._apply(backend=v, restart=True),
            )
        )
        group.add(
            _row_spin(
                "Refresh interval",
                "Seconds between checks of which contexts are still running.",
                live.poll_seconds,
                1,
                60,
                1,
                lambda v: self._apply(poll_seconds=v),
            )
        )
        group.add(
            _row_combo(
                "Log level",
"How much detail is written to the log. Applies on restart.",
                LEVEL_LABELS,
                settings.LOG_LEVELS,
                live.log_level,
                lambda v: self._apply(log_level=v, restart=True),
            )
        )
        return group

    def _files(self) -> widgets.Group:
        """Where things live, so the hand-editable files can be found."""
        from .store import data_dir
        from .logging_setup import log_path
        from .uistate import state_path

        group = widgets.Group(
            title="Files",
            description="Everything here can also be edited by hand.",
        )
        for title, path in (
            ("Settings", settings.settings_path()),
            ("Style", theme.style_path()),
            ("Contexts", data_dir() / "contexts.json"),
            ("Interface state", state_path()),
            ("Log", log_path()),
        ):
            row = widgets.Row(title=title)
            row.add_css_class("property")
            group.add(row)

        write = Gtk.Button(label="Write the style file", halign=Gtk.Align.START)
        write.connect("clicked", lambda _b: self._write_theme())
        group.add(
            _stacked(
                "Write the style file",
                "Creates style.css with every colour spelled out, ready to edit. "
                "Saved changes apply immediately.",
                write,
            )
        )

        restart = Gtk.Button(label="Restart now", halign=Gtk.Align.START)
        restart.add_css_class("destructive-action")
        restart.connect("clicked", lambda _b: self._restart())
        group.add(
            _stacked(
                "Restart Context",
                "Applies the settings that are only read at startup. Contexts "
                "that are open stay open.",
                restart,
            )
        )
        return group

    # -- applying ------------------------------------------------------------

    def _apply(
        self,
        restart: bool = False,
        resync: bool = False,
        remonitor: bool = False,
        **changes,
    ) -> None:
        settings.update(**changes)
        if resync:
            self._sync_rows()
        if remonitor:
            # Which screens the launcher docks to changed. Same operation as a
            # cable being plugged in, so it needs no restart.
            app = self.window.get_application()
            rebuild = getattr(app, "rebuild_launchers", None)
            if rebuild is not None:
                rebuild()
                return
        # Every launcher, not just the one showing this page: a width or a
        # collapse mode applies to all of them.
        app = self.window.get_application()
        targets = getattr(app, "launchers", None) or [self.window]
        for window in targets:
            window.settings_changed(needs_restart=False, changed=changes)
        if restart:
            self.window.settings_changed(needs_restart=True, changed=changes)

    def _restart(self) -> None:
        app = self.window.get_application()
        if app is not None:
            app.restart()

    def _write_theme(self) -> None:
        path = theme.current().write_template()
        log.info("wrote the style file to %s", path)
        self.window.notify("theme", "Style file written", str(path))
