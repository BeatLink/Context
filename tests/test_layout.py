"""Layouts, and the split directions they translate into."""

from __future__ import annotations

import pytest

from context.state.layout import (
    PRESETS,
    Layout,
    Slot,
    preset_for,
    snap,
    split_directions,
)


def test_slot_clamps_out_of_range_values():
    slot = Slot.from_dict({"x": -1, "y": 5, "width": 0.5, "height": 0.5})
    assert 0.0 <= slot.x <= 1.0
    assert 0.0 <= slot.y <= 1.0


def test_layout_round_trip():
    layout = Layout(slots=[Slot(0.0, 0.0, 0.5, 1.0), Slot(0.5, 0.0, 0.5, 1.0)])
    assert len(Layout.from_list(layout.to_list()).slots) == 2


def test_layout_from_garbage_is_empty():
    assert Layout.from_list("not a list").slots == []
    assert Layout.from_list([1, "two", None]).slots == []


@pytest.mark.parametrize(
    "name,expected",
    [
        ("maximised", []),
        ("side-by-side", ["r"]),
        ("stacked", ["d"]),
        ("three-columns", ["r", "r"]),
        ("main-and-stack", ["r", "d"]),
        ("grid", ["r", "d", "r"]),
    ],
)
def test_preset_split_directions(name, expected):
    """Every preset has to survive the trip through direction inference.

    A layout is stored as rectangles but applied as splits, so this is where a
    preset would silently come out wrong.
    """
    assert split_directions(list(PRESETS[name])) == expected


def test_split_directions_ignores_the_first_slot():
    """The first window has nothing to split against."""
    assert split_directions([Slot()]) == []
    assert split_directions([]) == []


def test_preset_for_covers_any_count():
    for count in range(1, 10):
        layout = preset_for(count)
        assert len(layout.slots) == count, f"{count} windows"


def test_preset_for_zero_is_empty():
    assert preset_for(0).slots == []


def test_resize_keeps_a_slot_per_window():
    layout = preset_for(2)
    assert len(layout.resized(4).slots) == 4
    assert len(layout.resized(1).slots) == 1


def test_slot_for_out_of_range_is_full_screen():
    """A resource with no slot fills the screen rather than vanishing."""
    assert preset_for(1).slot_for(5).is_full


@pytest.mark.parametrize(
    "value,expected", [(0.02, 0.0), (0.51, 0.5), (0.99, 1.0), (-1.0, 0.0), (2.0, 1.0)]
)
def test_snap_rounds_and_clamps(value, expected):
    assert snap(value) == pytest.approx(expected)


# -- Self healing ------------------------------------------------------------


def test_healed_leaves_a_good_layout_alone():
    layout, problems = preset_for(2).healed(2)
    assert len(layout.slots) == 2
    assert problems == []


def test_healed_rebuilds_when_the_slot_count_is_wrong():
    """Adding an app without touching the layout leaves it short."""
    layout, problems = preset_for(1).healed(3)
    assert len(layout.slots) == 3
    assert problems


def test_healed_grows_a_zero_sized_slot():
    """A slot dragged to nothing would launch a window with no area."""
    broken = Layout(slots=[Slot(0.0, 0.0, 0.0, 1.0), Slot(0.5, 0.0, 0.5, 1.0)])
    layout, problems = broken.healed(2)
    assert all(s.width > 0 and s.height > 0 for s in layout.slots)
    assert problems


def test_healed_pulls_a_slot_back_on_screen():
    off = Layout(slots=[Slot(0.9, 0.0, 0.5, 1.0), Slot(0.0, 0.0, 0.5, 1.0)])
    layout, problems = off.healed(2)
    assert all(s.x + s.width <= 1.0 + 1e-6 for s in layout.slots)
    assert all(s.y + s.height <= 1.0 + 1e-6 for s in layout.slots)
    assert problems


def test_healed_with_no_apps_is_empty():
    layout, problems = preset_for(3).healed(0)
    assert layout.slots == []
    assert problems == []


def test_a_healed_layout_still_yields_directions():
    """Healing has to produce something the tiling code can actually use."""
    layout, _ = Layout(slots=[Slot(0.0, 0.0, 0.0, 0.0)]).healed(3)
    assert len(split_directions(layout.slots)) == 2
