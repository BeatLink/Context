"""Layouts, and the split directions they translate into."""

from __future__ import annotations

import pytest

from context.layout import (
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
