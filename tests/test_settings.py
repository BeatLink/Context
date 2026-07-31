"""User settings.

Settings are user-authored and hand-editable, so every value that reaches the
interface has to survive a file containing nonsense.
"""

from __future__ import annotations

import json
import os

import pytest

from context import settings
from context.settings import Settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "_current", None)
    for name in ("CONTEXT_SIDEBAR_EDGE", "CONTEXT_SIDEBAR_WIDTH", "CONTEXT_RAIL_WIDTH"):
        monkeypatch.delenv(name, raising=False)
    yield tmp_path


def test_defaults_are_usable():
    live = Settings()
    assert live.sidebar_width > live.rail_width
    assert live.auto_expand is False
    assert live.backend == "auto"


def test_widths_are_clamped_to_what_can_be_rendered():
    """A width below the minimum leaves a sidebar too narrow to show a list."""
    tiny = Settings(sidebar_width=1, rail_width=1).validated()
    assert tiny.sidebar_width == settings.MIN_SIDEBAR_WIDTH
    assert tiny.rail_width == settings.MIN_RAIL_WIDTH

    huge = Settings(sidebar_width=99999, rail_width=99999).validated()
    assert huge.sidebar_width == settings.MAX_SIDEBAR_WIDTH
    assert huge.rail_width == settings.MAX_RAIL_WIDTH


def test_nonsense_values_fall_back():
    live = Settings(
        sidebar_edge="diagonal", log_level="shouty", backend="cinnamon"
    ).validated()
    assert live.sidebar_edge == "left"
    assert live.log_level == "info"
    assert live.backend == "auto"


def test_a_broken_file_is_ignored(isolated_settings):
    settings.settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings.settings_path().write_text("{not json")
    assert Settings.load().sidebar_width == Settings().sidebar_width


def test_a_non_object_file_is_ignored(isolated_settings):
    settings.settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings.settings_path().write_text("[1, 2, 3]")
    assert Settings.load().sidebar_width == Settings().sidebar_width


def test_unknown_keys_are_dropped(isolated_settings):
    settings.settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings.settings_path().write_text(json.dumps({"sidebar_width": 500, "nope": 1}))
    assert Settings.load().sidebar_width == 500


def test_update_persists_and_becomes_live(isolated_settings):
    settings.update(sidebar_width=500, rail_width=72)
    assert settings.current().sidebar_width == 500
    stored = json.loads(settings.settings_path().read_text())
    assert stored["sidebar_width"] == 500
    assert stored["rail_width"] == 72


def test_update_leaves_other_values_alone(isolated_settings):
    settings.update(sidebar_width=500)
    settings.update(rail_width=72)
    assert settings.current().sidebar_width == 500
    assert settings.current().rail_width == 72


def test_the_sidebar_reads_both_widths_from_settings(isolated_settings):
    from context import sidebar

    settings.update(sidebar_width=500, rail_width=72)
    assert sidebar.configured_width() == 500
    assert sidebar.rail_width() == 72


def test_the_environment_still_wins(isolated_settings, monkeypatch):
    """A one-off override for a single run has to keep working."""
    from context import sidebar

    settings.update(sidebar_width=500, rail_width=72)
    monkeypatch.setenv("CONTEXT_SIDEBAR_WIDTH", "640")
    monkeypatch.setenv("CONTEXT_RAIL_WIDTH", "40")
    assert sidebar.configured_width() == 640
    assert sidebar.rail_width() == 40


def test_the_colour_scheme_defaults_to_the_desktop():
    assert Settings().color_scheme == "system"


def test_an_unknown_colour_scheme_falls_back():
    assert Settings(color_scheme="sepia").validated().color_scheme == "system"


def test_light_mode_recolours_what_depends_on_the_background(isolated_settings):
    """Two ways a dark palette disappears on a light one, both fixed here.

    The neutrals are translucent white, which does nothing over white. The
    accent-derived fills are a pale aqua at low alpha, which washes out — so
    the accent is deepened for light mode rather than kept.
    """
    from context import theme

    base = theme.Theme()
    light = theme.for_scheme(base, dark=False)
    assert light.rail_background != base.rail_background
    assert light.tile_background != base.tile_background
    assert light.accent != base.accent
    assert light.slot_fill != base.slot_fill


def test_the_drawn_parts_follow_the_scheme(isolated_settings, monkeypatch):
    """The stylesheet followed the scheme; the layout preview did not.

    Anything painting with Cairo has to ask for the adjusted palette, or it
    draws dark colours onto a light interface.
    """
    from context import settings, theme

    settings.update(color_scheme="light")
    assert theme.active().accent == theme.LIGHT_OVERRIDES["accent"]

    settings.update(color_scheme="dark")
    assert theme.active().accent == theme.Theme().accent


def test_dark_mode_leaves_the_theme_alone(isolated_settings):
    from context import theme

    base = theme.Theme()
    assert theme.for_scheme(base, dark=True) is base


def test_a_hand_written_colour_is_not_recoloured(isolated_settings, monkeypatch):
    """An explicit theme.json is a deliberate choice, in either scheme."""
    from context import theme

    path = theme.theme_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rail_background": "#ff00ff"}))

    light = theme.for_scheme(theme.Theme.load(), dark=False)
    assert light.rail_background == "#ff00ff"
    # Untouched keys still get the light treatment.
    assert light.tile_background == theme.LIGHT_OVERRIDES["tile_background"]


def test_the_collapse_mode_defaults_to_a_rail():
    assert Settings().collapse_mode == "rail"


def test_an_unknown_collapse_mode_falls_back():
    assert Settings(collapse_mode="vanish").validated().collapse_mode == "rail"


def test_never_collapse_is_a_mode():
    assert "none" in settings.COLLAPSE_MODES
    assert Settings(collapse_mode="none").validated().collapse_mode == "none"


def test_the_spin_control_is_left_to_the_theme(isolated_settings):
    """Setting a min-height creates the mismatch it looks like it fixes.

    A standalone GtkSpinButton already sizes its entry and its two buttons
    alike. The rule reaches the entry but not the buttons, so the entry grows
    and the buttons do not.
    """
    from context import theme

    css = theme.Theme().css().decode()
    block = css[css.index(".ctx-spin"):]
    assert "min-height" not in block.split("}")[0]


def test_a_desktop_theme_is_kept_out_of_context(monkeypatch, isolated_settings):
    """A themed desktop otherwise dictates the colours, whatever the scheme.

    home-manager's `gtk.theme` writes ~/.config/gtk-4.0/gtk.css importing a
    theme, which loads at USER priority — above libadwaita's stylesheet and
    above anything an application installs. Sass-built themes inline their
    colours as literals too, so redefining named colours does not reach them
    either. GTK_THEME skips theme loading for this process, which does.
    """
    from context import settings, theme

    monkeypatch.delenv("GTK_THEME", raising=False)
    settings.update(color_scheme="light")
    assert theme.pin_gtk_theme() == "Adwaita:light"
    assert os.environ["GTK_THEME"] == "Adwaita:light"

    monkeypatch.delenv("GTK_THEME", raising=False)
    settings.update(color_scheme="dark")
    assert theme.pin_gtk_theme() == "Adwaita:dark"


def test_matching_the_desktop_leaves_its_theme_in_place(monkeypatch, isolated_settings):
    """"System" means the desktop decides, theme included."""
    from context import settings, theme

    monkeypatch.delenv("GTK_THEME", raising=False)
    settings.update(color_scheme="system")
    assert theme.pin_gtk_theme() is None
    assert "GTK_THEME" not in os.environ


def test_an_explicit_gtk_theme_is_not_overridden(monkeypatch, isolated_settings):
    """Someone setting it by hand is choosing deliberately."""
    from context import settings, theme

    monkeypatch.setenv("GTK_THEME", "Yaru:dark")
    settings.update(color_scheme="light")
    assert theme.pin_gtk_theme() is None
    assert os.environ["GTK_THEME"] == "Yaru:dark"
