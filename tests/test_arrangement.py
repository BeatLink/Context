"""A context spread across the screens it finds.

A context owns one workspace per screen, and stores an arrangement per screen
*count* — docked and undocked are different placements, both remembered.
"""

from __future__ import annotations

import pytest

from context.arrangement import Arrangement
from context.layout import Layout, Slot
from context.store import Context
from context.resources import Resource


def test_a_new_arrangement_is_one_screen():
    a = Arrangement()
    assert a.screen_count == 1
    assert a.screen_for(0) == 0


def test_spread_deals_windows_across_screens():
    """Someone who just plugged in a monitor wants their windows to use it."""
    a = Arrangement.spread(4, 2)
    assert a.indices_on(0) == [0, 2]
    assert a.indices_on(1) == [1, 3]
    assert len(a.screens) == 2


def test_spread_onto_one_screen_is_the_old_behaviour():
    a = Arrangement.spread(3, 1)
    assert a.indices_on(0) == [0, 1, 2]
    assert a.screen_count == 1


def test_a_window_with_no_assignment_lands_on_the_first_screen():
    a = Arrangement(screens=[Layout(), Layout()])
    assert a.screen_for(7) == 0


def test_healing_brings_back_windows_from_a_screen_that_is_gone():
    """Unplugging a monitor must not lose the windows that were on it."""
    a = Arrangement(screens=[Layout()], assignments={0: 0, 1: 1})
    healed, problems = a.healed(2)
    assert healed.screen_for(1) == 0
    assert any("screen that is gone" in p for p in problems)


def test_healing_gives_each_screen_slots_for_what_is_on_it():
    a = Arrangement(screens=[Layout(), Layout()], assignments={0: 0, 1: 1, 2: 1})
    healed, _ = a.healed(3)
    assert len(healed.layout_for(0).slots) == 1
    assert len(healed.layout_for(1).slots) == 2


def test_an_arrangement_round_trips():
    a = Arrangement.spread(3, 2)
    back = Arrangement.from_dict(a.to_dict())
    assert back.assignments == a.assignments
    assert len(back.screens) == len(a.screens)


def test_broken_assignments_are_skipped_not_fatal():
    back = Arrangement.from_dict({"screens": [], "assignments": {"x": "y", "0": 1}})
    assert back.assignments == {0: 1}


# -- on the context ----------------------------------------------------------


def test_a_context_keeps_an_arrangement_per_screen_count():
    """Docked and undocked are different placements, both remembered."""
    ctx = Context(title="work", resources=[Resource(app_id="a"), Resource(app_id="b")])
    ctx.set_arrangement(1, Arrangement.spread(2, 1))
    ctx.set_arrangement(2, Arrangement.spread(2, 2))

    assert ctx.arrangement_for(1).screen_count == 1
    assert ctx.arrangement_for(2).screen_count == 2
    # Unplugging goes back to the one-screen arrangement, not a rebuilt one.
    assert ctx.arrangement_for(1).indices_on(0) == [0, 1]


def test_more_screens_than_arranged_for_falls_back_to_the_nearest():
    """Plugging in a third monitor starts from the two-monitor layout."""
    ctx = Context(title="work", resources=[Resource(app_id="a")])
    ctx.set_arrangement(2, Arrangement.spread(1, 2))
    assert ctx.arrangement_for(3).screen_count == 2


def test_a_context_with_no_arrangements_uses_its_flat_layout():
    """Every context written before spanning existed."""
    ctx = Context(
        title="work",
        resources=[Resource(app_id="a"), Resource(app_id="b")],
        layout=Layout(slots=[Slot(0, 0, 0.5, 1), Slot(0.5, 0, 0.5, 1)]),
    )
    a = ctx.arrangement_for(1)
    assert a.screen_count == 1
    assert a.indices_on(0) == [0, 1]
    assert len(a.layout_for(0).slots) == 2


def test_handles_are_per_screen_and_primary_first():
    ctx = Context(title="work")
    ctx.set_handle("fake", "ctx-work")
    ctx.set_handle("fake", "ctx-work-s2", screen=1)
    assert ctx.handle_for("fake") == "ctx-work"
    assert ctx.handle_for("fake", screen=1) == "ctx-work-s2"
    assert ctx.handles_for("fake") == ["ctx-work", "ctx-work-s2"]


def test_handles_round_trip_through_the_store():
    ctx = Context(title="work")
    ctx.set_handle("fake", "ctx-work")
    ctx.set_handle("fake", "ctx-work-s2", screen=1)
    assert Context.from_dict(ctx.to_dict()).handles_for("fake") == [
        "ctx-work",
        "ctx-work-s2",
    ]


def test_dropping_handles_clears_every_screen():
    ctx = Context(title="work")
    ctx.set_handle("fake", "ctx-work")
    ctx.set_handle("fake", "ctx-work-s2", screen=1)
    ctx.drop_handles("fake")
    assert ctx.handles_for("fake") == []


def test_a_legacy_context_still_loads(tmp_path):
    """The shape written before any of this existed."""
    raw = {
        "title": "work",
        "apps": ["a.desktop", "b.desktop"],
        "workspaces": {"hyprland": "ctx-work"},
        "layout": [{"x": 0, "y": 0, "width": 1, "height": 1}],
    }
    ctx = Context.from_dict(raw)
    assert ctx.handles_for("hyprland") == ["ctx-work"]
    assert [r.app_id for r in ctx.resources] == ["a.desktop", "b.desktop"]
    assert ctx.arrangement_for(1).screen_count == 1
