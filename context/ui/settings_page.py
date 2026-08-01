"""The settings page.

Everything in `settings.Settings`, grouped the way it is encountered rather than
the order it happens to be stored in. Changes apply as they are made — there is
no save button — and the ones the running window can honour immediately do so.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from context.state import settings
from context.system import monitors
from context.ui import theme, widgets
from context.system.logging_setup import get_logger

log = get_logger("settings_page")

EDGE_LABELS = ("Left", "Right", "Top", "Bottom")
LEVEL_LABELS = ("Debug", "Info", "Warning", "Error", "Critical")
BACKEND_LABELS = ("Detect automatically", "Hyprland", "None")
COLLAPSE_LABELS = ("A rail of icons", "Hidden entirely", "Never collapse")
# The overview grid's orderings, by the key `settings.OVERVIEW_SORTS` holds.
SORT_LABELS = {
    "recent": "Recent",
    "name": "A–Z",
    "kind": "By kind",
    "contexts": "In contexts",
}

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
    """One of several, as buttons rather than a dropdown.

    A `Gtk.DropDown` opens a popover, and this page is a layer-shell overlay:
    the popup closes with the old value still selected, so a dropdown here does
    nothing at all. The editor hit this first and the note recording it said the
    settings page was exempt — which stopped being true the day settings moved
    out of the sidebar into a window of its own, and went unnoticed until every
    combo in the application was dead.
    """
    choice = widgets.SegmentedChoice(lambda index: on_change(values[index]))
    for label in labels:
        choice.add(label)
    if current_value in values:
        choice.set_selected(values.index(current_value), notify=False)

    # No scroller. The buttons are the control, so the row is at least as wide
    # as all of them — a scrollbar across a handful of buttons hides options
    # behind a gesture and reads as a mistake. The description takes whatever
    # is left and wraps, which is the thing that should give.
    row = _stacked(title, subtitle, choice)
    row.choice = choice
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

        # Tabs by what a setting is *about*, not by which view shows it.
        # Named after views, the scratchpad ended up cut across three of them —
        # which pads exist in one, how they are drawn in another, whether the
        # overview shows one in a third — so changing one feature meant visiting
        # three tabs. Where a setting genuinely belongs to one view it still
        # lives there: where the launcher docks is a fact about the launcher,
        # not a domain of its own.
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)

        for name, title, groups in (
            (
                "launcher",
                "Launcher",
                [self._appearance(), self._behaviour(), self._sidebar_contents()],
            ),
            ("overview", "Overview", [self._overview()]),
            ("scratchpad", "Scratchpad", [self._scratchpad()]),
            (
                "contexts",
                "Contexts",
                [self._contexts(), self._saving(), self._screens()],
            ),
            ("system", "System", [self._advanced(), self._layers(), self._files()]),
        ):
            page = widgets.Page(max_width=760)
            for group in groups:
                page.add(group)
            self.stack.add_titled(page, name, title)

        # A stack switcher rather than a dropdown: the settings screen is a
        # layer-shell overlay, and a popover on one throws the click away.
        switcher = Gtk.StackSwitcher(stack=self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        header.set_title_widget(switcher)

        toolbar.set_content(self.stack)
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
                "Overview row",
                "The overview, listed with the open contexts. It is a place "
                "of its own, so it is where the places are.",
                "show_overview_row",
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

        self._sync_sidebar_rows()
        return group

    def _overview(self) -> widgets.Group:
        """The overview screen: how it opens, every time it opens.

        It is summoned to do one thing and dismissed again, so what it looks
        like on opening is a decision made once here rather than a state it
        drifts into. The category filter is deliberately not among them —
        narrowing is done in the moment, and an overview that opened filtered
        would look like half the applications had gone missing.
        """
        live = settings.current()
        group = widgets.Group(
            title="Overview Window",
            description="Everything Context can open, on one screen. These are "
            "how it starts each time it is opened.",
        )
        group.add(
            _row_combo(
                "Sort applications by",
                "How the grid is ordered when it opens.",
                tuple(SORT_LABELS[k] for k in settings.OVERVIEW_SORTS),
                settings.OVERVIEW_SORTS,
                live.overview_sort,
                lambda v: self._apply(overview_sort=v),
            )
        )
        return group

    def _scratchpad(self) -> widgets.Group:
        """Every scratchpad setting, including where it is drawn.

        It was split across three tabs while they were named after views, since
        a feature shown in two of them belonged to neither. Whether the sidebar
        shows one and whether the overview does are two switches of the same
        thing, and they read as a pair here in a way they never could apart.
        """
        live = settings.current()
        group = widgets.Group(
            title="Scratchpad",
            description="Somewhere to type. One scratchpad that is always "
            "there, and one for each context, saved as you write.",
        )
        self.scratchpad_row = _row_switch(
            "Scratchpad",
            "Somewhere to type in the sidebar and the overview. Switching this "
            "off leaves what is written on disk.",
            live.scratchpad,
            lambda value: self._apply(scratchpad=value, resync=True),
        )
        group.add(self.scratchpad_row)

        self.scratchpad_rows = []
        for title, subtitle, field in (
            (
                "Global scratchpad",
                "One scratchpad that is there wherever you are.",
                "scratchpad_global",
            ),
            (
                "Context scratchpads",
                "One for each context, shown while you are in it. With both on, "
                "a switch above the scratchpad moves between them.",
                "scratchpad_per_context",
            ),
            (
                "Show both at once",
                "With a global scratchpad and a context one, show them stacked "
                "rather than one at a time behind a switch.",
                "scratchpad_show_both",
            ),
            (
                "Show in the sidebar",
                "The scratchpad in the launcher's column. It is the only place "
                "it appears; on home the sidebar stands open beside it.",
                "show_notes",
            ),
        ):
            row = _row_switch(
                title,
                subtitle,
                getattr(live, field),
                lambda value, name=field: self._apply(**{name: value}, resync=True),
            )
            group.add(row)
            self.scratchpad_rows.append(row)

        self.scratchpad_height_row = _row_spin(
            "Writing area height",
            "How tall the box is, in pixels. Per scratchpad, so showing both is "
            "twice this.",
            live.scratchpad_height,
            settings.MIN_SCRATCHPAD_HEIGHT,
            settings.MAX_SCRATCHPAD_HEIGHT,
            10,
            lambda v: self._apply(scratchpad_height=v, resync=True),
        )
        group.add(self.scratchpad_height_row)
        self.scratchpad_rows.append(self.scratchpad_height_row)
        self._sync_scratchpad_rows()
        return group

    def _sync_scratchpad_rows(self) -> None:
        # The three below are all about *which* notes are listed, which is not a
        # question at all while the scratchpad is off.
        live = settings.current()
        for row in getattr(self, "scratchpad_rows", []):
            row.set_sensitive(live.scratchpad)

    def _sync_sidebar_rows(self) -> None:
        # App results cannot appear without the search box that summons them;
        # a live switch here looked like a broken feature rather than a
        # dependency.
        live = settings.current()
        self.apps_switch_row.set_sensitive(live.show_search)

    def _sync_rows(self) -> None:
        """Hide the settings that the current mode makes meaningless."""
        if hasattr(self, "apps_switch_row"):
            self._sync_sidebar_rows()
        self._sync_scratchpad_rows()
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

    def _contexts(self) -> widgets.Group:
        live = settings.current()
        group = widgets.Group(
            title="Contexts",
            description="How contexts are listed wherever they are shown.",
        )
        group.add(
            _row_combo(
                "Order contexts by",
                "The sidebar, the overview and the rail all follow this, so "
                "they cannot disagree about the order.",
                ("Most recently used", "When they were made", "Name"),
                settings.CONTEXT_SORTS,
                live.context_sort,
                lambda v: self._apply(context_sort=v, resync=True),
            )
        )
        return group

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

    def _layers(self) -> widgets.Group:
        """The settings chain, and what each file in it decides.

        Worth showing rather than leaving implicit: with a setting declared by a
        NixOS or home-manager module, "I changed this and it went back" has a
        real explanation, and this is where it is.
        """
        chain = settings.layers()
        writable = chain[-1]
        where = settings.origins()
        declared = sorted(k for k, path in where.items() if path != writable)
        own = sorted(settings.overrides())

        group = widgets.Group(
            title="Where settings come from",
            description="Read in order, and the last file to mention a setting "
            "decides it. Context only ever writes the last one, and only the "
            "settings you have actually changed.",
        )
        for path in chain:
            keys = sorted(k for k, source in where.items() if source == path)
            row = widgets.ActionRow(title=str(path))
            row.add_css_class("property")
            if path == writable:
                row.set_subtitle(
                    f"Written by Context · {len(keys)} setting"
                    f"{'s' if len(keys) != 1 else ''} changed here"
                    if keys
                    else "Written by Context · nothing changed here yet"
                )
            elif not path.exists():
                row.set_subtitle("Not present")
            else:
                row.set_subtitle(
                    f"Declared · {len(keys)} setting{'s' if len(keys) != 1 else ''}"
                )
            group.add(row)

        self.reset_button = Gtk.Button(
            label="Reset to what is declared", halign=Gtk.Align.START
        )
        self.reset_button.add_css_class("destructive-action")
        self.reset_button.connect("clicked", lambda _b: self._reset())
        self.reset_button.set_sensitive(bool(own))
        group.add(
            _stacked(
                "Reset your changes",
                "Forget everything changed on this screen, so every setting "
                f"follows the files above again. {len(own)} changed here, "
                f"{len(declared)} declared elsewhere.",
                self.reset_button,
            )
        )
        return group

    def _reset(self) -> None:
        settings.reset()
        app = self.window.get_application()
        targets = getattr(app, "launchers", None) or [self.window]
        for window in targets:
            window.settings_changed(needs_restart=False, changed={})
        self.reset_button.set_sensitive(False)
        self._sync_rows()

    def _files(self) -> widgets.Group:
        """Where things live, so the hand-editable files can be found."""
        from context.state.store import data_dir
        from context.system.logging_setup import log_path
        from context.state.uistate import state_path

        group = widgets.Group(
            title="Files",
            description="Everything here can also be edited by hand.",
        )
        for title, path in (
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
