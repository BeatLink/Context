"""Theming.

Context draws several things libadwaita has no styling for — the app grid's
tiles, the layout preview's windows — so those colours have to come from
somewhere. Rather than hard-coding them, a theme is a small set of named colours
that both the stylesheet and the Cairo drawing read from.

A theme is JSON at `$XDG_CONFIG_HOME/context/theme.json`, so it can be edited
without touching the code, and anything it does not set falls back to the
default. The accent is also published as `@ctx_accent` for use in CSS.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields, replace
from pathlib import Path

from .logging_setup import get_logger

log = get_logger("theme")

ENV_THEME = "CONTEXT_THEME"

# Colour schemes. `SYSTEM` follows the desktop's own preference; the other two
# are the point of this module — an application choosing for itself, which is
# what libadwaita would not permit.
LIGHT = "light"
DARK = "dark"
SYSTEM = "system"
SCHEMES = (SYSTEM, LIGHT, DARK)


def system_prefers_light() -> bool:
    """Whether the desktop asks for a light theme.

    The cross-desktop answer is the XDG settings portal's `color-scheme`: 1 is
    "prefer dark", 2 is "prefer light", 0 is no preference. Read over D-Bus
    rather than through a toolkit, so it does not depend on which one is drawing.
    """
    try:
        from gi.repository import Gio

        proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Settings",
            None,
        )
        value = proxy.call_sync(
            "ReadOne",
            _portal_key("org.freedesktop.appearance", "color-scheme"),
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        )
        return value.unpack()[0] == 2
    except Exception as exc:
        # No portal, no session bus, or a desktop that does not answer. Dark is
        # what Context has always drawn, so it is the safer default.
        log.debug("no system colour scheme (%s); assuming dark", exc)
        return False


def _portal_key(namespace: str, key: str):
    from gi.repository import GLib

    return GLib.Variant("(ss)", (namespace, key))


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "context"


def theme_path() -> Path:
    override = os.environ.get(ENV_THEME)
    return Path(override) if override else config_dir() / "theme.json"


def _rgba(value: str, fallback: tuple[float, float, float, float]):
    """Parse #rgb, #rrggbb or #rrggbbaa into floats for Cairo."""
    text = value.strip().lstrip("#")
    try:
        if len(text) == 3:
            parts = [int(c * 2, 16) for c in text]
        elif len(text) in (6, 8):
            parts = [int(text[i : i + 2], 16) for i in range(0, len(text), 2)]
        else:
            return fallback
    except ValueError:
        return fallback
    values = [p / 255 for p in parts]
    while len(values) < 4:
        values.append(1.0)
    return tuple(values[:4])


@dataclass(frozen=True)
class Theme:
    """Colours, as hex strings so the file stays hand-editable."""

    accent: str = "#5ac0c0"
    surface: str = "#1e1e1e"
    on_surface: str = "#ffffff"

    # Drawn under the surface: the card a group of settings sits on, the line
    # between rows, and the shade a control uses to lift off the background.
    card: str = "#ffffff0d"
    border: str = "#ffffff1a"
    control: str = "#ffffff14"
    control_hover: str = "#ffffff26"

    # Layout preview
    preview_background: str = "#00000047"
    slot_fill: str = "#5ac0c052"
    slot_fill_active: str = "#5ac0c080"
    slot_border: str = "#5ac0c0b3"

    # App grid
    tile_background: str = "#ffffff10"
    tile_hover: str = "#ffffff1f"
    tile_selected: str = "#5ac0c038"

    # The screen a dragged window would land on, and the window on its way out.
    drop_target: str = "#5ac0c024"
    leaving_border: str = "#5ac0c0"

    # Collapsed sidebar. Saved contexts recede, open ones are lit, and the one
    # you are in is ringed — three states that differ in more than shade.
    rail_background: str = "#ffffff08"
    rail_hover: str = "#ffffff24"
    rail_open: str = "#5ac0c033"
    rail_active: str = "#5ac0c059"
    rail_divider: str = "#ffffff26"

    @classmethod
    def light(cls) -> "Theme":
        """The same theme, drawn on a light surface.

        Not a filter over the dark one: the overlays that build the dark theme
        are white at low alpha, and inverting them to black at the same alpha
        gives washed-out greys with the wrong contrast. Each is chosen instead.

        This is what libadwaita would not allow. It follows the system
        preference and offers no way for an application to differ, which is
        reasonable for a GNOME application and wrong for a shell the user
        themes themselves — and it is why the light theme could not be made to
        work while Adw widgets were drawing the surfaces.
        """
        return cls(
            accent="#2a8f8f",
            surface="#f6f5f4",
            on_surface="#1b1b1b",
            card="#ffffff",
            border="#0000001f",
            control="#00000010",
            control_hover="#0000001c",
            preview_background="#00000012",
            slot_fill="#2a8f8f30",
            slot_fill_active="#2a8f8f5c",
            slot_border="#2a8f8fcc",
            tile_background="#00000010",
            tile_hover="#0000001c",
            tile_selected="#2a8f8f30",
            drop_target="#2a8f8f1f",
            leaving_border="#2a8f8f",
            rail_background="#0000000a",
            rail_hover="#00000018",
            rail_open="#2a8f8f2e",
            rail_active="#2a8f8f4d",
            rail_divider="#00000021",
        )

    @classmethod
    def for_scheme(cls, scheme: str) -> "Theme":
        """The built-in theme for `light`, `dark`, or whatever the system says.

        A theme.json on disk still wins: `load` layers it over whichever of
        these is the starting point, so someone who has set two colours by hand
        keeps them and gets the rest from the scheme they picked.
        """
        if scheme == LIGHT:
            return cls.light()
        if scheme == DARK:
            return cls()
        return cls.light() if system_prefers_light() else cls()

    @classmethod
    def load(cls, scheme: str | None = None) -> "Theme":
        """The theme to draw with: the chosen scheme, plus any hand edits.

        The scheme decides the starting palette and theme.json overrides
        individual colours on top, so setting the accent by hand does not also
        opt out of light mode.
        """
        if scheme is None:
            from . import settings

            scheme = settings.current().color_scheme

        base = cls.for_scheme(scheme)
        path = theme_path()
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            return base
        except (OSError, json.JSONDecodeError) as exc:
            # A broken theme must not stop the launcher starting.
            log.warning("ignoring %s: %s", path, exc)
            return base

        if not isinstance(raw, dict):
            log.warning("ignoring %s: expected an object", path)
            return base

        known = {f.name for f in fields(cls)}
        values = {k: str(v) for k, v in raw.items() if k in known and isinstance(v, str)}
        unknown = set(raw) - known
        if unknown:
            log.debug("theme keys ignored: %s", ", ".join(sorted(unknown)))
        return replace(base, **values)

    def write_default(self) -> Path:
        """Write the current values out, as a starting point to edit."""
        path = theme_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({f.name: getattr(self, f.name) for f in fields(self)}, indent=2)
        )
        return path

    def surface_is_dark(self) -> bool:
        """Whether the surface is dark enough to want light widgets on it.

        Measured rather than declared, so a hand-edited theme.json that sets a
        dark surface still gets dark-theme widgets underneath — the scheme name
        it started from is not what is actually being drawn.
        """
        r, g, b, _ = _rgba(self.surface, (0, 0, 0, 1))
        # Rec. 709 luma: green reads brighter than red, red brighter than blue.
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 0.5

    def rgba(self, name: str):
        """One colour as an (r, g, b, a) tuple, for Cairo."""
        default = getattr(Theme(), name, "#ffffff")
        return _rgba(getattr(self, name, default), _rgba(default, (1, 1, 1, 1)))

    def css(self) -> bytes:
        """The stylesheet for the parts libadwaita does not cover."""
        return f"""
@define-color ctx_accent {self.accent};
@define-color ctx_surface {self.surface};
@define-color ctx_on_surface {self.on_surface};

/* GTK's own named colours, redefined.

   Setting `gtk-theme-name` or GTK_THEME does not help: the stylesheet is
   already resolved by the time an application can ask, so `theme_bg_color`
   stayed at the desktop theme's dark value (Mint-Y-Aqua here) even when the
   portal reported "prefer light". Every widget Context does not name a colour
   for — scrollbars, popovers, tooltips, menus — reads these, so redefining
   them is what makes the light theme actually light rather than a light page
   with dark furniture on it. Measured, not assumed: overriding the names moves
   the computed colour, overriding the setting does not. */
@define-color theme_bg_color {self.surface};
@define-color theme_fg_color {self.on_surface};
@define-color theme_base_color {self.card};
@define-color theme_text_color {self.on_surface};
@define-color theme_selected_bg_color {self.accent};
@define-color theme_selected_fg_color {self.surface};
@define-color insensitive_bg_color {self.surface};
@define-color insensitive_fg_color alpha({self.on_surface}, 0.5);
@define-color borders {self.border};
@define-color window_bg_color {self.surface};
@define-color window_fg_color {self.on_surface};
@define-color view_bg_color {self.card};
@define-color view_fg_color {self.on_surface};
@define-color headerbar_bg_color {self.card};
@define-color headerbar_fg_color {self.on_surface};
@define-color popover_bg_color {self.card};
@define-color popover_fg_color {self.on_surface};
@define-color accent_bg_color {self.accent};
@define-color accent_color {self.accent};

/* Popovers are children of the root, not of the window, so a rule scoped to
   `.ctx-window` never reaches a dropdown's list. */
popover > contents,
popover > arrow {{
    background-color: {self.card};
    color: {self.on_surface};
}}

/* The style classes libadwaita used to define.

   Removing its widgets left these behind on widgets all over Context —
   `boxed-list` on every list, `dim-label` on every section heading — with
   nothing defining them any more. They did not fail loudly: the list simply
   fell back to the desktop theme's dark card, and the headings kept an opacity
   that made them invisible on a light surface. Defining them here is what
   finishes the removal. */
.boxed-list {{
    background-color: {self.card};
    border: 1px solid {self.border};
    border-radius: 12px;
}}
.boxed-list > row {{
    background: transparent;
    border-bottom: 1px solid {self.border};
}}
.boxed-list > row:last-child {{
    border-bottom: none;
}}

.dim-label {{
    opacity: 0.7;
}}

.heading {{
    font-weight: bold;
}}
.title-4 {{
    font-weight: bold;
    font-size: 1.1em;
}}

/* Flat buttons carry no plate until pointed at — used for the icon buttons
   that sit inside rows, where a full button would be noise. */
button.flat {{
    background: none;
    background-image: none;
    border-color: transparent;
    box-shadow: none;
}}
button.flat:hover {{
    background-color: {self.control_hover};
}}

button.suggested-action {{
    background-image: none;
    background-color: {self.accent};
    color: {self.surface};
    border-color: {self.accent};
}}
button.destructive-action {{
    background-image: none;
    background-color: #c01c28;
    color: #ffffff;
    border-color: #c01c28;
}}

/* The context you are in, marked the way a browser marks the current tab.
   Placed after the blanket foreground rule above so it is not overridden by
   it, and matched on descendants too since the row's labels are children. */
.accent,
.accent label,
window.ctx-window .accent,
window.ctx-window .accent label {{
    color: {self.accent};
}}

/* Context draws its own surfaces rather than inheriting a desktop theme's.
   This is the part libadwaita made impossible: it resolves light and dark from
   the system preference before an application gets a say, so a stylesheet like
   this one was always painting over widgets that had already chosen. */
window.ctx-window {{
    background-color: {self.surface};
    color: {self.on_surface};
}}

/* Text colour has to reach every descendant, not just direct children.
   `> *` left the section headings ("Open", "Saved") several levels down still
   wearing the desktop theme's light-on-dark foreground, which on a light
   surface is invisible rather than merely wrong. Backgrounds stay unset so
   cards and controls keep their own. */
window.ctx-window label,
window.ctx-window button,
window.ctx-window entry {{
    color: {self.on_surface};
}}

.ctx-header {{
    background-color: {self.card};
    border-bottom: 1px solid {self.border};
    padding: 6px 8px;
    min-height: 40px;
}}
.ctx-header-title {{
    font-weight: bold;
}}

.ctx-page {{
    background-color: {self.surface};
}}

.ctx-group-title {{
    font-weight: bold;
    font-size: 1.05em;
    margin-left: 4px;
}}
.ctx-group-description {{
    font-size: 0.9em;
    opacity: 0.65;
    margin-left: 4px;
    margin-bottom: 2px;
}}

/* One card per group, with the rows divided by a line rather than by
   separator widgets — a hidden row then leaves no gap behind it. */
.ctx-card {{
    background-color: {self.card};
    border: 1px solid {self.border};
    border-radius: 12px;
}}
.ctx-card > row {{
    border-bottom: 1px solid {self.border};
    background: transparent;
}}
.ctx-card > row:last-child {{
    border-bottom: none;
}}

/* Controls have to be styled explicitly now that no widget library is doing
   it. Kept to one shade and one hover, so a theme stays two colours to edit. */
/* Controls must be able to shrink with the sidebar. The default themes set a
   min-width on entries and dropdown buttons that is wider than a collapsed
   launcher, which pushes them off the right edge instead of narrowing them. */
.ctx-page entry,
.ctx-page spinbutton,
.ctx-page spinbutton text,
.ctx-page dropdown,
.ctx-page dropdown > button {{
    min-width: 0;
}}
.ctx-page dropdown > button > box > label {{
    min-width: 0;
}}

.ctx-page button,
.ctx-page entry,
.ctx-page spinbutton,
.ctx-page dropdown > button {{
    background-image: none;
    background-color: {self.control};
    color: {self.on_surface};
    border: 1px solid {self.border};
    border-radius: 8px;
}}
.ctx-page button:hover,
.ctx-page dropdown > button:hover {{
    background-color: {self.control_hover};
}}
.ctx-page entry:focus-within,
.ctx-page spinbutton:focus-within {{
    border-color: {self.accent};
}}
.ctx-page switch:checked {{
    background-color: {self.accent};
}}

.ctx-toast {{
    background-color: {self.card};
    color: {self.on_surface};
    border: 1px solid {self.border};
    border-radius: 12px;
    padding: 10px 16px;
}}

.ctx-status-title {{
    font-weight: bold;
    font-size: 1.2em;
}}
.ctx-status-description,
.ctx-status-icon {{
    opacity: 0.6;
}}

.ctx-tile {{
    background-color: {self.tile_background};
    border: 1px solid {self.tile_background};
    border-radius: 10px;
}}
.ctx-tile:hover {{
    background-color: {self.tile_hover};
}}
.ctx-tile.ctx-chosen {{
    background-color: {self.tile_selected};
    border-color: {self.accent};
}}

.ctx-rail-button {{
    background-color: {self.rail_background};
    border: 2px solid transparent;
    border-radius: 12px;
    padding: 6px;
    min-width: 0;
    min-height: 0;
}}
.ctx-rail-button:hover {{
    background-color: {self.rail_hover};
}}

/* Saved: dimmed and unfilled, so the running contexts read first. */
.ctx-rail-button.ctx-saved {{
    opacity: 0.55;
}}
.ctx-rail-button.ctx-saved:hover {{
    opacity: 1;
}}

/* Open: filled and at full strength. */
.ctx-rail-button.ctx-open {{
    background-color: {self.rail_open};
    opacity: 1;
}}

/* Current: filled harder and ringed in the accent. */
.ctx-rail-button.ctx-active {{
    background-color: {self.rail_active};
    border-color: {self.accent};
    opacity: 1;
}}

/* Nothing in the rail may set a floor above the configured width. */
.ctx-rail-toggle {{
    min-width: 0;
    min-height: 0;
    padding: 2px;
}}

.ctx-rail-divider {{
    background-color: {self.rail_divider};
    margin: 4px 10px;
}}

/* Settings rows stack their control under the description, so the text gets
   the full width and the control does too. */
.ctx-setting-title {{
    font-weight: bold;
}}
.ctx-setting-subtitle {{
    font-size: 0.9em;
    opacity: 0.65;
}}

/* A standalone GtkSpinButton already sizes its entry and its two buttons to
   match — measured at 34px each. Setting a min-height here does not: the rule
   reaches the entry but not the buttons, so it *creates* the mismatch it looks
   like it would fix. Leave the geometry to the theme and only give the control
   room to breathe. */
.ctx-spin {{
    margin-top: 2px;
}}
""".encode()


_current: Theme | None = None
_installed = False
_provider = None


def current() -> Theme:
    """The loaded theme, read once per run."""
    global _current
    if _current is None:
        _current = Theme.load()
    return _current


def reload() -> Theme:
    global _current
    _current = Theme.load()
    return _current


def install() -> bool:
    """Put the stylesheet on the display, once.

    Anything using a `ctx-` style class has to call this — the launcher styles
    its rail, the editor its tiles, and whichever appears first must not be the
    unstyled one.
    """
    global _installed, _provider
    if _installed:
        return True

    from gi.repository import Gdk, Gtk

    display = Gdk.Display.get_default()
    if display is None:
        return False

    _provider = Gtk.CssProvider()
    _provider.load_from_data(current().css())
    # USER, not APPLICATION: the theme's own rules are installed at THEME
    # priority but a desktop theme may also install at APPLICATION, and
    # Context's colours have to be the last word on its own window.
    Gtk.StyleContext.add_provider_for_display(
        display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
    )
    _installed = True
    return True


def reinstall() -> bool:
    """Reload the stylesheet in place, for when the scheme or theme changes.

    Re-reads the theme as well as re-rendering it: switching to light mode
    changes which palette `load` starts from, and reusing the cached one would
    redraw the old colours.
    """
    reload()
    if not _installed:
        return install()
    _provider.load_from_data(current().css())
    return True
