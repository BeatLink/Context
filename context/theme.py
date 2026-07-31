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
from dataclasses import dataclass, fields
from pathlib import Path

from .logging_setup import get_logger

log = get_logger("theme")

ENV_THEME = "CONTEXT_THEME"


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

    # Layout preview
    preview_background: str = "#00000047"
    slot_fill: str = "#5ac0c052"
    slot_fill_active: str = "#5ac0c080"
    slot_border: str = "#5ac0c0b3"

    # App grid
    tile_background: str = "#ffffff10"
    tile_hover: str = "#ffffff1f"
    tile_selected: str = "#5ac0c038"

    @classmethod
    def load(cls) -> "Theme":
        path = theme_path()
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError) as exc:
            # A broken theme must not stop the launcher starting.
            log.warning("ignoring %s: %s", path, exc)
            return cls()

        if not isinstance(raw, dict):
            log.warning("ignoring %s: expected an object", path)
            return cls()

        known = {f.name for f in fields(cls)}
        values = {k: str(v) for k, v in raw.items() if k in known and isinstance(v, str)}
        unknown = set(raw) - known
        if unknown:
            log.debug("theme keys ignored: %s", ", ".join(sorted(unknown)))
        return cls(**values)

    def write_default(self) -> Path:
        """Write the current values out, as a starting point to edit."""
        path = theme_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({f.name: getattr(self, f.name) for f in fields(self)}, indent=2)
        )
        return path

    def rgba(self, name: str):
        """One colour as an (r, g, b, a) tuple, for Cairo."""
        default = getattr(Theme(), name, "#ffffff")
        return _rgba(getattr(self, name, default), _rgba(default, (1, 1, 1, 1)))

    def css(self) -> bytes:
        """The stylesheet for the parts libadwaita does not cover."""
        return f"""
@define-color ctx_accent {self.accent};

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
""".encode()


_current: Theme | None = None


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
