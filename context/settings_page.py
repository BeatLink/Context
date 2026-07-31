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
COLLAPSE_LABELS = ("A rail of icons", "Hidden entirely")


def _row_spin(title, subtitle, value, low, high, step, on_change) -> Adw.SpinRow:
    row = Adw.SpinRow(
        title=title,
        subtitle=subtitle,
        adjustment=Gtk.Adjustment(
            value=value, lower=low, upper=high, step_increment=step, page_increment=step
        ),
    )
    row.connect("notify::value", lambda r, _p: on_change(int(r.get_value())))
    return row


def _row_combo(title, subtitle, labels, values, current_value, on_change) -> Adw.ComboRow:
    row = Adw.ComboRow(
        title=title, subtitle=subtitle, model=Gtk.StringList.new(list(labels))
    )
    row.set_selected(values.index(current_value) if current_value in values else 0)
    row.connect(
        "notify::selected", lambda r, _p: on_change(values[r.get_selected()])
    )
    return row


def _row_switch(title, subtitle, active, on_change) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=active)
    switch.connect("notify::active", lambda s, _p: on_change(s.get_active()))
    row.add_suffix(switch)
    row.set_activatable_widget(switch)
    return row


class SettingsPage(Adw.NavigationPage):
    """A preferences page hosted in the launcher's navigation stack."""

    def __init__(self, window) -> None:
        super().__init__(title="Settings", tag="settings")
        self.window = window

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

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
            description="Where the launcher sits, and how much room it takes.",
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
        group.add(
            _row_spin(
                "Expanded width",
                "Pixels reserved when the launcher is open.",
                live.sidebar_width,
                settings.MIN_SIDEBAR_WIDTH,
                settings.MAX_SIDEBAR_WIDTH,
                10,
                lambda v: self._apply(sidebar_width=v),
            )
        )
        group.add(
            _row_spin(
                "Collapsed width",
                "Pixels reserved by the rail.",
                live.rail_width,
                settings.MIN_RAIL_WIDTH,
                settings.MAX_RAIL_WIDTH,
                4,
                lambda v: self._apply(rail_width=v),
            )
        )
        return group

    def _behaviour(self) -> Adw.PreferencesGroup:
        live = settings.current()
        group = Adw.PreferencesGroup(
            title="Collapsing",
            description="What the collapse button does.",
        )
        group.add(
            _row_combo(
                "Collapse to",
                "A rail keeps one icon per context. Hidden gives back all the "
                "space and leaves a sliver to hover over.",
                COLLAPSE_LABELS,
                settings.COLLAPSE_MODES,
                live.collapse_mode,
                lambda v: self._apply(collapse_mode=v),
            )
        )
        group.add(
            _row_switch(
                "Expand on hover",
                "Open the full launcher while the pointer is over it, and "
                "collapse it again on leaving. Always on when hidden.",
                live.auto_expand,
                lambda v: self._apply(auto_expand=v),
            )
        )
        group.add(
            _row_spin(
                "Hover delay",
                "Milliseconds to wait before expanding, so passing over the rail "
                "does not open it.",
                live.auto_expand_delay_ms,
                0,
                2000,
                20,
                lambda v: self._apply(auto_expand_delay_ms=v),
            )
        )
        return group

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

        write_theme = Adw.ActionRow(
            title="Write the default theme",
            subtitle="Creates the theme file with the current colours, ready to edit.",
        )
        button = Gtk.Button(label="Write", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: self._write_theme())
        write_theme.add_suffix(button)
        group.add(write_theme)
        return group

    # -- applying ------------------------------------------------------------

    def _apply(self, restart: bool = False, restyle: bool = False, **changes) -> None:
        settings.update(**changes)
        if restyle:
            theme.apply_color_scheme()
        self.window.settings_changed(needs_restart=restart, changed=changes)

    def _write_theme(self) -> None:
        path = theme.current().write_default()
        log.info("wrote the default theme to %s", path)
        self.window.toasts.add_toast(Adw.Toast(title=f"Wrote {path}", timeout=4))
