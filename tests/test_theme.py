"""Theming.

A theme is user-editable JSON, so the interesting cases are all the ways that
file can be wrong without the launcher failing to start.
"""

from __future__ import annotations

import json

import pytest

from context import theme
from context.theme import Theme


@pytest.fixture(autouse=True)
def isolated_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(theme, "_current", None)
    return tmp_path


def test_defaults_when_no_file_exists():
    assert Theme.load("dark").accent == Theme().accent


def test_a_partial_theme_keeps_the_other_defaults(isolated_theme):
    path = theme.theme_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"accent": "#ff0000"}))

    loaded = Theme.load("dark")
    assert loaded.accent == "#ff0000"
    assert loaded.surface == Theme().surface


def test_broken_json_falls_back_rather_than_crashing(isolated_theme):
    """A typo in a hand-edited theme must not stop the launcher starting."""
    path = theme.theme_path()
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")

    assert Theme.load("dark").accent == Theme().accent


def test_a_non_object_theme_is_ignored(isolated_theme):
    path = theme.theme_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(["red", "green"]))

    assert Theme.load("dark").accent == Theme().accent


def test_unknown_keys_are_ignored(isolated_theme):
    path = theme.theme_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"accent": "#123456", "nonsense": "#abcdef"}))

    assert Theme.load("dark").accent == "#123456"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("#ffffff", (1.0, 1.0, 1.0, 1.0)),
        ("#000000", (0.0, 0.0, 0.0, 1.0)),
        ("#fff", (1.0, 1.0, 1.0, 1.0)),
        ("#ff000080", (1.0, 0.0, 0.0, 0.502)),
    ],
)
def test_hex_parses_to_cairo_floats(value, expected):
    parsed = Theme(slot_fill=value).rgba("slot_fill")
    assert parsed == pytest.approx(expected, abs=0.01)


def test_a_malformed_colour_falls_back_to_the_default():
    """Cairo takes floats, so an unparseable colour would otherwise raise."""
    parsed = Theme(slot_fill="not-a-colour").rgba("slot_fill")
    assert len(parsed) == 4
    assert all(0.0 <= v <= 1.0 for v in parsed)


def test_css_carries_the_accent():
    css = Theme(accent="#abcdef").css()
    assert b"#abcdef" in css
    assert b"@define-color ctx_accent" in css


def test_write_default_round_trips(isolated_theme):
    """Every colour is written, so a file on disk pins the theme completely —
    a written-out dark theme stays dark even when the system asks for light."""
    path = Theme().write_default()
    assert path.exists()
    assert Theme.load("light") == Theme()


def test_current_is_cached_until_reloaded(isolated_theme):
    first = theme.current()
    assert theme.current() is first

    path = theme.theme_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"accent": "#010203"}))

    assert theme.current() is first  # still cached
    assert theme.reload().accent == "#010203"
