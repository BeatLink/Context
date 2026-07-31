"""The settings page.

Everything in `settings.Settings`, grouped the way it is encountered rather than
the order it happens to be stored in. Changes apply as they are made — there is
no save button — and the ones the running window can honour immediately do so.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from . import settings, theme
from .logging_setup import get_logger

log = get_logger("settings_page")

EDGE_LABELS = ("Left", "Right", "Top", "Bottom")
LEVEL_LABELS = ("Debug", "Info", "Warning", "Error", "Critical")
BACKEND_LABELS = ("Detect automatically", "Hyprland", "None")
SCHEME_LABELS = ("Match the desktop", "Light", "Dark")
COLLAPSE_LABELS = ("A rail of icons", "Hidden entirely", "Never collapse")


def _stacked(title: str, subtitle: str, control: Gtk.Widget) -> Adw.PreferencesRow:
    """A row with its control under the description rather than beside it.

    `Adw.ActionRow` and its subclasses put the control in a suffix, which is
    cramped once the description is a sentence: the text wraps to three lines
    in a 360px sidebar while the control keeps a fixed share of the width.
    Those rows cannot be re-oriented, so the row is built from a plain
    `PreferencesRow` instead.

    The title is set on the row as well as drawn, so it still reaches
    accessibility tooling and anything that looks rows up by name.
    """
    row = Adw.PreferencesRow(title=title, activatable=False)
    row.add_css_class("ctx-setting")

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.set_margin_top(12)
    box.set_margin_bottom(12)
    box.set_margin_start(14)
    box.set_margin_end(14)

    heading = Gtk.Label(label=title, xalign=0.0, wrap=True)
    heading.add_css_class("ctx-setting-title")
    box.append(heading)

    if subtitle:
        description = Gtk.Label(label=subtitle, xalign=0.0, wrap=True)
        description.add_css_class("ctx-setting-subtitle")
        description.set_margin_bottom(2)
        box.append(description)

    box.append(control)
    row.set_child(box)
    return row


def _row_spin(title, subtitle, value, low, high, step, on_change) -> Adw.PreferencesRow:
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
    return _stacked(title, subtitle, drop)


def _row_switch(title, subtitle, active, on_change) -> Adw.PreferencesRow:
    switch = Gtk.Switch(active=active, halign=Gtk.Align.START)
    switch.connect("notify::active", lambda s, _p: on_change(s.get_active()))
    return _stacked(title, subtitle, switch)


class SettingsPage(Adw.NavigationPage):
    """A preferences page hosted in the launcher's navigation stack."""

    def __init__(self, window) -> None:
        super().__init__(title="Settings", tag="settings")
        self.window = window

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        # Nothing to minimise, maximise or close: this is a page inside the
        # docked launcher, and its own back button is the way out. Every other
        # header in Context suppresses them for the same reason.
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()
        page.add(self._appearance())
        page.add(self._behaviour())
        page.add(self._advanced())
        page.add(self._files())

        toolbar.set_content(page)
        self.set_child(toolbar)

    # -- groups --------------------------------------------------------------

    def _appearance(self) -> Adw.PreferencesGroup:
        live = settings.current()
        group = Adw.PreferencesGroup(
            title="Appearance",
            description="How the launcher looks and where it sits.",
        )
        group.add(
            _row_combo(
                "Colour scheme",
                "Light, dark, or whatever the desktop is set to.",
                SCHEME_LABELS,
                settings.COLOR_SCHEMES,
                live.color_scheme,
                lambda v: self._apply(color_scheme=v, restyle=True),
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

    def _behaviour(self) -> Adw.PreferencesGroup:
        """What the collapse button does, and what collapsing looks like.

        The launcher's own width is not here — it applies in every mode, so it
        belongs under Appearance. What is here is the width it shrinks *to*,
        which only means anything while collapsing does. A row is hidden when
        the current mode makes it meaningless, so the group only ever shows
        settings that currently do something.
        """
        live = settings.current()
        group = Adw.PreferencesGroup(
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

        self._sync_rows()
        return group

    def _sync_rows(self) -> None:
        """Hide the settings that the current mode makes meaningless."""
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

    def _advanced(self) -> Adw.PreferencesGroup:
        live = settings.current()
        group = Adw.PreferencesGroup(title="Advanced")
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
                "How much detail is written to the log.",
                LEVEL_LABELS,
                settings.LOG_LEVELS,
                live.log_level,
                lambda v: self._apply(log_level=v, restart=True),
            )
        )
        return group

    def _files(self) -> Adw.PreferencesGroup:
        """Where things live, so the hand-editable files can be found."""
        from .store import data_dir
        from .logging_setup import log_path
        from .uistate import state_path

        group = Adw.PreferencesGroup(
            title="Files",
            description="Everything here can also be edited by hand.",
        )
        for title, path in (
            ("Settings", settings.settings_path()),
            ("Theme", theme.theme_path()),
            ("Contexts", data_dir() / "contexts.json"),
            ("Interface state", state_path()),
            ("Log", log_path()),
        ):
            row = Adw.ActionRow(title=title, subtitle=str(path))
            row.add_css_class("property")
            group.add(row)

        write = Gtk.Button(label="Write the theme file", halign=Gtk.Align.START)
        write.connect("clicked", lambda _b: self._write_theme())
        group.add(
            _stacked(
                "Write the default theme",
                "Creates the theme file with the current colours, ready to edit.",
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
        restyle: bool = False,
        resync: bool = False,
        **changes,
    ) -> None:
        settings.update(**changes)
        if restyle:
            theme.apply_color_scheme()
        if resync:
            self._sync_rows()
        self.window.settings_changed(needs_restart=restart, changed=changes)

    def _restart(self) -> None:
        app = self.window.get_application()
        if app is not None:
            app.restart()

    def _write_theme(self) -> None:
        path = theme.current().write_default()
        log.info("wrote the default theme to %s", path)
        self.window.toasts.add_toast(Adw.Toast(title=f"Wrote {path}", timeout=4))
