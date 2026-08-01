"""User settings.

Distinct from `uistate`, which records what the user last did. These are choices
they made deliberately, so they live in the config directory and are editable by
hand as well as from the settings page.

Environment variables still win, so a one-off `CONTEXT_SIDEBAR_WIDTH=520` for a
single run behaves the way it always did.
"""

from __future__ import annotations

import json
import os
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path

from .logging_setup import get_logger

log = get_logger("settings")

ENV_PATH = "CONTEXT_SETTINGS"

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

# Below this the sidebar cannot show a list, and above it stops being a rail.
MIN_SIDEBAR_WIDTH = 200
MAX_SIDEBAR_WIDTH = 1200
# An icon button will not render below this in the Adwaita stylesheet, and a
# `min-width: 0` from an application provider does not reach it — the same
# behaviour that made a spin button's parts mismatch. Offering less would be
# the setting lying: the rail came out at 44 when asked for 32.
MIN_RAIL_WIDTH = 36
MAX_RAIL_WIDTH = 160

# How many screen modes a context can hold a layout for. More than this and the
# editor stops being readable, and nobody arranges windows across that many.
MIN_SCREENS = 1
MAX_SCREENS = 4


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "context"


def settings_path() -> Path:
    override = os.environ.get(ENV_PATH)
    return Path(override) if override else config_dir() / "settings.json"


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
    # How often the open list is re-checked against the compositor.
    poll_seconds: int = 2
    log_level: str = "info"
    backend: str = "auto"

    @classmethod
    def load(cls) -> "Settings":
        path = settings_path()
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError) as exc:
            # Broken settings must not stop the launcher starting.
            log.warning("ignoring %s: %s", path, exc)
            raw = {}
        if not isinstance(raw, dict):
            log.warning("ignoring %s: expected an object", path)
            raw = {}
        return cls(**cls._coerce(raw))

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
        path = settings_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {f.name: getattr(self, f.name) for f in fields(self)}, indent=2
                )
            )
            temporary.replace(path)
        except OSError as exc:
            log.warning("could not write %s: %s", path, exc)


def _clamp(value, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


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
    """Apply changes, persist them, and make them the live settings."""
    global _current
    _current = current().replace(**changes)
    _current.save()
    log.info("settings updated: %s", ", ".join(sorted(changes)))
    return _current
