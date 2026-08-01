"""Theming.

Context is themed the way waybar or swaync are: one user-editable CSS file
loaded over the built-in stylesheet. The interesting cases are all the ways
that file can be wrong without the launcher failing to start.
"""

from __future__ import annotations

import pytest

from context.ui import theme
from context.ui.theme import Theme

from tests.conftest import needs_display, run_app


@pytest.fixture(autouse=True)
def isolated_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv(theme.ENV_STYLE, raising=False)
    monkeypatch.setattr(theme, "_current", None)
    monkeypatch.setattr(theme, "_installed", False)
    monkeypatch.setattr(theme, "_provider", None)
    monkeypatch.setattr(theme, "_user_provider", None)
    monkeypatch.setattr(theme, "_monitor", None)
    return tmp_path


def write_style(text: str):
    path = theme.style_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_defaults_when_no_file_exists():
    assert Theme.load() == Theme()


def test_a_define_color_overrides_the_palette():
    write_style("@define-color ctx_accent #ff0000;\n")

    loaded = Theme.load()
    assert loaded.accent == "#ff0000"
    assert loaded.surface == Theme().surface


def test_unknown_names_are_ignored():
    write_style(
        "@define-color ctx_nonsense #123456;\n"
        "@define-color wallpaper_fg #ffffff;\n"
    )

    assert Theme.load() == Theme()


def test_plain_rules_are_left_to_gtk():
    """A file with only widget rules themes widgets, not the drawn palette."""
    write_style(".ctx-rail-button { border-radius: 0; }\n")

    assert Theme.load() == Theme()


def test_a_broken_file_falls_back_rather_than_crashing():
    """A typo in a hand-edited file must not stop the launcher starting."""
    write_style("@define-color ctx_accent\n{{{ not css at all")

    assert Theme.load() == Theme()


def test_rgb_functions_parse_for_cairo():
    """Colour-scheme generators emit rgb()/rgba() as often as hex."""
    write_style(
        "@define-color ctx_accent rgb(255, 0, 0);\n"
        "@define-color ctx_slot_fill rgba(0, 255, 0, 0.5);\n"
    )

    loaded = Theme.load()
    assert loaded.rgba("accent") == pytest.approx((1.0, 0.0, 0.0, 1.0))
    assert loaded.rgba("slot_fill") == pytest.approx((0.0, 1.0, 0.0, 0.5))


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


def test_the_stylesheet_defines_every_colour():
    """Each palette entry is published as a name style.css can redefine."""
    css = Theme().css().decode()
    from dataclasses import fields

    for field in fields(Theme):
        assert f"@define-color ctx_{field.name} " in css


def test_rules_reference_names_rather_than_values():
    """Redefining a name has to reach every rule that uses the colour, so the
    rules must not inline the value the way an f-string template would."""
    css = Theme().css().decode()
    assert "@ctx_accent" in css
    rules = "\n".join(
        line for line in css.splitlines() if not line.startswith("@define-color")
    )
    assert Theme().accent not in rules


def test_a_legacy_theme_json_is_flagged(caplog):
    """theme.json is no longer read; someone who still has one should learn
    that from the log rather than from their colours going stale."""
    legacy = theme.config_dir() / "theme.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("{}")

    with caplog.at_level("WARNING", logger="context.ui.theme"):
        Theme.load()
    assert any("theme.json" in message for message in caplog.messages)


def test_write_template_round_trips():
    """The written file pins every colour, so it survives default changes."""
    path = Theme().write_template()
    assert path.exists()
    assert Theme.load() == Theme()


def test_current_is_cached_until_reloaded():
    first = theme.current()
    assert theme.current() is first

    write_style("@define-color ctx_accent #010203;\n")

    assert theme.current() is first  # still cached
    assert theme.reload().accent == "#010203"


def test_the_style_path_can_be_pointed_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setenv(theme.ENV_STYLE, str(tmp_path / "elsewhere.css"))
    assert theme.style_path() == tmp_path / "elsewhere.css"


# -- the loaded stylesheet ----------------------------------------------------


@needs_display
def test_a_user_redefinition_reaches_the_widgets(gtk_app):
    """The mechanism the whole design rests on: a `@define-color` in style.css
    must override the built-in definition for every rule that references the
    name, across providers. Measured on a real widget rather than assumed."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    write_style("@define-color ctx_accent #ff0000;\n")
    seen = {}

    def body(app):
        assert theme.install()
        window = Gtk.ApplicationWindow(application=app)
        window.add_css_class("ctx-window")
        label = Gtk.Label(label="here now")
        label.add_css_class("accent")
        window.set_child(label)
        seen["color"] = label.get_color()
        app.quit()

    run_app(gtk_app, body)
    color = seen["color"]
    assert (round(color.red, 2), round(color.green, 2), round(color.blue, 2)) == (
        1.0,
        0.0,
        0.0,
    )


@needs_display
def test_reinstall_picks_up_an_edit(gtk_app):
    """What the file watcher calls when style.css changes on disk.

    Styles revalidate on the next frame rather than at the reload call, so the
    test presents the window and waits for the recolour instead of reading the
    style back synchronously — which reads the old colour and proves nothing.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    seen = {}

    def body(app):
        assert theme.install()
        window = Gtk.ApplicationWindow(application=app)
        window.add_css_class("ctx-window")
        label = Gtk.Label(label="here now")
        label.add_css_class("accent")
        window.set_child(label)
        window.present()
        seen["before"] = label.get_color().to_string()

        write_style("@define-color ctx_accent #00ff00;\n")
        theme.reinstall()

        context = GLib.MainContext.default()
        deadline = GLib.get_monotonic_time() + 2_000_000
        while (
            label.get_color().to_string() == seen["before"]
            and GLib.get_monotonic_time() < deadline
        ):
            context.iteration(False)
        seen["after"] = label.get_color().to_string()
        window.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["before"] != seen["after"]
    assert seen["after"] == "rgb(0,255,0)"


def test_a_translucent_surface_reaches_the_widgets(monkeypatch, tmp_path):
    """Transparency is the surface colour's alpha: there is nowhere else to put
    it, since GTK's `alpha()` takes a literal rather than a named colour."""
    style = tmp_path / "style.css"
    style.write_text("@define-color ctx_surface rgba(30, 30, 30, 0.75);\n")
    monkeypatch.setenv(theme.ENV_STYLE, str(style))
    monkeypatch.setattr(theme, "_current", None)

    live = theme.current()
    assert live.rgba("surface") == pytest.approx((30 / 255, 30 / 255, 30 / 255, 0.75))
    # And the stylesheet hands the same value to the widgets.
    assert b"rgba(30, 30, 30, 0.75)" in live.css()


def test_a_full_screen_view_is_opaque(monkeypatch, tmp_path):
    """Transparency is for a strip at the edge. Spread over the whole output it
    is a haze between the user and what they are reading."""
    style = tmp_path / "style.css"
    style.write_text("@define-color ctx_surface rgba(20, 30, 40, 0.5);\n")
    monkeypatch.setenv(theme.ENV_STYLE, str(style))
    monkeypatch.setattr(theme, "_current", None)

    css = theme.current().css().decode()
    # Derived from whatever the surface is, so one translucent colour gives both.
    assert "@define-color ctx_surface_solid rgb(20, 30, 40);" in css
    assert ".ctx-surface.ctx-solid" in css
