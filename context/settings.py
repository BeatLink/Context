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
from dataclasses import dataclass, fields
from pathlib import Path

from .logging_setup import get_logger

log = get_logger("settings")

ENV_PATH = "CONTEXT_SETTINGS"

EDGES = ("left", "right", "top", "bottom")
LOG_LEVELS = ("debug", "info", "warning", "error", "critical")
BACKENDS = ("auto", "hyprland", "none")
COLOR_SCHEMES = ("system", "light", "dark")
# What the collapse button does.
#
# "rail" and "none" both stay pinned to the edge: they always reserve space and
# there is always something on screen. Only "hidden" unpins, giving the space
# back and leaving a sliver to hover over.
#
# "none" removes collapsing altogether — no button, and the keybind says so.
COLLAPSE_MODES = ("rail", "hidden", "none")

# Below this the sidebar cannot show a list, and above it stops being a rail.
MIN_SIDEBAR_WIDTH = 200
MAX_SIDEBAR_WIDTH = 1200
MIN_RAIL_WIDTH = 32
MAX_RAIL_WIDTH = 160


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "context"


def settings_path() -> Path:
    override = os.environ.get(ENV_PATH)
    return Path(override) if override else config_dir() / "settings.json"


@dataclass(frozen=True)
class Settings:
    sidebar_edge: str = "left"
    sidebar_width: int = 380
    rail_width: int = 56
    # "system" follows the desktop's own light/dark preference.
    color_scheme: str = "system"
    collapse_mode: str = "rail"
    # Expand on hover and collapse again on leave, without changing the saved
    # collapsed state — the rail stays the resting shape.
    auto_expand: bool = False
    auto_expand_delay_ms: int = 120
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
            field = known.get(key)
            if field is None:
                continue
            try:
                if field.type is bool or isinstance(field.default, bool):
                    values[key] = bool(value)
                elif isinstance(field.default, int):
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
            sidebar_width=_clamp(
                self.sidebar_width, MIN_SIDEBAR_WIDTH, MAX_SIDEBAR_WIDTH, 380
            ),
            rail_width=_clamp(self.rail_width, MIN_RAIL_WIDTH, MAX_RAIL_WIDTH, 56),
            color_scheme=(
                self.color_scheme.strip().lower()
                if self.color_scheme.strip().lower() in COLOR_SCHEMES
                else "system"
            ),
            collapse_mode=(
                self.collapse_mode.strip().lower()
                if self.collapse_mode.strip().lower() in COLLAPSE_MODES
                else "rail"
            ),
            auto_expand=bool(self.auto_expand),
            auto_expand_delay_ms=_clamp(self.auto_expand_delay_ms, 0, 2000, 120),
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
