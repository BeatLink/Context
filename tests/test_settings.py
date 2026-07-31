"""User settings.

Settings are user-authored and hand-editable, so every value that reaches the
interface has to survive a file containing nonsense.
"""

from __future__ import annotations

import json

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


def test_all_displays_is_a_monitor_choice(isolated_settings):
    """A layer surface belongs to one output, so this means one window each."""
    from context import monitors, settings

    settings.update(monitor=settings.ALL_MONITORS)
    assert monitors.everywhere()

    settings.update(monitor="eDP-1")
    assert not monitors.everywhere()


def test_all_displays_asks_for_one_dock_per_screen(isolated_settings, backend):
    from context import monitors, settings
    from context.backends.base import MonitorInfo

    backend.outputs = [
        MonitorInfo(name="A", width=1920, height=1080),
        MonitorInfo(name="B", width=1920, height=1080, x=1920),
    ]
    settings.update(monitor=settings.ALL_MONITORS)
    assert [m.name for m in monitors.docks_on(backend)] == ["A", "B"]


def test_one_monitor_asks_for_one_dock(isolated_settings, backend):
    from context import monitors, settings
    from context.backends.base import MonitorInfo

    backend.outputs = [MonitorInfo(name="A", width=1920, height=1080)]
    settings.update(monitor="")
    assert len(monitors.docks_on(backend)) == 1


def test_all_displays_with_nothing_connected_still_gives_one_dock(
    isolated_settings, backend
):
    """Better one launcher the compositor places than none at all."""
    from context import monitors, settings

    backend.outputs = []
    settings.update(monitor=settings.ALL_MONITORS)
    assert monitors.docks_on(backend) == [None]


# -- when to offer to save ---------------------------------------------------


def test_the_save_prompt_defaults_to_closing():
    """The least intrusive of the three that ask."""
    assert Settings().save_prompt == "close"


def test_an_unknown_save_prompt_falls_back():
    assert Settings(save_prompt="sometimes").validated().save_prompt == "close"


def test_every_save_moment_is_accepted():
    for moment in settings.SAVE_PROMPTS:
        assert Settings(save_prompt=moment).validated().save_prompt == moment
