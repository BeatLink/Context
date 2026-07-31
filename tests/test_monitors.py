"""Which output things happen on.

The compositor still chooses where a context's workspace lands; what is here is
everything that has to *ask* — the layout preview needs the real shape, and the
launcher needs to dock somewhere predictable.
"""

from __future__ import annotations

import pytest

from context import monitors
from context.backends.base import MonitorInfo


@pytest.fixture
def two_screens(backend):
    backend.outputs = [
        MonitorInfo(name="eDP-1", width=1920, height=1200, focused=True),
        MonitorInfo(name="HDMI-A-1", width=3440, height=1440, x=1920),
    ]
    return backend


def test_aspect_comes_from_the_monitor_not_a_constant():
    """A layout is fractions of what it opens on, so 16:9 is a guess."""
    assert MonitorInfo("a", 1920, 1080).aspect == pytest.approx(16 / 9)
    assert MonitorInfo("b", 1920, 1200).aspect == pytest.approx(1.6)
    assert MonitorInfo("c", 3440, 1440).aspect == pytest.approx(2.389, abs=1e-3)
    # Rotated: the compositor reports the logical size, so this needs no
    # special handling — it just has to not be assumed landscape.
    assert MonitorInfo("d", 1080, 1920).aspect == pytest.approx(0.5625)


def test_a_monitor_with_no_height_does_not_divide_by_zero():
    assert MonitorInfo("broken", 1920, 0).aspect == pytest.approx(16 / 9)


def test_the_preview_uses_the_focused_monitor(two_screens):
    assert monitors.preview_aspect(two_screens) == pytest.approx(1.6)


def test_the_preview_uses_the_configured_monitor(two_screens, monkeypatch, tmp_path):
    from context import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(monitor="HDMI-A-1")

    assert monitors.preview_aspect(two_screens) == pytest.approx(2.389, abs=1e-3)


def test_an_unplugged_monitor_falls_back_to_the_focused_one(
    two_screens, monkeypatch, tmp_path
):
    """A laptop configured for its dock spends most of its time away from it."""
    from context import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(monitor="DP-9")

    assert monitors.by_name("DP-9", two_screens) is None
    assert monitors.preferred(two_screens).name == "eDP-1"


def test_no_monitors_at_all_still_gives_a_usable_aspect(backend):
    """A null backend, or a compositor that will not say."""
    backend.outputs = []
    assert monitors.preferred(backend) is None
    assert monitors.preview_aspect(backend) == pytest.approx(monitors.FALLBACK_ASPECT)


def test_the_first_monitor_stands_in_when_none_is_focused(backend):
    backend.outputs = [
        MonitorInfo(name="DP-1", width=2560, height=1440),
        MonitorInfo(name="DP-2", width=1920, height=1080),
    ]
    assert monitors.focused(backend).name == "DP-1"


def test_names_lists_what_is_connected(two_screens):
    assert monitors.names(two_screens) == ["eDP-1", "HDMI-A-1"]


def test_the_setting_is_not_validated_against_what_is_plugged_in(monkeypatch, tmp_path):
    """Naming an absent monitor has to survive being saved.

    Validating it away would mean configuring a docking station only while
    docked, and losing the setting every time the cable came out.
    """
    from context.settings import Settings

    assert Settings(monitor="DP-9").validated().monitor == "DP-9"
    assert Settings(monitor="  DP-9  ").validated().monitor == "DP-9"
    assert Settings().validated().monitor == ""


# -- screen numbering --------------------------------------------------------


def test_screens_are_numbered_by_the_configured_order(two_screens, monkeypatch, tmp_path):
    """The whole of Context's screen identity is this one setting.

    A context only ever says "screen 2", so moving a cable is a change here
    rather than an edit to every context that mentioned the old monitor.
    """
    from context import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "_current", None)

    assert [m.name for m in monitors.ordered(two_screens)] == ["eDP-1", "HDMI-A-1"]

    settings.update(screen_order=["HDMI-A-1", "eDP-1"])
    assert [m.name for m in monitors.ordered(two_screens)] == ["HDMI-A-1", "eDP-1"]


def test_an_unplugged_monitor_does_not_leave_a_gap(two_screens, monkeypatch, tmp_path):
    """Unplugging the middle screen should promote the next, not lose one."""
    from context import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(screen_order=["DP-9", "HDMI-A-1", "eDP-1"])

    assert [m.name for m in monitors.ordered(two_screens)] == ["HDMI-A-1", "eDP-1"]


def test_monitors_the_order_does_not_mention_keep_their_place(
    two_screens, monkeypatch, tmp_path
):
    from context import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(screen_order=["HDMI-A-1"])

    assert [m.name for m in monitors.ordered(two_screens)] == ["HDMI-A-1", "eDP-1"]


def test_the_launcher_uses_the_configured_order(two_screens, monkeypatch, tmp_path):
    """Screen 1 must be the same monitor for the editor and the launcher."""
    from context import launcher, settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "_current", None)
    settings.update(screen_order=["HDMI-A-1", "eDP-1"])

    assert [m.name for m in launcher._outputs(two_screens)] == ["HDMI-A-1", "eDP-1"]


def test_screen_modes_are_bounded():
    """More than a handful and the editor stops being readable."""
    from context.settings import MAX_SCREENS, MIN_SCREENS, Settings

    assert Settings(max_screens=99).validated().max_screens == MAX_SCREENS
    assert Settings(max_screens=0).validated().max_screens == MIN_SCREENS


def test_the_screen_order_survives_a_round_trip(monkeypatch, tmp_path):
    """Naming a monitor that is unplugged has to persist, as with `monitor`."""
    from context.settings import Settings

    live = Settings(screen_order=["DP-9", "eDP-1"]).validated()
    assert live.screen_order == ["DP-9", "eDP-1"]
    assert Settings(**Settings._coerce(live.__dict__)).screen_order == ["DP-9", "eDP-1"]
