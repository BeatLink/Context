"""User settings.

Distinct from `uistate`, which records what the user last did. These are choices
they made deliberately, so they live in the config directory and are editable by
hand as well as from the settings page.

Environment variables still win, so a one-off `CONTEXT_SIDEBAR_WIDTH=520` for a
single run behaves the way it always did.

Settings are read from a **chain of files rather than one**, merged key by key
in order, and the last file to mention a key decides it:

    /etc/xdg/context/settings.json          every XDG_CONFIG_DIRS entry, least
                                            important first
    ~/.config/context/settings.d/*.json     drop-ins, in name order
    ~/.config/context/settings.json         the file Context itself writes

That last file is the only one Context writes, and it records **only what was
actually changed** — not a snapshot of every setting. The distinction is the
whole reason the chain works: a full snapshot would name every key, so every
declared value below would be shadowed by a copy of its own default and no
declaration could ever take effect again.

So a NixOS or home-manager module owns a drop-in, the settings screen owns the
last file, and the two compose: what you change by hand wins, and everything you
have not touched keeps following the declaration. `reset()` drops an override
and lets the declared value come back.
"""

from __future__ import annotations

import json
import os
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path

from context.system.logging_setup import get_logger

log = get_logger("settings")

# Replaces the whole chain with one file. A single-run override, and what the
# tests use to get a settings file of their own.
ENV_PATH = "CONTEXT_SETTINGS"
# The whole chain, spelled out and lowest priority first, so a packaged Context
# can be told exactly where to look rather than inferring it.
ENV_LAYERS = "CONTEXT_SETTINGS_PATH"

# Drop-ins beside the writable file. Anything declaring settings owns a file
# here rather than the one Context writes, so the two never fight over it.
DROP_IN_DIR = "settings.d"

EDGES = ("left", "right", "top", "bottom")
LOG_LEVELS = ("debug", "info", "warning", "error", "critical")
BACKENDS = ("auto", "hyprland", "none")
# What the collapse button does.
#
# "rail" and "none" both stay pinned to the edge: they always reserve space and
# there is always something on screen. Only "hidden" unpins, giving the space
# back and leaving a sliver to hover over.
#
# "none" removes collapsing altogether — no button, and the keybind says so.
COLLAPSE_MODES = ("rail", "hidden", "none")

# A layer surface belongs to exactly one output — the protocol's `output` arg is
# a single wl_output, and NULL means "you choose", not "all of them". So showing
# the launcher everywhere is one window per screen rather than one that spans.
ALL_MONITORS = "*"

# When to offer to save a context that has drifted from what was saved.
#
# "change" is the eager one and the most intrusive: a context drifts constantly
# as windows are opened and moved, so it asks often. "switch" and "close" ask at
# the moments you were leaving anyway, which is where a prompt costs least.
SAVE_PROMPTS = ("never", "change", "switch", "close")

# How the context list is ordered, in the sidebar, the overview and the rail.
# "recent" is by when each was last opened, which is what the list has always
# done; the other two are stable orders that do not move under the pointer.
CONTEXT_SORTS = ("recent", "created", "name")

# How the overview's application grid is ordered. Mirrors the keys of
# `system.apps.SORTS`, spelled out rather than imported: that module reads
# desktop entries through Gio, and settings have to load without a display.
# `test_the_overview_sorts_match_the_grid` pins the two together.
OVERVIEW_SORTS = ("recent", "name", "kind", "contexts")

# Below this the sidebar cannot show a list, and above it stops being a rail.
MIN_SIDEBAR_WIDTH = 200
MAX_SIDEBAR_WIDTH = 1200
# An icon button will not render below this in the Adwaita stylesheet, and a
# `min-width: 0` from an application provider does not reach it — the same
# behaviour that made a spin button's parts mismatch. Offering less would be
# the setting lying: the rail came out at 44 when asked for 32.
MIN_RAIL_WIDTH = 36
MAX_RAIL_WIDTH = 160

# How tall the sidebar's writing area may be. Below the floor there is not room
# for a line and its scrollbar; above the ceiling it has eaten the sidebar,
# which is the list it is meant to sit beside.
MIN_SCRATCHPAD_HEIGHT = 60
MAX_SCRATCHPAD_HEIGHT = 600

# How many screen modes a context can hold a layout for. More than this and the
# editor stops being readable, and nobody arranges windows across that many.
MIN_SCREENS = 1
MAX_SCREENS = 4


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "context"


def system_dirs() -> list[Path]:
    """The system config directories, most important first, as XDG orders them."""
    raw = os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg"
    return [Path(part) for part in raw.split(os.pathsep) if part.strip()]


def layers() -> list[Path]:
    """Every settings file, in the order they are merged. The last one wins.

    Files that do not exist are still listed: the settings screen shows the
    chain, and "this is where a declaration would go" is worth seeing.
    """
    override = os.environ.get(ENV_PATH)
    if override:
        return [Path(override)]

    explicit = os.environ.get(ENV_LAYERS)
    if explicit:
        return [Path(part) for part in explicit.split(os.pathsep) if part.strip()]

    found: list[Path] = []
    # XDG_CONFIG_DIRS is most-important-first and this list is least-important
    # first, because merging takes the last mention of a key.
    for directory in reversed(system_dirs()):
        found.extend(_drop_ins(directory / "context"))
        found.append(directory / "context" / "settings.json")
    found.extend(_drop_ins(config_dir()))
    found.append(config_dir() / "settings.json")
    return found


def _drop_ins(base: Path) -> list[Path]:
    """The drop-ins in one directory, in name order.

    Every config directory has them, not just the home one: a NixOS module
    declares into /etc/xdg and needs a file of its own there as much as a
    home-manager module does in the home directory.
    """
    return sorted((base / DROP_IN_DIR).glob("*.json"))


def settings_path() -> Path:
    """The one file Context writes: the last link in the chain."""
    return layers()[-1]


def read_layer(path: Path) -> dict:
    """One settings file, or nothing if it is missing or unusable."""
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        # A broken file must not stop the launcher starting, and must not take
        # the rest of the chain down with it either.
        log.warning("ignoring %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("ignoring %s: expected an object", path)
        return {}
    return raw


def merged() -> dict:
    """Every layer, flattened. Later files overwrite earlier ones key by key."""
    values: dict = {}
    for path in layers():
        values.update(read_layer(path))
    return values


def origins() -> dict[str, Path]:
    """Which file last set each key.

    What the settings screen needs to say where a value came from, and what
    `reset` needs to know whether dropping an override changes anything.
    """
    where: dict[str, Path] = {}
    known = {f.name for f in fields(Settings)}
    for path in layers():
        for key in read_layer(path):
            if key in known:
                where[key] = path
    return where


@dataclass(frozen=True)
class Settings:
    sidebar_edge: str = "left"
    # Which output the launcher docks to, by compositor name (eDP-1, HDMI-A-1).
    # Empty means wherever the compositor would put it, which is the only
    # sensible answer on a single-monitor session.
    # "" is wherever the compositor puts it; ALL_MONITORS puts one launcher on
    # every screen. Anything else is a connector name.
    monitor: str = ""
    # Which physical monitor is screen 1, screen 2, and so on — by connector
    # name, in order. Empty means left to right, which is the right answer
    # until someone says otherwise.
    #
    # This is the whole of Context's screen identity: contexts themselves only
    # ever say "screen 1", so moving a cable or rearranging the desk is a
    # change here rather than in every context.
    screen_order: list = field(default_factory=list)
    # How many screen modes to offer a layout for, whatever is plugged in now.
    max_screens: int = 2
    # Whether, and when, to offer to save a context that has changed.
    save_prompt: str = "close"
    sidebar_width: int = 380
    rail_width: int = 56
    collapse_mode: str = "rail"
    # Expand on hover and collapse again on leave, without changing the saved
    # collapsed state — the rail stays the resting shape.
    auto_expand: bool = False
    auto_expand_delay_ms: int = 120
    # How long the sidebar stays open after the pointer leaves its zone. Going
    # back the way you came — around a menu, past the edge of the screen — is
    # common enough that retracting the instant the cursor is outside made the
    # sidebar feel like it was running away.
    collapse_delay_ms: int = 400
    # Whether Context reports itself to the desktop's notification daemon.
    notifications: bool = True
    # What the expanded sidebar shows. All of it is useful and none of it is
    # essential — a sidebar that is only the open contexts is a perfectly good
    # sidebar, and at 380px every section costs the others room.
    show_search: bool = True
    # The built-in "New context" row. Separate from the search box: the row
    # works on its own — blank, it opens the editor to be named — and the box
    # is useful without it.
    show_new_context: bool = True
    show_saved: bool = True
    # The quick-action buttons on a context's row. Each one is a shortcut to
    # something the right-click menu offers anyway, so switching one off takes
    # away the button and never the action — which is what makes these safe to
    # offer, and different from a switch that removes a capability.
    show_save_button: bool = True
    show_restore_button: bool = True
    show_add_app_button: bool = True
    show_close_button: bool = True
    show_apps: bool = True
    # How the contexts are ordered wherever they are listed.
    context_sort: str = "recent"
    # The overview's grid, as it should be every time it opens rather than as
    # it was left. It is reached to do one thing and dismissed, so what it
    # remembers between openings is a setting rather than a habit it picks up.
    overview_sort: str = "recent"
    # Notes, kept as an append-only history. The master switch; with it off the
    # notes are still on disk and nothing lists them.
    scratchpad: bool = True
    # Notes that stand outside any context, always listed wherever you are.
    scratchpad_global: bool = True
    # Notes owned by a context, listed only while you are in it. The two are
    # separate switches because they answer different questions — a scratchpad
    # for the desk and a scratchpad for the job are both reasonable, and so is
    # either one alone.
    scratchpad_per_context: bool = True
    # Show the global scratchpad and the context's at once, rather than one at
    # a time behind a switch. Off by default: two boxes in a 380px sidebar is
    # half the height each, and most of the time you want the one you are in.
    scratchpad_show_both: bool = False
    # How tall the writing area is, in pixels. Per scratchpad, so showing both
    # is twice this rather than this split in half — a box that shrank because
    # a second one appeared would be a setting that stopped meaning anything.
    scratchpad_height: int = 132
    # The Notes section in the sidebar's list. The overview shows notes
    # whenever the scratchpad is on; this is only about the narrow view, where
    # every section costs the others room.
    show_notes: bool = True
    # How often the open list is re-checked against the compositor.
    poll_seconds: int = 2
    log_level: str = "info"
    backend: str = "auto"

    @classmethod
    def load(cls) -> "Settings":
        return cls(**cls._coerce(merged()))

    @classmethod
    def _coerce(cls, raw: dict) -> dict:
        known = {f.name: f for f in fields(cls)}
        values: dict = {}
        for key, value in raw.items():
            spec = known.get(key)
            if spec is None:
                continue
            try:
                if spec.default_factory is not MISSING:
                    # Only lists so far, and only of strings.
                    values[key] = [str(v) for v in value] if isinstance(value, list) else []
                elif spec.type is bool or isinstance(spec.default, bool):
                    values[key] = bool(value)
                elif isinstance(spec.default, int):
                    values[key] = int(value)
                else:
                    values[key] = str(value)
            except (TypeError, ValueError):
                log.warning("ignoring %s=%r: wrong type", key, value)
        return values

    def replace(self, **changes) -> "Settings":
        current = {f.name: getattr(self, f.name) for f in fields(self)}
        current.update(self._coerce(changes))
        return Settings(**current).validated()

    def validated(self) -> "Settings":
        """Clamp to what the interface can actually render."""
        return Settings(
            sidebar_edge=(
                self.sidebar_edge if self.sidebar_edge in EDGES else "left"
            ),
            # Not validated against the connected outputs: a monitor that is
            # unplugged today is still the right choice for tomorrow.
            monitor=self.monitor.strip(),
            screen_order=[str(n).strip() for n in self.screen_order if str(n).strip()],
            max_screens=_clamp(self.max_screens, MIN_SCREENS, MAX_SCREENS, 2),
            save_prompt=(
                self.save_prompt.strip().lower()
                if self.save_prompt.strip().lower() in SAVE_PROMPTS
                else "close"
            ),
            sidebar_width=_clamp(
                self.sidebar_width, MIN_SIDEBAR_WIDTH, MAX_SIDEBAR_WIDTH, 380
            ),
            rail_width=_clamp(self.rail_width, MIN_RAIL_WIDTH, MAX_RAIL_WIDTH, 56),
            collapse_mode=(
                self.collapse_mode.strip().lower()
                if self.collapse_mode.strip().lower() in COLLAPSE_MODES
                else "rail"
            ),
            auto_expand=bool(self.auto_expand),
            auto_expand_delay_ms=_clamp(self.auto_expand_delay_ms, 0, 2000, 120),
            collapse_delay_ms=_clamp(self.collapse_delay_ms, 0, 5000, 400),
            notifications=bool(self.notifications),
            show_search=bool(self.show_search),
            show_new_context=bool(self.show_new_context),
            show_saved=bool(self.show_saved),
            show_save_button=bool(self.show_save_button),
            show_restore_button=bool(self.show_restore_button),
            show_add_app_button=bool(self.show_add_app_button),
            show_close_button=bool(self.show_close_button),
            show_apps=bool(self.show_apps),
            context_sort=(
                self.context_sort.strip().lower()
                if self.context_sort.strip().lower() in CONTEXT_SORTS
                else "recent"
            ),
            overview_sort=(
                self.overview_sort.strip().lower()
                if self.overview_sort.strip().lower() in OVERVIEW_SORTS
                else "recent"
            ),
            scratchpad=bool(self.scratchpad),
            scratchpad_global=bool(self.scratchpad_global),
            scratchpad_per_context=bool(self.scratchpad_per_context),
            scratchpad_show_both=bool(self.scratchpad_show_both),
            scratchpad_height=_clamp(
                self.scratchpad_height, MIN_SCRATCHPAD_HEIGHT, MAX_SCRATCHPAD_HEIGHT, 132
            ),
            show_notes=bool(self.show_notes),
            poll_seconds=_clamp(self.poll_seconds, 1, 60, 2),
            log_level=(
                self.log_level.strip().lower()
                if self.log_level.strip().lower() in LOG_LEVELS
                else "info"
            ),
            backend=(
                self.backend.strip().lower()
                if self.backend.strip().lower() in BACKENDS
                else "auto"
            ),
        )

    def save(self) -> None:
        """Write every setting into the writable layer.

        Deliberately not what `update` uses. This spells out the whole of the
        current configuration, which shadows every layer beneath it — useful for
        pinning a machine to what it is doing right now, and exactly what must
        not happen on an ordinary settings change.
        """
        write_overrides({f.name: getattr(self, f.name) for f in fields(self)})


def _clamp(value, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def overrides() -> dict:
    """What has been set in the writable layer, and nothing else."""
    return read_layer(settings_path())


def write_overrides(values: dict) -> None:
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, indent=2, sort_keys=True))
        temporary.replace(path)
    except OSError as exc:
        log.warning("could not write %s: %s", path, exc)


_current: Settings | None = None


def current() -> Settings:
    global _current
    if _current is None:
        _current = Settings.load().validated()
    return _current


def reload() -> Settings:
    global _current
    _current = Settings.load().validated()
    return _current


def update(**changes) -> Settings:
    """Record these changes in the writable layer and make them live.

    Only the keys named here are written. Writing the whole of `Settings` would
    put a value against every key, and a layer that mentions every key overrides
    every layer below it — one visit to the settings screen would detach the
    machine from its declaration for good.
    """
    global _current
    stored = overrides()
    stored.update(Settings._coerce(changes))
    write_overrides(stored)
    _current = Settings.load().validated()
    log.info("settings updated: %s", ", ".join(sorted(changes)))
    return _current


def reset(*names: str) -> Settings:
    """Drop overrides, letting whatever the layers below say come back.

    With no names, drops all of them: the machine goes back to being exactly
    what was declared for it.
    """
    global _current
    stored = overrides()
    if names:
        for name in names:
            stored.pop(name, None)
    else:
        stored = {}
    write_overrides(stored)
    _current = Settings.load().validated()
    log.info("settings reset: %s", ", ".join(sorted(names)) if names else "all")
    return _current
